"""KURE-v1 safetensors → ONNX(int8) 변환 (T7.5.1).

`jhgan/ko-sroberta-multitask`(경량)는 레포가 int8 ONNX를 직접 배포해 변환이
필요 없었지만(Phase 3), `nlpai-lab/KURE-v1`은 safetensors만 제공한다. 그래서
고성능 모드는 Phase 3부터 "준비 중"으로 막혀 있었고, 이 스크립트가 그것을 푼다.

## 🔴 이 스크립트는 런타임이 아니다

`torch`·`optimum`·`transformers`는 **여기서만** 쓰는 빌드타임 의존성이다.
Phase 3에서 torch(117MB)를 걷어내 런타임을 onnxruntime+tokenizers+numpy로
줄여둔 것을 되돌리면 안 된다(TECH 9.2 인스톨러 예산). 그래서 프로젝트
`.venv`가 아니라 **별도 `.venv-convert`**에서 돌린다:

    py -3.14 -m venv .venv-convert
    .venv-convert/Scripts/python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    .venv-convert/Scripts/python -m pip install "optimum[onnxruntime]" sentence-transformers truststore
    .venv-convert/Scripts/python -m scripts.convert_kure

변환이 끝나면 `.venv-convert`와 중간 산출물을 지운다(`--clean`).

## 🔴 사내망 TLS 검사 프록시 — `truststore`가 필요하다

이 환경에서 huggingface_hub 다운로드가 **SSL 인증서 검증 실패**로 막혔다
(`us.aws.cdn.hf.co` … `self-signed certificate in certificate chain`). 사내망이
HTTPS를 가로채 자체 CA로 재서명하기 때문인데, 갈리는 지점은 신뢰 저장소다:

  · `urllib`(표준 라이브러리) → **Windows 인증서 저장소** → 사내 CA 있음 → 성공
  · `requests`/huggingface_hub → **certifi 번들** → 사내 CA 없음 → 실패

그래서 이 저장소의 기존 다운로더(`slm/download.py`,
`indexer/vector/download.py`)는 urllib만 써서 GGUF 4.77GB까지 멀쩡히 받아왔다.
변환은 optimum/transformers가 내부에서 requests를 쓰므로 그 경로를 못 탄다 —
`truststore.inject_into_ssl()`로 requests도 OS 저장소를 보게 만든다.

## 산출물

    models/KURE-v1/model.onnx      int8 양자화 (약 570MB)
    models/KURE-v1/tokenizer.json  토크나이저
    models/KURE-v1/reference.npz   검증용 참조 벡터 (아래)

`reference.npz`는 **`sentence-transformers` 정식 추론으로 뽑은 참조 벡터**다.
변환 venv를 지운 뒤에도 프로젝트 venv에서 "우리 ONNX가 원본과 같은 벡터를
내는가"를 대조할 수 있어야 하기 때문에 함께 저장한다(T7.5.3).

## 알려진 함정

- **2GB protobuf 한계**: fp32 ONNX가 약 2.3GB라 단일 파일에 안 들어가
  `model.onnx` + `model.onnx_data`(external data)로 쪼개진다. 양자화 단계가
  이 구조를 읽을 수 있어야 하고, 결과물은 다시 단일 파일이어야 한다.
- **풀링은 CLS다**: KURE-v1의 `1_Pooling/config.json`은 `cls_token`만 켜져
  있다(경량 모델은 mean). 런타임 쪽 분기는 `config.settings.ModelProfile.pooling`
  에 있고, 이 스크립트는 그 값이 맞는지 참조 벡터로 검증만 한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import HEAVY  # noqa: E402

# 참조 벡터를 뽑을 문장들. 한국어 검색이 주 용도이므로 한국어 위주로 두되,
# 길이·형식이 서로 다른 것을 섞어 특정 길이에서만 맞는 변환을 걸러낸다.
REFERENCE_SENTENCES = [
    "계약서 검토 시 기준이 되는 조항은 손해배상과 계약 해지 조건이다.",
    "DNS는 도메인명을 분산된 트리 형태의 계층적 구조로 관리한다.",
    "짧은 문장.",
    "구분 | 최소 사양 | 권장 사양\nRAM | 8GB | 16GB",
    "IDLE is Python's Tkinter-based Integrated DeveLopment Environment.",
    "코치는 자신의 경력, 실적, 역량에 관하여 과대하게 선전하거나 광고하지 않습니다. "
    "이는 한국코치협회 윤리규정 제3조에 명시된 의무이며, 위반 시 자격이 정지될 수 있다.",
]

WORK_DIR = PROJECT_ROOT / "build" / "kure_convert"
REFERENCE_FILE = "reference.npz"


def _use_os_trust_store() -> None:
    """requests/huggingface_hub가 OS 인증서 저장소를 보게 만든다.

    사내망 TLS 검사 프록시 아래에서는 이게 없으면 모델 다운로드가 통째로
    막힌다(모듈 독스트링 참고). 없으면 경고만 하고 진행한다 — 프록시가 없는
    환경에서는 필요 없다.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        print("경고: truststore가 없습니다. 사내망 TLS 검사 프록시 환경이면 "
              "다운로드가 SSL 오류로 실패합니다 (`pip install truststore`).",
              file=sys.stderr)


def _require_build_deps() -> None:
    missing = []
    for module in ("torch", "optimum", "sentence_transformers"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        raise SystemExit(
            f"오류: 변환 전용 의존성이 없습니다: {', '.join(missing)}\n"
            "이 스크립트는 프로젝트 .venv가 아니라 .venv-convert에서 실행해야 합니다.\n"
            "  py -3.14 -m venv .venv-convert\n"
            "  .venv-convert/Scripts/python -m pip install torch "
            "--index-url https://download.pytorch.org/whl/cpu\n"
            '  .venv-convert/Scripts/python -m pip install "optimum[onnxruntime]" '
            "sentence-transformers truststore"
        )


def export_fp32(repo_id: str, out_dir: Path) -> Path:
    """safetensors → fp32 ONNX. 2GB를 넘으면 external data로 쪼개진다."""
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[1/4] fp32 ONNX 추출: {repo_id}")
    model = ORTModelForFeatureExtraction.from_pretrained(repo_id, export=True)
    model.save_pretrained(out_dir)

    # 토크나이저도 같은 자리에 — 런타임은 tokenizer.json 하나만 쓴다.
    AutoTokenizer.from_pretrained(repo_id).save_pretrained(out_dir)

    onnx_path = out_dir / "model.onnx"
    if not onnx_path.is_file():
        raise SystemExit(f"오류: fp32 ONNX가 생성되지 않았습니다: {onnx_path}")

    external = out_dir / "model.onnx_data"
    size = onnx_path.stat().st_size + (external.stat().st_size if external.is_file() else 0)
    print(f"      완료 — {size / 1e9:.2f} GB"
          f"{' (external data 분리됨)' if external.is_file() else ''}")
    return onnx_path


def quantize_int8(fp32_path: Path, dest: Path) -> Path:
    """동적 int8 양자화.

    경량 모델이 배포하는 것과 같은 **동적** 양자화를 쓴다 — 보정 데이터셋이
    필요 없고, Phase 3에서 이미 이 방식의 특성(배치·CPU에 따른 편차)을 측정해
    둬서 비교 기준이 있다.
    """
    from onnxruntime.quantization import QuantType, quantize_dynamic

    dest.parent.mkdir(parents=True, exist_ok=True)
    print("[2/4] int8 동적 양자화")
    quantize_dynamic(
        model_input=str(fp32_path),
        model_output=str(dest),
        weight_type=QuantType.QInt8,
    )
    print(f"      완료 — {dest.stat().st_size / 1e6:.0f} MB")
    return dest


def save_reference(repo_id: str, dest_dir: Path) -> Path:
    """`sentence-transformers` 정식 추론 결과를 참조 벡터로 저장한다 (T7.5.3).

    변환 venv를 지운 뒤에도 프로젝트 venv에서 대조할 수 있어야 하므로,
    문장과 벡터를 함께 파일로 남긴다.
    """
    import numpy as np
    from sentence_transformers import SentenceTransformer

    print("[3/4] 참조 벡터 생성 (sentence-transformers 정식 추론)")
    model = SentenceTransformer(repo_id)
    vectors = model.encode(
        REFERENCE_SENTENCES, normalize_embeddings=True, convert_to_numpy=True
    ).astype("float32")

    dest = dest_dir / REFERENCE_FILE
    np.savez(
        dest,
        sentences=np.array(REFERENCE_SENTENCES, dtype=object),
        vectors=vectors,
        meta=np.array([json.dumps({
            "repo_id": repo_id,
            "dim": int(vectors.shape[1]),
            "pooling": HEAVY.pooling,
            "max_seq_length": int(model.max_seq_length),
        }, ensure_ascii=False)], dtype=object),
    )
    print(f"      완료 — {vectors.shape[0]}문장 × {vectors.shape[1]}차원")
    return dest


def _copy_tokenizer(work_dir: Path, dest_dir: Path) -> None:
    src = work_dir / "tokenizer.json"
    if not src.is_file():
        raise SystemExit(f"오류: tokenizer.json이 없습니다: {src}")
    shutil.copy2(src, dest_dir / "tokenizer.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.convert_kure")
    parser.add_argument("--repo-id", default=HEAVY.repo_id)
    parser.add_argument("--work-dir", default=str(WORK_DIR),
                        help="fp32 중간 산출물 위치 (변환 후 --clean으로 삭제)")
    parser.add_argument("--clean", action="store_true",
                        help="변환 성공 후 fp32 중간 산출물을 지운다")
    parser.add_argument("--skip-reference", action="store_true",
                        help="참조 벡터 생성을 건너뛴다 (검증을 포기하는 것이므로 비권장)")
    args = parser.parse_args(argv)

    _use_os_trust_store()  # 사내망 프록시 대응 — import 전에 불러야 한다
    _require_build_deps()

    work_dir = Path(args.work_dir)
    dest_dir = HEAVY.local_dir
    dest_dir.mkdir(parents=True, exist_ok=True)

    fp32_path = export_fp32(args.repo_id, work_dir)
    quantize_int8(fp32_path, dest_dir / "model.onnx")

    if not args.skip_reference:
        save_reference(args.repo_id, dest_dir)

    _copy_tokenizer(work_dir, dest_dir)

    print("[4/4] 정리")
    if args.clean:
        shutil.rmtree(work_dir, ignore_errors=True)
        print(f"      중간 산출물 삭제: {work_dir}")
    else:
        print(f"      중간 산출물 유지: {work_dir} (--clean으로 삭제)")

    print()
    print(f"완료: {dest_dir}")
    print("검증: ./.venv/Scripts/python -m scripts.verify_kure")
    print("변환 venv는 이제 지워도 됩니다: rm -rf .venv-convert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

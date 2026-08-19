"""사양별 모델 프로파일과 검색 상수 (T3.7).

TECH 8장의 "설정 파일로 경량/권장 모드를 토글하는 단일 코드베이스" 요구를
구현한다. 모든 경로는 **프로젝트 루트 기준 상대 경로**로 계산해 TECH 9.1의
포터블 원칙(폴더 통째로 옮겨도 동작)을 지킨다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 이 파일 기준으로 프로젝트 루트를 잡는다 (절대 경로를 코드에 박지 않는다).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "models"

# 파서가 추출·캡처한 이미지를 모아두는 곳 (Phase 11-D).
#
# 예전에는 문서 폴더 옆에 `<문서폴더>/.assets/<파일명>/`으로 흩어져 저장됐다
# (`parser/base.py`의 옛 기본값) — 문서가 서브폴더 곳곳에 있으니 `.assets`도
# 곳곳에 생겼고, 지우려면 트리 전체를 뒤져야 했다. 게다가 **사용자의 원본
# 문서 폴더**에 쓰기를 하는 것이라 OneDrive·백업 도구가 이걸 같이 동기화하는
# 부작용도 있었다. 여기 한 곳으로 모아 `data/`(이미 통째로 .gitignore 대상)와
# 같은 운명이 되게 한다 — 지우려면 폴더 하나만 지우면 된다.
ASSETS_DIR = PROJECT_ROOT / "data" / "assets"

# 유사도 임계값 — 여러 곳에서 쓰이므로 여기 한 곳에서만 정의한다.
#   · DESIGN §5.6: 이 값 미만이면 결과 카드를 흐리게 + "관련성 낮음" 표시
#   · TECH 5.3 1단계 안전장치: Phase 7에서 이 값 미만이면 sLM을 호출하지 않음
SIMILARITY_THRESHOLD = 0.5

# 벡터 재순위에 넘길 FTS5 후보 개수. 크면 정확하지만 임베딩 조회 비용이 는다.
DEFAULT_CANDIDATE_LIMIT = 100


@dataclass(frozen=True)
class ModelProfile:
    """임베딩 모델 하나의 배포 단위."""

    key: str
    label: str
    repo_id: str
    onnx_file: str  # 레포 내 경로
    dim: int
    max_seq_length: int
    # 다운로드해야 하는 파일 목록 (레포 내 경로 → 로컬 저장 파일명)
    files: tuple[tuple[str, str], ...]
    # 🔴 문장 벡터를 만드는 방식. **모델마다 다르고, 틀리면 예외 없이 조용히
    # 나쁜 벡터가 나온다** — 레포의 `1_Pooling/config.json`을 그대로 따라야 한다.
    #   · "mean" — 토큰 벡터를 attention_mask 가중 평균 (ko-sroberta-multitask)
    #   · "cls"  — 첫 토큰([CLS])만 사용 (KURE-v1, bge-m3 계열)
    # Phase 3에서는 모델이 하나뿐이라 mean을 코드에 하드코딩했다가, Phase 7.5에서
    # KURE-v1이 CLS 풀링임을 확인하고 프로파일 속성으로 끌어올렸다.
    pooling: str = "mean"

    @property
    def local_dir(self) -> Path:
        return MODELS_DIR / self.key

    @property
    def onnx_path(self) -> Path:
        return self.local_dir / "model.onnx"

    @property
    def tokenizer_path(self) -> Path:
        return self.local_dir / "tokenizer.json"

    def is_installed(self) -> bool:
        return self.onnx_path.is_file() and self.tokenizer_path.is_file()


# 경량 모드 (최소 사양 8GB, 기본값)
# int8 양자화 ONNX를 레포가 이미 제공하므로 torch 없이 바로 추론할 수 있다.
LIGHT = ModelProfile(
    key="ko-sroberta-multitask",
    label="경량 모드 (최소 사양)",
    repo_id="jhgan/ko-sroberta-multitask",
    onnx_file="onnx/model_qint8_avx512_vnni.onnx",
    dim=768,
    max_seq_length=128,
    files=(
        ("onnx/model_qint8_avx512_vnni.onnx", "model.onnx"),
        ("tokenizer.json", "tokenizer.json"),
    ),
    pooling="mean",  # jhgan/ko-sroberta-multitask의 modules.json 기준
)

# 권장 모드 (권장 사양 16GB) — 이전 이름 "고성능 모드" [2026-08-11 이름 변경]
#
# KURE-v1은 ONNX를 제공하지 않는다(safetensors만) — Phase 7.5에서 우리가 직접
# 변환한다(`scripts/convert_kure.py`). 커뮤니티 재업로드본이 있지만 변환 품질을
# 확인할 근거가 없어 쓰지 않는다(PLAN Phase 7.5).
#
# **`files`는 다운로드용이 아니다.** 변환 산출물은 허깅페이스에 없으므로
# `download_profile()`로 받을 수 없고, sLM·LibreOffice와 같은 방침으로
# "인터넷 되는 PC에서 변환 → models/ 폴더째 복사"로 배포한다(TECH 9.1/9.3).
# 여기 남은 값은 변환 스크립트가 무엇을 만들어야 하는지 나타내는 표시일 뿐이다.
#
# 원본 `model.safetensors`는 **2.27GB**다(실측: Content-Range 2,271,064,456
# bytes). 568M 파라미터 × fp32 4바이트라 계산도 맞는다. 허깅페이스 API의
# `usedStorage`가 568MB로 나와 잠시 오기로 의심했으나, 그 값은 Xet 중복 제거
# 기준이라 실제 파일 크기가 아니다 — 기존 문서의 2.27GB가 맞다.
HEAVY = ModelProfile(
    key="KURE-v1",
    label="권장 모드 (권장 사양)",
    repo_id="nlpai-lab/KURE-v1",
    onnx_file="onnx/model_int8.onnx",
    dim=1024,
    # 레포의 sentence_bert_config.json 기준 8192. Phase 3에서 512로 적어둔 것은
    # 확인 없이 넣은 추정치였다(2026-08-10 정정).
    max_seq_length=8192,
    files=(
        ("onnx/model_int8.onnx", "model.onnx"),
        ("tokenizer.json", "tokenizer.json"),
    ),
    # 🔴 1_Pooling/config.json에서 pooling_mode_cls_token=true만 켜져 있다.
    # 경량 모델과 다르다 — mean으로 뽑으면 조용히 나쁜 벡터가 된다.
    pooling="cls",
)

PROFILES: dict[str, ModelProfile] = {LIGHT.key: LIGHT, HEAVY.key: HEAVY}

# UI 콤보박스(DESIGN §4.3)가 쓰는 표시용 순서
PROFILE_ORDER = (LIGHT.key, HEAVY.key)


# --- sLM (AI 요약용 소형 언어 모델) — Phase 6 후보, Phase 7 채택 -----------

SLM_DIR = MODELS_DIR / "slm"


@dataclass(frozen=True)
class SlmProfile:
    """sLM 후보 하나의 배포 단위 (GGUF 단일 파일)."""

    key: str
    label: str
    repo_id: str
    gguf_file: str  # 레포 내 경로
    size_gb: float  # HF에서 확인한 실제 크기 (표시·용량 계획용)
    note: str = ""
    # Qwen3 계열의 thinking 모드처럼 측정 전에 꺼야 하는 옵션이 있으면 여기에.
    extra_server_args: tuple[str, ...] = ()
    # 다운로드 안내 팝업에 띄우고 "새로고침(파일 확인)"이 대조할 체크섬 (TECH 9.3).
    # **제품에 노출하는 모델만 채운다** — 이 저장소가 실제로 받아본 파일에서 계산한
    # 값이라, 받아본 적 없는 후보에 추측값을 적을 수는 없다. 빈 값이면 검증을
    # 건너뛰고 크기 검사만 한다.
    sha256: str = ""
    # 파일 크기(바이트). sha256과 함께 실제 파일에서 기록한다 — size_gb는 표시용
    # 반올림값이라 검증에 쓸 수 없다.
    size_bytes: int = 0

    @property
    def local_path(self) -> Path:
        return SLM_DIR / f"{self.key}.gguf"

    def is_installed(self) -> bool:
        return self.local_path.is_file()

    @property
    def download_url(self) -> str:
        """다운로드 안내 팝업에 그대로 띄울 URL (slm/download.py와 같은 규칙)."""
        return f"https://huggingface.co/{self.repo_id}/resolve/main/{self.gguf_file}"


# 후보 4종 — 2026-08 시점에 HF에서 실제 파일 크기를 확인한 것들이다.
# TASK 문서 원안(Qwen2.5-1.5B/7B, EXAONE-3.5, Phi-3.5-mini)은 2024년 기준이라
# 갱신했다. 특히 EXAONE-4.0-1.2B(0.81GB)는 문서 작성 시점에 없던 선택지로,
# 8GB 최소 사양에서 한국어 특화 모델을 쓸 수 있는지를 바꿔놓는다.
SLM_CANDIDATES: tuple[SlmProfile, ...] = (
    SlmProfile(
        key="exaone-4.0-1.2b",
        label="EXAONE 4.0 1.2B",
        repo_id="LGAI-EXAONE/EXAONE-4.0-1.2B-GGUF",
        gguf_file="EXAONE-4.0-1.2B-Q4_K_M.gguf",
        size_gb=0.81,
        note="LG AI Research 공식 GGUF, 한국어 특화. 최소 사양 채택 모델",
        sha256="7b5e753540183ae4d56e6febd9b48cdd944de53386e6faa8f51c8f98cb2b47df",
        size_bytes=812_437_792,
    ),
    SlmProfile(
        key="phi-4-mini",
        label="Phi-4-mini-instruct",
        repo_id="unsloth/Phi-4-mini-instruct-GGUF",
        gguf_file="Phi-4-mini-instruct-Q4_K_M.gguf",
        size_gb=2.49,
        note="Phi-3.5-mini 후속. 크기 대비 지시 준수가 강점으로 알려짐",
    ),
    SlmProfile(
        key="qwen3.5-4b",
        label="Qwen3.5 4B",
        repo_id="unsloth/Qwen3.5-4B-GGUF",
        gguf_file="Qwen3.5-4B-Q4_K_M.gguf",
        size_gb=2.74,
        note="Apache 2.0. thinking 모드가 기본 활성이라 --reasoning off로 끈다",
        # 실측: 끄지 않으면 300토큰을 전부 사고에 쓰고 130초 만에 **빈 응답**을
        # 돌려준다. `--reasoning-budget 0`은 효과가 없었고 `--reasoning off`만
        # 통했다(템플릿에 빈 <think></think>가 삽입되며 8토큰으로 정상 응답).
        extra_server_args=("--reasoning", "off"),
        sha256="00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
        size_bytes=2_740_937_888,
    ),
    SlmProfile(
        key="exaone-3.5-7.8b",
        label="EXAONE 3.5 7.8B",
        repo_id="lmstudio-community/EXAONE-3.5-7.8B-Instruct-GGUF",
        gguf_file="EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf",
        size_gb=4.77,
        note="한국어 특화. Phase 6 측정 대상이었으나 미채택",
    ),
)

SLM_PROFILES: dict[str, SlmProfile] = {p.key: p for p in SLM_CANDIDATES}
SLM_ORDER = tuple(p.key for p in SLM_CANDIDATES)


# --- 제품에 노출하는 sLM (Phase 7 채택 결과) -------------------------------
# 위 `SLM_CANDIDATES` 4종은 **Phase 6 측정 하네스가 계속 쓴다**
# (`scripts/benchmark_slm.py --models`). 반면 제품 UI(모델 관리 화면)가 보여줄
# 것은 실제로 채택된 2종뿐이다:
#
#   · qwen3.5-4b       권장 사양 채택 [사용자 확정, 2026-08-10]
#   · exaone-4.0-1.2b  최소 사양에서 켠다면 이것 (PLAN §6-B)
#
# EXAONE-3.5-7.8B는 권장 사양 측정에서 속도는 앞섰지만 메모리가 8.3GB로 두 배라
# 탈락했다 — 노출하면 "동시 작업 중 PC가 느려지지 않아야 한다"는 채택 근거와
# 어긋나는 선택지를 다시 권하는 꼴이 된다. Phi-4-mini는 준수율 문제로 전 사양
# 제외(지어낸 답에 근거 번호까지 붙인다).
SLM_RECOMMENDED = "qwen3.5-4b"   # 권장 사양(16GB)
SLM_MINIMUM = "exaone-4.0-1.2b"  # 최소 사양(8GB)
SLM_OFFERED_ORDER = (SLM_RECOMMENDED, SLM_MINIMUM)
# 목록 순서는 `SLM_OFFERED_ORDER`를 따른다 — 권장 사양을 먼저 보여줘야
# 사용자가 기본으로 고를 것이 위에 온다(`SLM_CANDIDATES` 순서는 측정 순서라
# 최소 사양 모델이 앞에 있다).
SLM_OFFERED: tuple[SlmProfile, ...] = tuple(
    SLM_PROFILES[key] for key in SLM_OFFERED_ORDER
)

# 기본값은 권장 사양 모델. 최소 사양에서는 sLM 요약 자체가 기본 OFF이므로
# (TECH 8장, Phase 6 결론) 여기서 최소 사양을 기본으로 둘 이유가 없다.
DEFAULT_SLM_PROFILE = SLM_RECOMMENDED

# 유휴 상태가 이만큼 이어지면 llama-server를 내려 메모리를 돌려준다.
# 채택 모델이 4.8GB를 쓰는데 16GB PC에서 다른 작업(안드로이드 스튜디오 빌드
# 등)과 동시에 돌아가는 것이 전제라, 안 쓰는 동안 물고 있으면 안 된다
# [사용자 확정, 2026-08-10]. 재기동 비용은 4.7초(실측)라 감당 가능하다.
SLM_IDLE_TIMEOUT_SEC = 300

# 4단계 안전장치 — 답변 문장이 근거 발췌와 이만큼도 안 겹치면 "확인 필요".
# [제안] 실제 답변으로 조정 여지가 있는 값이다.
SLM_OVERLAP_THRESHOLD = 0.6


def get_slm_profile(key: str) -> SlmProfile:
    if key not in SLM_PROFILES:
        valid = ", ".join(SLM_ORDER)
        raise ValueError(f"알 수 없는 sLM 프로파일: {key} (사용 가능: {valid})")
    return SLM_PROFILES[key]

_ENV_PROFILE = "RAG_MODEL_PROFILE"


def get_profile(key: str | None = None) -> ModelProfile:
    """활성 모델 프로파일을 반환한다.

    우선순위: 인자 > 환경변수(RAG_MODEL_PROFILE) > 경량 모드(기본값).
    최소 사양을 기본으로 두는 것이 안전하다.
    """
    resolved = key or os.environ.get(_ENV_PROFILE) or LIGHT.key
    if resolved not in PROFILES:
        valid = ", ".join(PROFILES)
        raise ValueError(f"알 수 없는 모델 프로파일: {resolved} (사용 가능: {valid})")
    return PROFILES[resolved]

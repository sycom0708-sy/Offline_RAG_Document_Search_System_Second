"""변환된 KURE-v1 검증 (T7.5.3, T7.5.4).

**프로젝트 `.venv`에서 돌린다** — torch 없이, 실제 런타임과 똑같은 경로
(`indexer.vector.embedder.Embedder`)로 벡터를 뽑아 대조한다. 변환 스크립트가
쓰는 `.venv-convert`에서 검증하면 "런타임에서도 같은 값이 나오는가"를 확인할
수 없어 의미가 없다.

    ./.venv/Scripts/python -m scripts.verify_kure

검증 항목:

1. **변환 정확성** — 우리 ONNX 벡터 vs `sentence-transformers` 참조 벡터
   (`models/KURE-v1/reference.npz`, 변환 시 생성)의 코사인 유사도.
   풀링을 틀렸다면 여기서 크게 떨어진다.
2. **풀링 방식 교차 확인** — CLS로 뽑은 것과 mean으로 뽑은 것 중 어느 쪽이
   참조에 가까운지 직접 비교한다. 프로파일 설정이 맞다는 것을 수치로 남긴다.
3. **양자화 재현성** — 배치 vs 단건 자기 유사도(Phase 3에서 경량 모델로 측정한
   것과 같은 방식). int8 동적 양자화는 배치 단위로 활성값 스케일을 잡아 같은
   문장도 배치 구성에 따라 벡터가 달라진다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from config.settings import HEAVY
from indexer.vector.embedder import Embedder, pool_and_normalize

REFERENCE_FILE = "reference.npz"


def _load_reference(profile) -> tuple[list[str], np.ndarray, dict]:
    path = profile.local_dir / REFERENCE_FILE
    if not path.is_file():
        raise SystemExit(
            f"오류: 참조 벡터가 없습니다: {path}\n"
            "`.venv-convert/Scripts/python -m scripts.convert_kure`로 변환하면 함께 생성됩니다."
        )
    data = np.load(path, allow_pickle=True)
    sentences = [str(s) for s in data["sentences"]]
    vectors = data["vectors"].astype(np.float32)
    meta = json.loads(str(data["meta"][0])) if "meta" in data else {}
    return sentences, vectors, meta


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """행별 코사인. 양쪽 모두 L2 정규화돼 있다고 가정하지 않는다."""
    a_n = a / np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-12, None)
    b_n = b / np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-12, None)
    return (a_n * b_n).sum(axis=1)


def _raw_hidden(embedder: Embedder, texts: list[str]):
    """풀링 전 `last_hidden_state`와 attention_mask를 그대로 얻는다.

    풀링 방식을 바꿔가며 비교하려면 추론 결과를 재사용해야 한다 — 두 번
    추론하면 int8 양자화 편차가 섞여 비교가 흐려진다.
    """
    embedder._ensure_loaded()
    encodings = embedder._tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    (hidden,) = embedder._session.run(
        ["last_hidden_state"],
        {"input_ids": input_ids, "attention_mask": attention_mask},
    )
    return hidden, attention_mask


def check_accuracy(embedder: Embedder, sentences: list[str], reference: np.ndarray) -> float:
    print("[1] 변환 정확성 — 우리 ONNX vs sentence-transformers 참조")
    ours = embedder.encode(sentences, batch_size=len(sentences))
    if ours.shape != reference.shape:
        raise SystemExit(
            f"오류: 차원이 다릅니다 — 우리 {ours.shape} vs 참조 {reference.shape}"
        )
    sims = _cosine(ours, reference)
    for sentence, sim in zip(sentences, sims):
        mark = "✔" if sim >= 0.99 else ("~" if sim >= 0.95 else "✘")
        print(f"    {mark} {sim:.4f}  {sentence[:44]}")
    print(f"    최소 {sims.min():.4f} / 중앙 {np.median(sims):.4f} / 평균 {sims.mean():.4f}")
    return float(sims.min())


def check_pooling(embedder: Embedder, sentences: list[str], reference: np.ndarray) -> str:
    """CLS와 mean 중 참조에 가까운 쪽을 실측으로 가린다."""
    print()
    print("[2] 풀링 방식 교차 확인 — 어느 쪽이 참조와 맞는가")
    hidden, mask = _raw_hidden(embedder, sentences)

    scores = {}
    for pooling in ("cls", "mean"):
        vectors = pool_and_normalize(hidden, mask, pooling)
        scores[pooling] = float(np.median(_cosine(vectors, reference)))
        flag = " ← 프로파일 설정" if pooling == embedder.profile.pooling else ""
        print(f"    {pooling:5} 중앙 코사인 {scores[pooling]:.4f}{flag}")

    winner = max(scores, key=scores.get)
    if winner != embedder.profile.pooling:
        print(f"    🔴 프로파일은 '{embedder.profile.pooling}'인데 '{winner}'가 더 맞습니다 "
              "— config/settings.py를 고쳐야 합니다.")
    else:
        print(f"    ✔ 프로파일 설정('{embedder.profile.pooling}')이 실측과 일치합니다.")
    return winner


def check_quantization_variance(embedder: Embedder, sentences: list[str]) -> float:
    """배치로 만든 벡터와 단건으로 만든 벡터가 얼마나 갈리는가 (Phase 3과 같은 측정).

    저장은 배치, 질의는 단건으로 만들기 때문에 이 편차가 곧 재순위 품질에
    영향을 준다.
    """
    print()
    print("[3] 양자화 재현성 — 배치 vs 단건 자기 유사도")
    batched = embedder.encode(sentences, batch_size=len(sentences))
    single = np.vstack([embedder.encode([s], batch_size=1) for s in sentences])
    sims = _cosine(batched, single)
    print(f"    최소 {sims.min():.4f} / 중앙 {np.median(sims):.4f}")
    print("    (Phase 3 경량 모델 실측: 이 PC 0.98대, 최소 사양 PC 중앙 0.940·최소 0.868)")
    return float(sims.min())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.verify_kure")
    parser.add_argument("--min-cosine", type=float, default=0.95,
                        help="변환 정확성 합격선 (int8 열화를 감안한 값)")
    args = parser.parse_args(argv)

    profile = HEAVY
    if not profile.is_installed():
        print(f"오류: {profile.label} 모델이 없습니다: {profile.local_dir}\n"
              ".venv-convert에서 `python -m scripts.convert_kure`를 먼저 실행하세요.",
              file=sys.stderr)
        return 1

    sentences, reference, meta = _load_reference(profile)
    print("=" * 78)
    print(f"KURE-v1 변환 검증 — {profile.local_dir}")
    print(f"참조: {meta.get('repo_id', '?')} / dim={meta.get('dim')} "
          f"/ pooling={meta.get('pooling')} / max_seq={meta.get('max_seq_length')}")
    print("=" * 78)

    embedder = Embedder(profile)
    worst = check_accuracy(embedder, sentences, reference)
    winner = check_pooling(embedder, sentences, reference)
    variance = check_quantization_variance(embedder, sentences)

    print()
    print("=" * 78)
    ok = worst >= args.min_cosine and winner == profile.pooling
    print(f"판정: {'통과' if ok else '실패'} "
          f"(정확성 최소 {worst:.4f} / 합격선 {args.min_cosine} · 풀링 {winner})")
    print(f"양자화 편차 최소 자기 유사도 {variance:.4f}")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

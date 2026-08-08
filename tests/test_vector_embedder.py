"""임베더 테스트 (T3.1, T3.2).

풀링 수식처럼 모델 없이 검증할 수 있는 부분은 항상 돌리고, 실제 추론은
모델이 있을 때만 돌린다.
"""

from __future__ import annotations

import numpy as np
import pytest

from indexer.vector.embedder import (
    ModelNotInstalledError,
    _mean_pool_and_normalize,
    blob_to_vector,
    vector_to_blob,
)


# --- 모델 없이 검증 가능한 부분 -------------------------------------


def test_mean_pool_ignores_padding_tokens():
    """패딩까지 평균에 넣으면 짧은 문장의 벡터가 0쪽으로 끌려간다."""
    hidden = np.array([[[2.0, 4.0], [4.0, 8.0], [99.0, 99.0]]], dtype=np.float32)
    mask = np.array([[1, 1, 0]], dtype=np.int64)  # 세 번째는 패딩

    pooled = _mean_pool_and_normalize(hidden, mask)

    # 패딩을 뺀 평균은 (3, 6) → 정규화하면 방향만 남는다.
    expected = np.array([3.0, 6.0])
    expected = expected / np.linalg.norm(expected)
    assert np.allclose(pooled[0], expected, atol=1e-6)


def test_mean_pool_output_is_l2_normalized():
    rng = np.random.default_rng(0)
    hidden = rng.normal(size=(4, 7, 16)).astype(np.float32)
    mask = np.ones((4, 7), dtype=np.int64)

    pooled = _mean_pool_and_normalize(hidden, mask)

    assert np.allclose(np.linalg.norm(pooled, axis=1), 1.0, atol=1e-5)


def test_mean_pool_handles_all_padding_without_dividing_by_zero():
    hidden = np.ones((1, 3, 4), dtype=np.float32)
    mask = np.zeros((1, 3), dtype=np.int64)

    pooled = _mean_pool_and_normalize(hidden, mask)

    assert np.isfinite(pooled).all()


def test_blob_round_trip_is_lossless():
    vector = np.array([0.5, -0.25, 0.125], dtype=np.float32)
    assert np.array_equal(blob_to_vector(vector_to_blob(vector)), vector)


def test_blob_is_float32_little_endian():
    vector = np.ones(4, dtype=np.float32)
    assert len(vector_to_blob(vector)) == 4 * 4


def test_missing_model_raises_actionable_error(tmp_path):
    from dataclasses import replace

    from config.settings import LIGHT
    from indexer.vector.embedder import Embedder

    missing = replace(LIGHT, key="존재하지-않는-모델")
    with pytest.raises(ModelNotInstalledError) as excinfo:
        Embedder(missing).encode_one("아무 텍스트")

    message = str(excinfo.value)
    assert "download" in message  # 무엇을 해야 하는지 알려줘야 한다


# --- 실제 모델이 있을 때만 --------------------------------------------


def test_encode_shape_and_normalization(embedder):
    vectors = embedder.encode(["첫 번째 문장이다", "두 번째 문장이다"])
    assert vectors.shape == (2, embedder.dim)
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


def test_encode_one_returns_single_vector(embedder):
    assert embedder.encode_one("문장 하나").shape == (embedder.dim,)


def test_encode_empty_list_returns_empty_array(embedder):
    assert embedder.encode([]).shape == (0, embedder.dim)


def test_similar_sentences_score_higher_than_unrelated(embedder):
    """임베딩이 의미를 담고 있는지 — 이게 깨지면 재순위가 무의미해진다."""
    base = embedder.encode_one("계약서 검토 시 기준이 되는 조항은 손해배상과 계약 해지다")
    similar = embedder.encode_one("계약 검토할 때 어떤 항목을 확인해야 하나요")
    unrelated = embedder.encode_one("오늘 점심 메뉴는 김치찌개입니다")

    assert float(base @ similar) > float(base @ unrelated)
    assert float(base @ similar) > 0.4


def test_identical_text_scores_near_one(embedder):
    text = "동일한 문장은 유사도가 1에 가까워야 한다"
    a, b = embedder.encode([text, text])
    assert float(a @ b) == pytest.approx(1.0, abs=1e-4)


def test_same_path_encoding_is_deterministic(embedder):
    """같은 입력을 같은 경로로 넣으면 **비트 단위로 같아야** 한다.

    양자화 편차는 배치 구성에서 오지, 실행마다 흔들리는 것이 아니다. 이게
    깨지면 아래 편차 테스트의 전제 자체가 무너진다.
    """
    text = "계약서 검토 시 기준이 되는 조항"
    assert np.array_equal(embedder.encode([text]), embedder.encode([text]))


def test_batching_deviation_stays_within_known_bounds(embedder):
    """배치로 만든 벡터와 단건 벡터의 편차가 알려진 범위 안인지 (회귀 감지용).

    **이 값은 CPU에 따라 크게 다르다 — 품질 기준으로 읽으면 안 된다.**
    int8 동적 양자화라 활성값 스케일을 배치 텐서 전체로 잡기 때문에, 같은
    문장이라도 어떤 배치에 실렸느냐로 벡터가 달라진다. 패딩 탓이 아니다 —
    패딩이 0인 문장도 갈리고, 패딩을 늘리면 오히려 편차가 줄었다(실측).

    실측 하한: AVX-512/VNNI가 있는 Ultra 5 125U에서는 0.98대, VNNI가 없는
    i5-8265U(최소 사양 기준기)에서는 실문서 300청크 기준 최소 0.868이다.
    그래서 하한을 0.85로 두되, **품질 보증은 아래 순위 테스트가 한다** —
    실제로 중요한 성질은 "순위가 뒤집히지 않는가"이고, 실문서 300청크·질의
    4건에서 top1은 전부 동일, top10 겹침은 8~10/10이었다(PLAN Phase 3 참고).
    """
    texts = ["짧은 문장", "이것은 조금 더 긴 문장이며 토큰 수가 다르다", "중간 길이 문장이다"]

    batched = embedder.encode(texts, batch_size=8)
    one_by_one = np.vstack([embedder.encode([t]) for t in texts])

    self_similarity = [float(batched[i] @ one_by_one[i]) for i in range(len(texts))]
    assert min(self_similarity) > 0.85


def test_batching_does_not_change_ranking(embedder):
    """양자화 노이즈가 검색 순위를 바꾸면 안 된다 — 이게 실제로 중요한 성질이다."""
    docs = [
        "계약서 검토 시 기준이 되는 조항은 손해배상, 계약 해지, 지급 조건이다",
        "계약 담당자는 매월 말일까지 실적을 보고한다",
        "오늘 점심 메뉴는 김치찌개입니다",
    ]
    query = embedder.encode_one("계약서 검토 기준이 뭐였지")

    rankings = []
    for batch_size in (1, 2, 8):
        vectors = embedder.encode(docs, batch_size=batch_size)
        scores = [float(query @ v) for v in vectors]
        rankings.append(sorted(range(len(docs)), key=lambda i: scores[i], reverse=True))

    assert rankings[0] == rankings[1] == rankings[2]


def test_count_tokens_reflects_text_length(embedder):
    assert embedder.count_tokens("짧다") < embedder.count_tokens("이 문장은 훨씬 더 길고 토큰이 많다" * 3)


def test_long_text_is_truncated_to_model_limit(embedder):
    """모델 한계를 넘는 입력도 예외 없이 처리돼야 한다(잘림은 청커가 예방)."""
    vector = embedder.encode_one("가나다라마바사" * 500)
    assert vector.shape == (embedder.dim,)
    assert np.isfinite(vector).all()

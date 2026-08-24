"""벡터 저장소 테스트 (T3.3, T3.4)."""

from __future__ import annotations

import numpy as np
import pytest

from indexer.fts5.schema import connect
from indexer.fts5.store import store_document
from indexer.vector.store import (
    embed_missing,
    fetch_vectors,
    missing_chunk_ids,
    missing_vector_count,
    store_vectors,
    vector_stats,
)
from parser.schema import Chunk, ChunkType, ParsedDocument

MODEL = "test-model"


def _document(doc_id: str = "d1", count: int = 3) -> ParsedDocument:
    document = ParsedDocument(doc_id=doc_id, file_path="x", file_name="x.txt", title="t")
    document.chunks = [
        Chunk(
            chunk_id=f"{doc_id}_c{i}",
            doc_id=doc_id,
            file_path="x",
            file_name="x.txt",
            type=ChunkType.TEXT,
            page_or_slide=i,
            content=f"{i}번째 문장이다",
        )
        for i in range(count)
    ]
    return document


@pytest.fixture
def db():
    conn = connect(":memory:")
    store_document(conn, _document())
    yield conn
    conn.close()


def _vectors(n: int, dim: int = 4) -> np.ndarray:
    rng = np.random.default_rng(0)
    v = rng.normal(size=(n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_store_and_fetch_round_trip(db):
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks ORDER BY id")]
    vectors = _vectors(len(ids))

    store_vectors(db, ids, vectors, MODEL)
    fetched = fetch_vectors(db, ids, MODEL)

    assert set(fetched) == set(ids)
    for chunk_id, vector in zip(ids, vectors):
        assert np.allclose(fetched[chunk_id], vector, atol=1e-6)


def test_fetch_ignores_other_model_vectors(db):
    """모델이 다르면 차원·의미 공간이 달라 섞이면 안 된다."""
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]
    store_vectors(db, ids, _vectors(len(ids)), MODEL)

    assert fetch_vectors(db, ids, "다른-모델") == {}


def test_store_replaces_existing_vector(db):
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks LIMIT 1")]
    store_vectors(db, ids, _vectors(1), MODEL)
    store_vectors(db, ids, _vectors(1) * -1, MODEL)

    assert db.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == 1


def test_store_rejects_length_mismatch(db):
    with pytest.raises(ValueError, match="개수 불일치"):
        store_vectors(db, ["a", "b"], _vectors(1), MODEL)


def test_fetch_empty_ids_returns_empty(db):
    assert fetch_vectors(db, [], MODEL) == {}


def test_fetch_handles_more_ids_than_sql_variable_limit(db):
    """IN 절 변수 한도(999)를 넘겨도 나눠서 조회해야 한다."""
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]
    store_vectors(db, ids, _vectors(len(ids)), MODEL)

    padded = [*ids, *[f"없는-청크-{i}" for i in range(1500)]]
    fetched = fetch_vectors(db, padded, MODEL)

    assert set(fetched) == set(ids)


def test_missing_chunk_ids_lists_unembedded(db):
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks ORDER BY id")]
    assert missing_chunk_ids(db, MODEL) == ids

    store_vectors(db, ids[:1], _vectors(1), MODEL)
    assert missing_chunk_ids(db, MODEL) == ids[1:]


def test_missing_vector_count_matches_the_id_list(db):
    """T10.26 — UI가 "이 모드로 검색이 되는 상태인가"만 볼 때 쓰는 개수 질의."""
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks ORDER BY id")]
    assert missing_vector_count(db, MODEL) == len(ids)

    store_vectors(db, ids[:1], _vectors(1), MODEL)
    assert missing_vector_count(db, MODEL) == len(ids) - 1

    store_vectors(db, ids[1:], _vectors(len(ids) - 1), MODEL)
    assert missing_vector_count(db, MODEL) == 0


def test_missing_vector_count_is_model_specific(db):
    """🔴 모드를 바꾸면 벡터가 0개인 상태가 된다 — 이걸 못 알아채면 검색 결과의
    유사도가 전부 None이 되고 AI 요약이 통째로 막힌다(실사용 보고, 2026-08-18)."""
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]
    store_vectors(db, ids, _vectors(len(ids)), MODEL)

    assert missing_vector_count(db, MODEL) == 0
    assert missing_vector_count(db, "다른-모델") == len(ids)


def test_missing_is_model_specific(db):
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]
    store_vectors(db, ids, _vectors(len(ids)), MODEL)

    # 모델을 바꾸면 전부 다시 만들어야 한다.
    assert len(missing_chunk_ids(db, "새-모델")) == len(ids)


def test_deleting_document_removes_vectors(db):
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]
    store_vectors(db, ids, _vectors(len(ids)), MODEL)

    db.execute("DELETE FROM documents")
    db.commit()

    assert db.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == 0


def test_vector_stats_counts_by_model(db):
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]
    store_vectors(db, ids, _vectors(len(ids)), MODEL)

    assert vector_stats(db) == {MODEL: len(ids)}


def test_two_models_coexist_for_the_same_chunk(db):
    """🔴 Phase 7.5 회귀 방지 — 이게 원래 깨져 있었다.

    `chunk_vectors`의 기본키가 `chunk_id` 단독이던 시절엔, 같은 청크를 다른
    모델로 다시 임베딩하면(=PC 성능 모드 전환) INSERT OR REPLACE가 **이전
    모델의 벡터를 지우고 덮어썼다.** Phase 3가 설계한 "모델별 벡터 공존"이
    스키마 수준에서 애초에 성립하지 않았던 것 — LIGHT 하나만 실사용된
    Phase 3~7 동안은 드러나지 않다가, Phase 7.5에서 KURE-v1으로 실제
    재인덱싱을 하면서 처음 발견됐다(LIGHT 벡터 522개가 통째로 사라짐).
    """
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]

    store_vectors(db, ids, _vectors(len(ids)), "경량-모델")
    store_vectors(db, ids, _vectors(len(ids), dim=1024), "권장-모델")

    assert vector_stats(db) == {"경량-모델": len(ids), "권장-모델": len(ids)}
    assert fetch_vectors(db, ids, "경량-모델") != {}
    assert fetch_vectors(db, ids, "권장-모델") != {}


def test_switching_model_does_not_require_full_reindex(db):
    """모드를 한 번 전환한 뒤 되돌아가면, 전환 전 모델은 다시 embed할 필요가 없어야 한다."""
    ids = [r["chunk_id"] for r in db.execute("SELECT chunk_id FROM chunks")]
    store_vectors(db, ids, _vectors(len(ids)), "경량-모델")

    store_vectors(db, ids, _vectors(len(ids), dim=1024), "권장-모델")

    # 경량 모드로 돌아가도 이미 만들어둔 벡터가 살아 있어야 한다.
    assert missing_chunk_ids(db, "경량-모델") == []


# --- 실제 모델 필요 --------------------------------------------------


def test_embed_missing_fills_all_chunks(db, embedder):
    processed = embed_missing(db, embedder)

    total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert processed == total
    assert vector_stats(db) == {embedder.profile.key: total}


def test_embed_missing_is_idempotent(db, embedder):
    embed_missing(db, embedder)
    assert embed_missing(db, embedder) == 0


def test_embed_missing_reports_progress(db, embedder):
    calls = []
    embed_missing(db, embedder, batch_size=1, on_progress=lambda d, t: calls.append((d, t)))

    total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert calls == [(i + 1, total) for i in range(total)]


def test_embed_missing_only_processes_new_chunks(db, embedder):
    embed_missing(db, embedder)
    store_document(db, _document("d2", count=2))

    assert embed_missing(db, embedder) == 2


def test_embed_missing_stops_early_when_stop_event_set(db, embedder):
    """T10.48 — 취소 버튼을 눌러도 임베딩 단계는 수천 개를 다 처리할 때까지
    멈추지 않던 문제(실사용 보고). 다음 배치를 시작하기 전에 확인해야 한다.
    """
    import threading

    total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert total >= 2  # `db` 픽스처는 3개 — 최소 2개는 있어야 "일부만 처리"가 성립

    stop_event = threading.Event()
    processed = embed_missing(
        db, embedder, batch_size=1,
        on_progress=lambda done, t: stop_event.set(),
        stop_event=stop_event,
    )

    assert processed == 1

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

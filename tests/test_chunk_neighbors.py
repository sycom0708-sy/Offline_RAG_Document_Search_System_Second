"""인접 청크 조회 테스트 (T10.21)."""

from __future__ import annotations

from indexer.fts5.schema import connect
from indexer.fts5.store import store_document
from parser.schema import Chunk, ChunkType, ParsedDocument, TableData
from search.chunk_neighbors import fetch_next_chunk


def _build_db(conn):
    document = ParsedDocument(doc_id="d1", file_path="x.doc", file_name="안내.doc", title="안내")
    document.chunks = [
        Chunk(
            chunk_id="d1_text_0",
            doc_id="d1",
            file_path="x.doc",
            file_name="안내.doc",
            type=ChunkType.TEXT,
            page_or_slide=None,
            content="-응시방법-",
        ),
        Chunk(
            chunk_id="d1_table_0",
            doc_id="d1",
            file_path="x.doc",
            file_name="안내.doc",
            type=ChunkType.TABLE,
            page_or_slide=None,
            content="1단계 서류전형\n2단계 필기전형",
            table=TableData(rows=[["1단계", "서류전형"]]),
        ),
        Chunk(
            chunk_id="d1_text_1",
            doc_id="d1",
            file_path="x.doc",
            file_name="안내.doc",
            type=ChunkType.TEXT,
            page_or_slide=None,
            content="마지막 문단입니다",
        ),
    ]
    store_document(conn, document)

    other = ParsedDocument(doc_id="d2", file_path="y.doc", file_name="다른문서.doc", title="다른문서")
    other.chunks = [
        Chunk(
            chunk_id="d2_text_0",
            doc_id="d2",
            file_path="y.doc",
            file_name="다른문서.doc",
            type=ChunkType.TEXT,
            page_or_slide=None,
            content="다른 문서의 첫 청크",
        ),
    ]
    store_document(conn, other)


def test_returns_the_next_chunk_in_the_same_document(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    conn = connect(db_path)
    _build_db(conn)

    next_chunk = fetch_next_chunk(conn, "d1_text_0")

    assert next_chunk is not None
    assert next_chunk.chunk_id == "d1_table_0"
    assert next_chunk.type is ChunkType.TABLE
    assert "서류전형" in next_chunk.content
    conn.close()


def test_returns_none_for_the_last_chunk_in_a_document(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    conn = connect(db_path)
    _build_db(conn)

    assert fetch_next_chunk(conn, "d1_text_1") is None
    conn.close()


def test_does_not_cross_into_a_different_document(tmp_path):
    """d1의 마지막 청크 다음에 d2의 첫 청크가 삽입 순서상 이어지더라도
    문서 경계를 넘어가면 안 된다 — 이미 위 테스트가 이걸 보장하지만,
    명시적으로 doc_id 경계를 검증한다."""
    db_path = tmp_path / "index.sqlite3"
    conn = connect(db_path)
    _build_db(conn)

    next_chunk = fetch_next_chunk(conn, "d1_table_0")

    assert next_chunk is not None
    assert next_chunk.doc_id == "d1"
    assert next_chunk.chunk_id == "d1_text_1"
    conn.close()


def test_returns_none_for_unknown_chunk_id(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    conn = connect(db_path)
    _build_db(conn)

    assert fetch_next_chunk(conn, "존재하지 않는 청크") is None
    conn.close()

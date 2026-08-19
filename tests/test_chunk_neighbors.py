"""인접 청크 조회 테스트 (T10.21)."""

from __future__ import annotations

from indexer.fts5.schema import connect
from indexer.fts5.store import store_document
from parser.schema import Chunk, ChunkType, ParsedDocument, TableData
from search.chunk_neighbors import fetch_next_chunk, fetch_previous_chunk, heading_before


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


class TestFetchPreviousChunk:
    """T10.25 — `fetch_next_chunk()`의 반대 방향."""

    def test_returns_the_chunk_inserted_just_before(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite3")
        _build_db(conn)

        previous = fetch_previous_chunk(conn, "d1_table_0")

        assert previous is not None
        assert previous.chunk_id == "d1_text_0"

    def test_returns_none_for_the_first_chunk(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite3")
        _build_db(conn)

        assert fetch_previous_chunk(conn, "d1_text_0") is None

    def test_returns_none_for_unknown_chunk(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite3")
        _build_db(conn)

        assert fetch_previous_chunk(conn, "없는id") is None


class TestHeadingBefore:
    """T10.25 — 표 앞 문단의 첫 줄을 "이 표가 무엇에 관한 것인지"로 쓴다.

    실측 배경: 응시료 표에는 등급(KAC/KPC/KSC)이 안 적혀 있고 **바로 앞 문단**에
    있다. 이걸 안 주면 모델이 표를 정확히 읽고도 등급을 잘못 붙인다.
    """

    def test_uses_the_first_line_of_the_previous_text_chunk(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite3")
        _build_db(conn)

        assert heading_before(conn, "d1_table_0") == "-응시방법-"

    def test_ignores_a_previous_table_chunk(self, tmp_path):
        """앞이 표면 제목 구실을 못 한다 — 노이즈만 된다."""
        conn = connect(tmp_path / "i.sqlite3")
        _build_db(conn)

        assert heading_before(conn, "d1_text_1") == ""

    def test_ignores_a_long_first_line(self, tmp_path):
        """본문 문단의 첫 줄을 제목처럼 얹으면 발췌만 길어진다."""
        conn = connect(tmp_path / "i.sqlite3")
        _build_db(conn)

        assert heading_before(conn, "d1_table_0", max_chars=3) == ""

    def test_returns_empty_for_the_first_chunk(self, tmp_path):
        conn = connect(tmp_path / "i.sqlite3")
        _build_db(conn)

        assert heading_before(conn, "d1_text_0") == ""

"""BM25 검색 테스트 (T2.7) — 대/소문자 구분·일치되는 단어 옵션 중심.

DESIGN 문서 §4.2 토글 두 개가 실제로 다른 검색 결과를 만드는지가 핵심이다.
"""

from __future__ import annotations

import pytest

from indexer.fts5.schema import connect
from indexer.fts5.search import build_match_query, search
from indexer.fts5.store import store_document
from parser.schema import Chunk, ChunkType, ParsedDocument


def _make_chunk(chunk_id, content, chunk_type=ChunkType.TEXT, page=1, table=None, image=None):
    return Chunk(
        chunk_id=chunk_id,
        doc_id="d1",
        file_path="x",
        file_name="x.txt",
        type=chunk_type,
        page_or_slide=page,
        content=content,
        table=table,
        image=image,
    )


@pytest.fixture
def db():
    conn = connect(":memory:")
    document = ParsedDocument(doc_id="d1", file_path="x", file_name="x.txt", title="t")
    document.chunks = [
        _make_chunk("c1", "API 문서를 확인하세요"),
        _make_chunk("c2", "api key를 발급받으세요"),
        _make_chunk("c3", "영업계약서 표준 문서"),
        _make_chunk("c4", "계약 해지 조건 안내"),
    ]
    store_document(conn, document)
    yield conn
    conn.close()


def test_default_search_is_case_insensitive(db):
    results = search(db, "API")
    assert {r.chunk_id for r in results} == {"c1", "c2"}


def test_case_sensitive_narrows_to_exact_case(db):
    upper = search(db, "API", case_sensitive=True)
    lower = search(db, "api", case_sensitive=True)
    assert [r.chunk_id for r in upper] == ["c1"]
    assert [r.chunk_id for r in lower] == ["c2"]


def test_exact_word_off_uses_prefix_matching(db):
    """접두 매칭(기본값)에서도 완전 토큰("계약 해지"의 "계약")은 당연히 잡혀야 한다.

    "영업계약서"처럼 "계약"이 토큰 중간에 있는 경우는 접두 매칭으로도 안 잡히는 게
    정상 동작이며, 그건 별도 테스트(test_prefix_match_does_not_catch_mid_token_substring)에서 확인한다.
    """
    results = search(db, "계약", exact_word=False)
    ids = {r.chunk_id for r in results}
    assert "c4" in ids


def test_prefix_match_does_not_catch_mid_token_substring(db):
    """FTS5 접두 매칭은 토큰의 '시작'만 본다 — "영업계약서"는 "계약"으로 시작하지 않으므로
    접두 매칭으로도 잡히지 않는 게 정상 동작이다 (실측 확인된 FTS5 특성)."""
    results = search(db, "계약", exact_word=False)
    ids = {r.chunk_id for r in results}
    assert "c3" not in ids


def test_exact_word_on_requires_full_token_match(db):
    results = search(db, "계약", exact_word=True)
    assert {r.chunk_id for r in results} == {"c4"}


def test_type_filter_restricts_results(db):
    assert search(db, "API", types=["table"]) == []
    assert len(search(db, "API", types=["text"])) == 2


def test_empty_query_returns_empty_list(db):
    assert search(db, "") == []
    assert search(db, "   ") == []


def test_special_characters_do_not_raise(db):
    # FTS5 연산자로 오인될 수 있는 문자들이 예외 없이 처리되는지만 확인 (매치 여부는 무관)
    for query in ["hello-world (2)", 'quote"inside', "a:b", "a AND b"]:
        search(db, query)  # 예외가 나지 않으면 통과


def test_table_caption_weighted_above_plain_content_match(db):
    """T2.6: 캡션·헤더 매치가 본문 매치보다 상위에 노출돼야 한다."""
    from parser.schema import TableData

    table = TableData(rows=[["8GB", "16GB"]], header_row=["최소 사양", "권장 사양"], caption="시스템 요구사항")
    doc2 = ParsedDocument(doc_id="d2", file_path="y", file_name="y.docx", title="t2")
    doc2.chunks = [
        _make_chunk("t1", "본문 어딘가에 요구사항이라는 단어가 살짝 등장한다"),
        Chunk(
            chunk_id="t2",
            doc_id="d2",
            file_path="y",
            file_name="y.docx",
            type=ChunkType.TABLE,
            page_or_slide=1,
            content=table.to_text(),
            table=table,
        ),
    ]
    store_document(db, doc2)

    results = search(db, "요구사항")
    assert results[0].chunk_id == "t2"


def test_build_match_query_prefix_vs_exact():
    assert build_match_query(["api"], exact_word=False) == '"api"*'
    assert build_match_query(["api"], exact_word=True) == '"api"'
    assert build_match_query(["a", "b"], exact_word=False) == '"a"* AND "b"*'


def test_build_match_query_escapes_quotes():
    assert build_match_query(['a"b'], exact_word=True) == '"a""b"'

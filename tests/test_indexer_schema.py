"""FTS5 스키마 테스트 (T2.2).

external content 테이블 패턴은 트리거 동기화가 핵심이라, INSERT/UPDATE/DELETE
각각에서 chunks_fts가 chunks와 어긋나지 않는지를 우선 검증한다.
"""

from __future__ import annotations

import pytest

from indexer.fts5.schema import connect


@pytest.fixture
def db():
    conn = connect(":memory:")
    conn.execute(
        "INSERT INTO documents(doc_id, file_path, file_name, title, status, indexed_at) "
        "VALUES ('d1','x','x.docx','제목','ok','now')"
    )
    yield conn
    conn.close()


def _insert_chunk(conn, chunk_id, content, caption="", chunk_type="text"):
    conn.execute(
        "INSERT INTO chunks(chunk_id, doc_id, file_path, file_name, type, page_or_slide, "
        "content, caption, created_at) VALUES (?,'d1','x','x.docx',?,1,?,?,'now')",
        (chunk_id, chunk_type, content, caption),
    )
    conn.commit()


def test_fts_match_is_case_insensitive(db):
    """unicode61은 색인 시점에 대소문자를 접는다 — 대소문자 구분은 search.py의
    후처리 몫이라는 설계 전제를 스키마 레벨에서 확인해둔다."""
    _insert_chunk(db, "c1", "API 문서를 확인하세요")
    rows = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'api'").fetchall()
    assert len(rows) == 1


def test_insert_trigger_syncs_fts(db):
    _insert_chunk(db, "c1", "검색 가능한 내용")
    rows = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '검색'").fetchall()
    assert len(rows) == 1


def test_update_trigger_syncs_fts(db):
    _insert_chunk(db, "c1", "이전 내용")
    db.execute("UPDATE chunks SET content = '새로운 내용' WHERE chunk_id = 'c1'")
    db.commit()
    assert db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '이전'").fetchall() == []
    assert len(db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '새로운'").fetchall()) == 1


def test_delete_trigger_syncs_fts(db):
    _insert_chunk(db, "c1", "삭제될 내용")
    db.execute("DELETE FROM chunks WHERE chunk_id = 'c1'")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0


def test_document_delete_cascades_to_chunks_and_fts(db):
    _insert_chunk(db, "c1", "문서에 딸린 내용")
    db.execute("DELETE FROM documents WHERE doc_id = 'd1'")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0


def test_caption_column_is_searchable_independently(db):
    _insert_chunk(db, "c1", "본문에는 없는 단어", caption="캡션전용키워드", chunk_type="table")
    rows = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH '캡션전용키워드'").fetchall()
    assert len(rows) == 1


def test_chunk_id_must_be_unique(db):
    _insert_chunk(db, "c1", "첫 번째")
    with pytest.raises(Exception):
        _insert_chunk(db, "c1", "중복 chunk_id")


def test_connect_creates_file_backed_db(tmp_path):
    db_path = tmp_path / "index.sqlite3"
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO documents(doc_id, file_path, file_name, title, status, indexed_at) "
        "VALUES ('d1','x','x.docx','t','ok','now')"
    )
    conn.commit()
    conn.close()

    assert db_path.is_file()

    reconnected = connect(db_path)
    assert reconnected.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    reconnected.close()

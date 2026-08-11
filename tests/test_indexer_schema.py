"""FTS5 스키마 테스트 (T2.2).

external content 테이블 패턴은 트리거 동기화가 핵심이라, INSERT/UPDATE/DELETE
각각에서 chunks_fts가 chunks와 어긋나지 않는지를 우선 검증한다.
"""

from __future__ import annotations

import sqlite3

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


def test_chunk_vectors_pk_migrates_from_legacy_single_column(tmp_path):
    """🔴 Phase 7.5 — 구버전 DB(chunk_id 단독 PK)를 열어도 조용히 새 스키마로 옮겨져야 한다.

    `CREATE TABLE IF NOT EXISTS`는 이미 만들어진 테이블을 바꾸지 않으므로,
    이 마이그레이션이 없으면 기존 사용자의 DB는 영원히 "모델 하나만 저장
    가능한" 구버전 스키마에 머문다 — 권장 모드로 전환하는 순간부터 다시
    조용히 벡터가 사라지는 버그가 재발한다.
    """
    db_path = tmp_path / "index.sqlite3"

    # 구버전 스키마를 직접 만든다 — connect()를 거치지 않고 DDL을 그대로 재현.
    legacy = sqlite3.connect(str(db_path))
    legacy.execute(
        """CREATE TABLE chunk_vectors (
            chunk_id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL
        )"""
    )
    legacy.execute(
        "INSERT INTO chunk_vectors VALUES ('c1', 'old-model', 4, X'00000000')"
    )
    legacy.commit()
    legacy.close()

    conn = connect(db_path)  # 마이그레이션이 여기서 일어나야 한다

    columns = conn.execute("PRAGMA table_info(chunk_vectors)").fetchall()
    pk_columns = {row["name"] for row in columns if row["pk"] > 0}
    assert pk_columns == {"chunk_id", "model"}

    # 구버전 데이터는 파생 데이터라 보존 대상이 아니다 — 재생성하면 된다.
    assert conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == 0
    conn.close()


def test_chunk_vectors_migration_is_idempotent(tmp_path):
    """이미 새 스키마인 DB를 다시 열어도 아무 일도 안 일어나야 한다(매번 DROP하면 안 됨)."""
    db_path = tmp_path / "index.sqlite3"
    conn = connect(db_path)
    conn.execute(
        "INSERT INTO documents(doc_id, file_path, file_name, title, status, indexed_at) "
        "VALUES ('d1','x','x.docx','t','ok','now')"
    )
    conn.execute(
        "INSERT INTO chunks(chunk_id, doc_id, file_path, file_name, type, content, created_at) "
        "VALUES ('c1','d1','x','x.docx','text','본문','now')"
    )
    conn.execute(
        "INSERT INTO chunk_vectors VALUES ('c1', 'model-a', 4, X'00000000000000000000000000000000')"
    )
    conn.commit()
    conn.close()

    reconnected = connect(db_path)  # 재연결 — 마이그레이션이 다시 안 돌아야 한다
    assert reconnected.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == 1
    reconnected.close()


def test_file_backed_db_uses_wal_journal_mode(tmp_path):
    """Phase 4부터 백그라운드 인덱싱(쓰기)과 UI 검색(읽기)이 동시에 일어난다.
    기본 롤백 저널은 쓰기 중 모든 읽기를 막으므로 WAL이 필요하다."""
    conn = connect(tmp_path / "index.sqlite3")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_memory_db_ignores_wal_without_error():
    """`:memory:` DB는 WAL을 지원하지 않지만 예외 없이 조용히 무시돼야 한다
    (기존 테스트 다수가 :memory:를 쓴다)."""
    conn = connect(":memory:")
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "memory"


def test_concurrent_reads_succeed_during_write(tmp_path):
    """WAL 전환 실측 검증 — 쓰기 스레드가 도는 동안 읽기가 락 없이 성공해야 한다."""
    import threading
    import time

    db_path = tmp_path / "index.sqlite3"
    connect(db_path).close()  # 파일 최초 생성

    errors: list[str] = []
    read_count = [0]
    stop = threading.Event()

    def reader() -> None:
        conn = connect(db_path)
        while not stop.is_set():
            try:
                conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
                read_count[0] += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
            time.sleep(0.002)
        conn.close()

    def writer() -> None:
        conn = connect(db_path)
        for i in range(50):
            conn.execute(
                "INSERT INTO documents(doc_id, file_path, file_name, title, status, indexed_at) "
                "VALUES (?,?,?,?,?,?)",
                (f"d{i}", "x", "x.txt", "t", "ok", "now"),
            )
            conn.commit()
        conn.close()

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    writer_thread.join(timeout=30)
    stop.set()
    reader_thread.join(timeout=5)

    assert errors == []
    assert read_count[0] > 0


def test_concurrent_fresh_file_creation_does_not_raise(tmp_path):
    """DB 파일이 아예 없는 상태에서 여러 커넥션이 동시에 처음 열리면,
    WAL 전환 SET이 겹쳐 "database is locked"가 날 수 있었다 (실측 재현됨).
    지금은 SET을 재시도로 흡수한다."""
    import threading

    db_path = tmp_path / "brand_new.sqlite3"
    errors: list[str] = []

    def opener() -> None:
        try:
            connect(db_path).close()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    threads = [threading.Thread(target=opener) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []

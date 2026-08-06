"""FTS5 인덱스 스키마 (T2.2).

`documents`/`chunks`에 원문을 그대로 보관하고, `chunks_fts`는 external content로
그 원문을 가리키기만 한다 (텍스트를 두 번 저장하지 않음). `unicode61` 토크나이저는
색인 시점에 대소문자를 항상 접으므로, "대/소문자 구분" 검색은 `chunks.content`의
원문을 후처리로 비교해 구현한다 — 실제 SQLite에서 "API"로 MATCH하면 "api"도
함께 걸리는 것을 확인했다 (search.py 참고).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,
    source_mtime REAL,
    source_hash TEXT,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    chunk_id TEXT UNIQUE NOT NULL,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    type TEXT NOT NULL,
    page_or_slide INTEGER,
    content TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',
    table_json TEXT,
    image_json TEXT,
    created_at TEXT NOT NULL,
    source_mtime REAL,
    source_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);

-- content='chunks' → chunks 테이블의 원문을 그대로 참조 (중복 저장 없음)
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, file_name, keywords, caption,
    content='chunks', content_rowid='id',
    tokenize='unicode61'
);

-- external content 테이블은 트리거로 직접 동기화해야 한다 (SQLite 공식 권장 패턴).
-- ON DELETE CASCADE로 지워지는 행도 트리거가 그대로 발동해 인덱스가 함께 정리된다.
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content, file_name, keywords, caption)
    VALUES (new.id, new.content, new.file_name, new.keywords, new.caption);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content, file_name, keywords, caption)
    VALUES ('delete', old.id, old.content, old.file_name, old.keywords, old.caption);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content, file_name, keywords, caption)
    VALUES ('delete', old.id, old.content, old.file_name, old.keywords, old.caption);
    INSERT INTO chunks_fts(rowid, content, file_name, keywords, caption)
    VALUES (new.id, new.content, new.file_name, new.keywords, new.caption);
END;
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """스키마가 적용된 커넥션을 반환한다. DB 파일이 없으면 새로 만든다."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn

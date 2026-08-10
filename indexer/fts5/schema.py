"""FTS5 인덱스 스키마 (T2.2).

`documents`/`chunks`에 원문을 그대로 보관하고, `chunks_fts`는 external content로
그 원문을 가리키기만 한다 (텍스트를 두 번 저장하지 않음). `unicode61` 토크나이저는
색인 시점에 대소문자를 항상 접으므로, "대/소문자 구분" 검색은 `chunks.content`의
원문을 후처리로 비교해 구현한다 — 실제 SQLite에서 "API"로 MATCH하면 "api"도
함께 걸리는 것을 확인했다 (search.py 참고).
"""

from __future__ import annotations

import sqlite3
import time
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

-- 벡터는 별도 테이블에 둔다 (Phase 3).
-- TECH 5.1은 ChromaDB를 지정했으나, 이 파이프라인은 ANN을 쓰지 않고 FTS5가 좁힌
-- 후보의 벡터만 chunk_id로 꺼내 직접 코사인을 계산한다. 그 용도에는 SQLite가
-- 더 정확히 부합하고(같은 트랜잭션·같은 쿼리로 조회), 의존성이 79개 늘지 않는다.
--
-- 🔴 기본키는 (chunk_id, model) 복합키다 — chunk_id 단독이 아니다.
-- Phase 7.5에서 KURE-v1로 실제 재인덱싱을 해보고서야 드러난 버그였다:
-- chunk_id 단독 PK였을 때는 청크당 벡터를 하나만 가질 수 있어서, 경량↔고성능
-- 모드를 전환할 때마다 INSERT OR REPLACE가 **이전 모델의 벡터를 지우고
-- 덮어썼다**. 두 모델 벡터가 공존해야 한다는 Phase 3 설계 의도(모델별 재순위
-- 비교, 모드 전환 시 재인덱싱 없이 유지)가 스키마 수준에서 애초에 성립하지
-- 않고 있었다 — LIGHT 하나만 실사용된 Phase 3~7 동안은 드러나지 않았다.
CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    model    TEXT NOT NULL,      -- 어떤 모델로 만든 벡터인지 (교체 시 무효화 판단)
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL,      -- float32 little-endian, L2 정규화 완료 상태
    PRIMARY KEY (chunk_id, model)
);

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
    """스키마가 적용된 커넥션을 반환한다. DB 파일이 없으면 새로 만든다.

    WAL 저널 모드를 켠다 — Phase 4부터 백그라운드 인덱싱(쓰기)과 UI 검색(읽기)이
    동시에 일어날 수 있는데, 기본 롤백 저널 모드는 쓰기 중 모든 읽기를 막는다.
    WAL은 읽기가 쓰기와 동시에 진행되도록 해준다. `:memory:` DB에는 적용되지
    않고 조용히 "memory" 모드로 남는다(실측 확인 — 예외 없이 무시됨).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # busy_timeout 없이는 락 경합 시 기본값(0ms)이라 즉시 "database is locked"를
    # 던진다. WAL이 동시 읽기/쓰기를 허용해도 전환 순간의 경합은 남는다.
    conn.execute("PRAGMA busy_timeout = 5000")
    _ensure_wal_mode(conn)
    conn.executescript(_DDL)
    _migrate_chunk_vectors_pk(conn)
    conn.commit()
    return conn


def _migrate_chunk_vectors_pk(conn: sqlite3.Connection) -> None:
    """구버전 `chunk_vectors`(chunk_id 단독 PK)를 복합키로 옮긴다.

    `CREATE TABLE IF NOT EXISTS`는 이미 만들어진 테이블의 컬럼·제약을 바꾸지
    않는다 — 이 프로젝트에 별도 마이그레이션 프레임워크가 없어(T10.5·Phase
    3까지 전부 "재인덱싱"으로 대응한 전례) 여기서 한 번만 감지해서 옮긴다.

    벡터는 전부 `embed_missing()`으로 재생성 가능한 파생 데이터라 안전하게
    한쪽만(구버전 쪽) 지우고 새 스키마로 다시 만들면 된다 — `chunks`/`documents`
    원문은 건드리지 않는다.
    """
    columns = conn.execute("PRAGMA table_info(chunk_vectors)").fetchall()
    if not columns:
        return  # 방금 새로 만들어진 테이블 — 이미 새 스키마다

    pk_columns = {row["name"] for row in columns if row["pk"] > 0}
    if pk_columns == {"chunk_id", "model"}:
        return  # 이미 마이그레이션됨

    # 구버전: chunk_id만 PK. 청크당 벡터가 최대 1개뿐이었으므로(모델 전환마다
    # 덮어써짐) 보존할 가치가 없다 — 지우고 새 스키마로 다시 만든다.
    conn.execute("DROP TABLE chunk_vectors")
    conn.executescript(_DDL)


def _ensure_wal_mode(conn: sqlite3.Connection) -> None:
    """journal_mode를 WAL로 맞춘다. 이미 WAL이면 재설정을 건너뛴다.

    WAL "설정"(값이 이미 wal이어도)은 내부적으로 배타적 잠금을 짧게 요구해,
    다른 연결이 마침 같은 순간 접속·초기화 중이면 `busy_timeout`이 있어도
    "database is locked"가 난다 (실측 재현됨). 반면 "조회"는 잠금이 필요
    없으므로, 이미 WAL이면 SET을 아예 시도하지 않는다 — DB 파일 최초 생성
    이후에는 모든 connect()가 조회만 하게 되어 이 문제를 비켜간다.

    다만 파일이 아예 처음 만들어지는 순간에는 여러 연결이 동시에 "아직 WAL
    아님"을 보고 동시에 SET을 시도하는 경합이 남는다 — 그 창구를 메우기
    위해 SET만 짧게 재시도한다.
    """
    current = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if current.lower() == "wal":
        return

    last_error: sqlite3.OperationalError | None = None
    for _ in range(10):
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last_error = exc
            time.sleep(0.1)
    raise last_error

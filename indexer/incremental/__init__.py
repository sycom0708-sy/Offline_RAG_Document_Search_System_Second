"""변경/신규 파일 판별 (T8.2, Phase 8).

`documents.source_mtime`/`source_hash`는 Phase 1부터 매 파싱마다 채워지고
있었다(`parser/base.py`). mtime을 먼저 보고, 다를 때만 해시를 계산한다 —
stat()은 사실상 공짜지만 SHA-256 파일 해시는 파일 전체를 읽어야 해서 비용이
있어, 변경 없는 파일에서는 해시 계산 자체를 생략한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from parser.utils.hashing import file_sha256
from parser.utils.ids import make_doc_id


def needs_reindex(conn: sqlite3.Connection, path: Path) -> bool:
    """이 파일을 다시 파싱해야 하면 True.

    mtime이 저장된 값과 같으면 변경 없음으로 보고 즉시 False(해시 계산 생략).
    mtime만 다르고 내용(해시)이 같으면 — 예: 저장 도구가 내용 변경 없이
    재저장한 경우 — 재파싱은 건너뛰되 DB의 mtime만 갱신해 다음 실행에서
    같은 해시 계산을 반복하지 않게 한다.
    """
    doc_id = make_doc_id(path)
    row = conn.execute(
        "SELECT source_mtime, source_hash FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        return True  # 신규 파일

    stored_mtime, stored_hash = row
    current_mtime = path.stat().st_mtime
    if stored_mtime is not None and current_mtime == stored_mtime:
        return False

    current_hash = file_sha256(path)
    if stored_hash is not None and current_hash == stored_hash:
        conn.execute(
            "UPDATE documents SET source_mtime = ? WHERE doc_id = ?",
            (current_mtime, doc_id),
        )
        conn.commit()
        return False

    return True

"""변경/신규 파일 판별 (T8.2, Phase 8).

`documents.source_mtime`/`source_hash`는 Phase 1부터 매 파싱마다 채워지고
있었다(`parser/base.py`). mtime을 먼저 보고, 다를 때만 해시를 계산한다 —
stat()은 사실상 공짜지만 SHA-256 파일 해시는 파일 전체를 읽어야 해서 비용이
있어, 변경 없는 파일에서는 해시 계산 자체를 생략한다.
"""

from __future__ import annotations

import sqlite3
from enum import Enum
from pathlib import Path

from parser.utils.hashing import file_sha256
from parser.utils.ids import make_doc_id


class FileChange(Enum):
    """파일 하나의 판정 결과 (Phase 11-B).

    Phase 8은 "다시 파싱해야 하나"만 알면 됐지만, 문서 관리 페이지가
    `신규`/`변경`을 따로 보여줘야 해서(DESIGN §14.4.1) 판정을 넓혔다.
    판별 자체는 원래부터 셋을 구분하고 있었다 — `row is None`이면 신규다.
    """

    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"

    @property
    def needs_parse(self) -> bool:
        return self is not FileChange.UNCHANGED


def classify_file(conn: sqlite3.Connection, path: Path) -> FileChange:
    """이 파일이 신규인지 · 변경됐는지 · 그대로인지 판정한다.

    mtime이 저장된 값과 같으면 변경 없음으로 보고 즉시 `UNCHANGED`(해시 계산
    생략). mtime만 다르고 내용(해시)이 같으면 — 예: 저장 도구가 내용 변경
    없이 재저장한 경우 — 재파싱은 건너뛰되 DB의 mtime만 갱신해 다음 실행에서
    같은 해시 계산을 반복하지 않게 한다.

    🔴 **파싱에 실패한 문서도 `UNCHANGED`로 나온다.** `parser/base.py`가
    파싱을 **시작할 때** mtime·해시를 채우고 `store_document()`가 그것을
    `status=FAILED` 문서에도 그대로 저장하기 때문이다. 즉 LibreOffice가 없어
    실패한 `.doc`은 나중에 LibreOffice를 설치해도 재인덱싱에서 계속
    건너뛰어진다. 여기서 상태를 보고 되살리지 않는 이유는 Phase 8이 측정한
    "무변경 재실행 0.87초"의 전제를 바꾸지 않기 위해서다 — 대신 문서 관리
    페이지의 `재시도` 버튼이 실패 파일만 골라 **강제로** 다시 파싱한다
    (`indexer.pipeline.reindex_files`, Phase 11-B [사용자 확정]).
    """
    doc_id = make_doc_id(path)
    row = conn.execute(
        "SELECT source_mtime, source_hash FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        return FileChange.NEW

    stored_mtime, stored_hash = row
    current_mtime = path.stat().st_mtime
    if stored_mtime is not None and current_mtime == stored_mtime:
        return FileChange.UNCHANGED

    current_hash = file_sha256(path)
    if stored_hash is not None and current_hash == stored_hash:
        conn.execute(
            "UPDATE documents SET source_mtime = ? WHERE doc_id = ?",
            (current_mtime, doc_id),
        )
        conn.commit()
        return FileChange.UNCHANGED

    return FileChange.CHANGED


def needs_reindex(conn: sqlite3.Connection, path: Path) -> bool:
    """이 파일을 다시 파싱해야 하면 True (Phase 8 이래의 이름 그대로).

    `classify_file()`을 bool로 좁힌 것뿐이다. "다시 파싱할까"만 묻는 자리에서
    열거형을 꺼내 비교하게 만들 이유가 없어 남겨 둔다.
    """
    return classify_file(conn, path).needs_parse

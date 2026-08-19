"""인접 청크 조회 (T10.21) — "근처 내용 더보기".

검색은 청크 단위로 매치하는데, 문서를 파싱할 때 헤딩 문단과 그 뒤에 이어지는
실제 내용(특히 표 — Phase 1 결정: 표는 구조 보존을 위해 별도 청크로 분리)이
서로 다른 청크로 쪼개져 있는 경우가 있다. 검색어가 헤딩과 거의 그대로
겹치면 그 헤딩 청크만 1위로 올라오고, 바로 다음 청크(실제 내용)는 화면에
안 보일 수 있다(실사용에서 발견, 2026-08-15).

`chunks` 테이블에는 문서 내 순서를 나타내는 별도 컬럼이 없다 — 대신
`store_document()`가 청크를 원본 순서 그대로 삽입하므로, 같은 `doc_id` 안에서
내부 PK(`id`, autoincrement)가 삽입 순서 = 문서 내 순서와 같다.
"""

from __future__ import annotations

import sqlite3

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType


def fetch_next_chunk(conn: sqlite3.Connection, chunk_id: str) -> SearchResult | None:
    """`chunk_id` 바로 다음(같은 문서, 삽입 순서 기준)에 오는 청크를 반환한다.

    문서의 마지막 청크이거나 `chunk_id` 자체를 못 찾으면 `None`."""
    row = conn.execute(
        "SELECT id, doc_id FROM chunks WHERE chunk_id = ?", (chunk_id,)
    ).fetchone()
    if row is None:
        return None

    next_row = conn.execute(
        """
        SELECT chunk_id, doc_id, file_path, file_name, type, page_or_slide,
               content, caption, table_json, image_json
        FROM chunks
        WHERE doc_id = ? AND id > ?
        ORDER BY id
        LIMIT 1
        """,
        (row["doc_id"], row["id"]),
    ).fetchone()
    if next_row is None:
        return None

    return SearchResult(
        chunk_id=next_row["chunk_id"],
        doc_id=next_row["doc_id"],
        file_path=next_row["file_path"],
        file_name=next_row["file_name"],
        type=ChunkType(next_row["type"]),
        page_or_slide=next_row["page_or_slide"],
        content=next_row["content"],
        caption=next_row["caption"],
        score=0.0,
        table_json=next_row["table_json"],
        image_json=next_row["image_json"],
    )


def fetch_previous_chunk(conn: sqlite3.Connection, chunk_id: str) -> SearchResult | None:
    """`chunk_id` 바로 앞(같은 문서, 삽입 순서 기준)에 오는 청크를 반환한다.

    `fetch_next_chunk()`의 반대 방향이다 — 같은 "내부 PK = 문서 내 순서" 성질에
    기댄다. 문서의 첫 청크이거나 `chunk_id`를 못 찾으면 `None`.
    """
    row = conn.execute(
        "SELECT id, doc_id FROM chunks WHERE chunk_id = ?", (chunk_id,)
    ).fetchone()
    if row is None:
        return None

    prev_row = conn.execute(
        """
        SELECT chunk_id, doc_id, file_path, file_name, type, page_or_slide,
               content, caption, table_json, image_json
        FROM chunks
        WHERE doc_id = ? AND id < ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (row["doc_id"], row["id"]),
    ).fetchone()
    if prev_row is None:
        return None

    return SearchResult(
        chunk_id=prev_row["chunk_id"],
        doc_id=prev_row["doc_id"],
        file_path=prev_row["file_path"],
        file_name=prev_row["file_name"],
        type=ChunkType(prev_row["type"]),
        page_or_slide=prev_row["page_or_slide"],
        content=prev_row["content"],
        caption=prev_row["caption"],
        score=0.0,
        table_json=prev_row["table_json"],
        image_json=prev_row["image_json"],
    )


def heading_before(conn: sqlite3.Connection, chunk_id: str, *, max_chars: int = 120) -> str:
    """`chunk_id` 앞 문단의 첫 줄을 "이 청크가 무엇에 관한 것인지"로 돌려준다 (T10.25).

    표에는 등급·구분이 안 적혀 있고 **바로 앞 문단**에 있는 경우가 흔하다 —
    실측: "KAC(Korea Associate Coach) : 코치인증자격 1단계" 청크 다음이 그
    등급의 응시 서류표다. 이게 없으면 모델이 표를 정확히 읽어도 어느 등급
    이야기인지 말할 수 없다(실제로 "KPC 응시자는 40만원"처럼 등급을 잘못
    붙였다).

    앞 청크가 표·이미지면 제목 구실을 못 하므로 쓰지 않는다. 첫 줄만, 그것도
    짧을 때만 쓴다 — 본문 문단의 첫 줄을 제목처럼 얹으면 노이즈만 된다.
    """
    previous = fetch_previous_chunk(conn, chunk_id)
    if previous is None or previous.type != ChunkType.TEXT:
        return ""

    for line in previous.content.splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= max_chars else ""
    return ""

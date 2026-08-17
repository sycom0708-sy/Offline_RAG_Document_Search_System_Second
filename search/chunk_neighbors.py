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

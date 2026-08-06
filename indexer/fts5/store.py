"""ParsedDocument → chunks/chunks_fts 저장 (T2.3, T2.5, T2.6).

제목은 Phase 1 파서가 이미 뽑아둔 `ParsedDocument.title`을 그대로 쓴다
(재추출하지 않음 — T2.5는 청크 단위 키워드 추출까지는 범위에 넣지 않고,
파서가 표/이미지 청크에 이미 채워둔 `Chunk.keywords`를 그대로 인덱싱한다).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from indexer.chunker import chunk_text
from parser.schema import Chunk, ChunkType, ParsedDocument


def store_document(conn: sqlite3.Connection, document: ParsedDocument) -> None:
    """document를 저장한다. 같은 doc_id가 이미 있으면 통째로 교체한다.

    chunks 삭제는 documents 삭제에 딸린 ON DELETE CASCADE로 처리되고,
    FTS 인덱스는 schema.py의 트리거가 함께 정리한다 (증분 갱신은 Phase 8 소관 —
    여기서는 "다시 인덱싱하면 항상 최신 상태"만 보장한다).
    """
    conn.execute("DELETE FROM documents WHERE doc_id = ?", (document.doc_id,))
    conn.execute(
        """
        INSERT INTO documents(doc_id, file_path, file_name, title, status,
                               source_mtime, source_hash, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document.doc_id,
            document.file_path,
            document.file_name,
            document.title,
            document.status.value,
            document.source_mtime,
            document.source_hash,
            datetime.now(timezone.utc).isoformat(),
        ),
    )

    for chunk in document.chunks:
        _store_chunk(conn, chunk)

    conn.commit()


def _table_caption_text(chunk: Chunk) -> str:
    """T2.6: 표의 캡션·헤더를 별도 컬럼에 실어 검색 가중치를 높인다 (TECH 4.3절)."""
    if chunk.type is not ChunkType.TABLE or chunk.table is None:
        return ""
    parts = [chunk.table.caption, *chunk.table.header_row]
    return " ".join(p for p in parts if p)


def _store_chunk(conn: sqlite3.Connection, chunk: Chunk) -> None:
    keywords_text = " ".join(chunk.keywords)
    caption_text = _table_caption_text(chunk)
    table_json = json.dumps(asdict(chunk.table), ensure_ascii=False) if chunk.table else None
    image_json = json.dumps(asdict(chunk.image), ensure_ascii=False) if chunk.image else None

    if chunk.type is ChunkType.TEXT:
        # 문장 경계를 지키며 검색에 적당한 크기로 재분할한다 (T2.4).
        pieces = chunk_text(chunk.content) or [chunk.content]
    else:
        # table/image는 구조·참조가 있어 재분할하지 않는다 (TECH 3.1절).
        pieces = [chunk.content]

    for index, piece in enumerate(pieces):
        sub_chunk_id = chunk.chunk_id if len(pieces) == 1 else f"{chunk.chunk_id}#{index}"
        conn.execute(
            """
            INSERT INTO chunks(chunk_id, doc_id, file_path, file_name, type,
                                page_or_slide, content, caption, keywords,
                                table_json, image_json, created_at,
                                source_mtime, source_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sub_chunk_id,
                chunk.doc_id,
                chunk.file_path,
                chunk.file_name,
                chunk.type.value,
                chunk.page_or_slide,
                piece,
                caption_text,
                keywords_text,
                table_json,
                image_json,
                chunk.created_at,
                chunk.source_mtime,
                chunk.source_hash,
            ),
        )

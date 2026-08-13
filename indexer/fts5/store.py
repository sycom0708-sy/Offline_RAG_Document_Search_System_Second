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
from typing import Callable

from indexer.chunker import chunk_text
from parser.schema import Chunk, ChunkType, ParsedDocument


def store_document(
    conn: sqlite3.Connection,
    document: ParsedDocument,
    count_tokens: Callable[[str], int] | None = None,
) -> list[str]:
    """document를 저장한다. 같은 doc_id가 이미 있으면 통째로 교체한다.

    `count_tokens`를 넘기면 청크를 **토큰 수** 기준으로 자른다. 임베딩을 만들
    인덱스라면 반드시 넘겨야 한다 — 문자 수 기준으로 자르면 임베딩 모델의
    입력 한계를 넘겨 뒷부분이 조용히 잘린다 (`indexer/chunker.py` 참고).

    chunks 삭제는 documents 삭제에 딸린 ON DELETE CASCADE로 처리되고,
    FTS 인덱스는 schema.py의 트리거가 함께 정리한다.

    반환값은 교체되기 전 이 문서에 있던 이미지 청크의 chunk_id 목록이다
    (Phase 8, T8.4). `chunk_id`는 `doc_id+type+ordinal` 기반이라 이미지
    내용이 바뀌어도 ordinal이 같으면 chunk_id가 그대로다 — 즉 썸네일 캐시
    키가 안 바뀌어 옛 이미지가 계속 나올 수 있다. 이 함수는 파일 내용이
    실제로 바뀐 경우에만 호출되므로(`indexer.incremental.needs_reindex`가
    걸러낸 뒤), 옛 이미지 청크를 조건 없이 모아 반환해도 안전하다 — 호출부가
    해당 chunk_id의 캐시 파일을 지우면 다음 조회 때 최신 원본으로 재생성된다.
    """
    stale_image_chunk_ids = [
        row[0]
        for row in conn.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id = ? AND type = 'image'",
            (document.doc_id,),
        )
    ]

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
        _store_chunk(conn, chunk, count_tokens)

    conn.commit()
    return stale_image_chunk_ids


def _table_caption_text(chunk: Chunk) -> str:
    """T2.6: 표의 캡션·헤더를 별도 컬럼에 실어 검색 가중치를 높인다 (TECH 4.3절)."""
    if chunk.type is not ChunkType.TABLE or chunk.table is None:
        return ""
    parts = [chunk.table.caption, *chunk.table.header_row]
    return " ".join(p for p in parts if p)


def _store_chunk(
    conn: sqlite3.Connection,
    chunk: Chunk,
    count_tokens: Callable[[str], int] | None = None,
) -> None:
    keywords_text = " ".join(chunk.keywords)
    caption_text = _table_caption_text(chunk)
    table_json = json.dumps(asdict(chunk.table), ensure_ascii=False) if chunk.table else None
    image_json = json.dumps(asdict(chunk.image), ensure_ascii=False) if chunk.image else None

    if chunk.type is ChunkType.TEXT:
        # 문장 경계를 지키며 검색에 적당한 크기로 재분할한다 (T2.4).
        pieces = chunk_text(chunk.content, count_tokens=count_tokens) or [chunk.content]
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

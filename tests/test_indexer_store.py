"""저장 파이프라인 테스트 (T2.3, T2.5, T2.6)."""

from __future__ import annotations

import json

import pytest

from indexer.fts5.schema import connect
from indexer.fts5.store import store_document
from parser import parse_file
from parser.schema import Chunk, ChunkType, ParsedDocument


@pytest.fixture
def db():
    conn = connect(":memory:")
    yield conn
    conn.close()


def test_store_real_docx_preserves_chunk_types(db, sample_docx):
    document = parse_file(sample_docx)
    store_document(db, document)

    rows = db.execute("SELECT type FROM chunks ORDER BY id").fetchall()
    stored_types = {r["type"] for r in rows}
    original_types = {c.type.value for c in document.chunks}
    assert stored_types == original_types


def test_table_chunk_caption_carries_header_and_caption(db, sample_docx):
    document = parse_file(sample_docx)
    store_document(db, document)

    row = db.execute("SELECT caption FROM chunks WHERE type = 'table'").fetchone()
    assert row is not None
    assert "구분" in row["caption"]  # TableData.header_row가 caption 컬럼에 실림


def test_table_and_image_chunks_are_not_resplit(db, sample_docx):
    """구조가 있는 청크는 T2.4 청킹을 거치지 않고 원형 그대로 저장돼야 한다 (TECH 3.1절)."""
    document = parse_file(sample_docx)
    store_document(db, document)

    table_rows = db.execute("SELECT chunk_id FROM chunks WHERE type = 'table'").fetchall()
    image_rows = db.execute("SELECT chunk_id FROM chunks WHERE type = 'image'").fetchall()
    for row in [*table_rows, *image_rows]:
        assert "#" not in row["chunk_id"]


def test_long_text_chunk_is_split_with_suffixed_ids(db):
    long_text = ("오프라인 문서 검색 시스템은 완전히 폐쇄된 네트워크에서 동작한다. " * 20).strip()
    document = ParsedDocument(doc_id="d1", file_path="x", file_name="x.txt", title="t")
    document.chunks.append(
        Chunk(
            chunk_id="d1_text_00000",
            doc_id="d1",
            file_path="x",
            file_name="x.txt",
            type=ChunkType.TEXT,
            page_or_slide=None,
            content=long_text,
        )
    )
    store_document(db, document)

    rows = db.execute("SELECT chunk_id FROM chunks ORDER BY id").fetchall()
    assert len(rows) > 1
    ids = [r["chunk_id"] for r in rows]
    assert ids == [f"d1_text_00000#{i}" for i in range(len(ids))]
    assert len(set(ids)) == len(ids)  # chunk_id UNIQUE 제약을 만족


def test_short_text_chunk_keeps_original_chunk_id(db):
    document = ParsedDocument(doc_id="d1", file_path="x", file_name="x.txt", title="t")
    document.chunks.append(
        Chunk(
            chunk_id="d1_text_00000",
            doc_id="d1",
            file_path="x",
            file_name="x.txt",
            type=ChunkType.TEXT,
            page_or_slide=None,
            content="짧은 문장.",
        )
    )
    store_document(db, document)

    row = db.execute("SELECT chunk_id FROM chunks").fetchone()
    assert row["chunk_id"] == "d1_text_00000"


def test_restoring_same_document_is_idempotent(db, sample_docx):
    document = parse_file(sample_docx)
    store_document(db, document)
    store_document(db, document)

    assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    doc_chunk_count = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert doc_chunk_count == db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]


def test_table_json_round_trips_structure(db):
    from parser.schema import TableData

    table = TableData(rows=[["8GB", "16GB"]], header_row=["최소", "권장"], caption="사양")
    document = ParsedDocument(doc_id="d1", file_path="x", file_name="x.xlsx", title="t")
    document.chunks.append(
        Chunk(
            chunk_id="d1_table_00000",
            doc_id="d1",
            file_path="x",
            file_name="x.xlsx",
            type=ChunkType.TABLE,
            page_or_slide=1,
            content=table.to_text(),
            table=table,
        )
    )
    store_document(db, document)

    row = db.execute("SELECT table_json FROM chunks").fetchone()
    restored = json.loads(row["table_json"])
    assert restored["rows"] == [["8GB", "16GB"]]
    assert restored["header_row"] == ["최소", "권장"]
    assert restored["caption"] == "사양"


def test_image_json_round_trips_structure(db):
    from parser.schema import ImageData

    image = ImageData(image_path="/tmp/img.png", caption="도면", origin="extracted")
    document = ParsedDocument(doc_id="d1", file_path="x", file_name="x.docx", title="t")
    document.chunks.append(
        Chunk(
            chunk_id="d1_image_00000",
            doc_id="d1",
            file_path="x",
            file_name="x.docx",
            type=ChunkType.IMAGE,
            page_or_slide=1,
            content="도면",
            image=image,
        )
    )
    store_document(db, document)

    row = db.execute("SELECT image_json FROM chunks").fetchone()
    restored = json.loads(row["image_json"])
    assert restored["image_path"] == "/tmp/img.png"
    assert restored["origin"] == "extracted"


def _document_with_image(doc_id: str) -> ParsedDocument:
    from parser.schema import ImageData

    image = ImageData(image_path="/tmp/img.png", caption="도면", origin="extracted")
    document = ParsedDocument(doc_id=doc_id, file_path="x", file_name="x.docx", title="t")
    document.chunks.append(
        Chunk(
            chunk_id=f"{doc_id}_image_00000",
            doc_id=doc_id,
            file_path="x",
            file_name="x.docx",
            type=ChunkType.IMAGE,
            page_or_slide=1,
            content="도면",
            image=image,
        )
    )
    return document


def test_store_document_returns_no_stale_ids_for_new_document(db):
    """처음 저장하는 문서는 교체 대상이 없으니 빈 목록을 돌려줘야 한다."""
    stale = store_document(db, _document_with_image("d1"))
    assert stale == []


def test_store_document_returns_previous_image_chunk_ids_on_replace(db):
    """재파싱으로 문서가 교체될 때, 옛 이미지 청크 id를 돌려줘야 썸네일 캐시를
    지울 수 있다 (Phase 8, T8.4) — chunk_id가 doc_id+type+ordinal 기반이라
    이미지 내용이 바뀌어도 캐시 키(chunk_id)가 그대로일 수 있기 때문이다.
    """
    store_document(db, _document_with_image("d1"))
    stale = store_document(db, _document_with_image("d1"))  # 같은 doc_id로 재저장(=재파싱 시뮬레이션)

    assert stale == ["d1_image_00000"]

"""공통 스키마 검증 (T1.12)."""

from __future__ import annotations

import json

import pytest

from parser.schema import Chunk, ChunkType, ImageData, ParseStatus, ParsedDocument, TableData

REQUIRED_FIELDS = {
    "chunk_id",
    "doc_id",
    "file_path",
    "file_name",
    "type",
    "page_or_slide",
    "content",
    "keywords",
    "embedding_vector",
    "created_at",
    "source_mtime",
    "source_hash",
}


def make_chunk(**overrides) -> Chunk:
    payload = dict(
        chunk_id="c1",
        doc_id="d1",
        file_path="/tmp/a.txt",
        file_name="a.txt",
        type=ChunkType.TEXT,
        page_or_slide=None,
        content="본문",
    )
    payload.update(overrides)
    return Chunk(**payload)


def test_chunk_has_all_tech_schema_fields():
    assert REQUIRED_FIELDS <= set(make_chunk().to_dict())


def test_chunk_is_json_serializable():
    restored = json.loads(make_chunk().to_json())
    assert restored["type"] == "text"
    assert restored["content"] == "본문"


def test_table_chunk_requires_table_data():
    with pytest.raises(ValueError):
        make_chunk(type=ChunkType.TABLE)


def test_image_chunk_requires_image_data():
    with pytest.raises(ValueError):
        make_chunk(type=ChunkType.IMAGE)


def test_table_to_text_puts_caption_and_header_first():
    table = TableData(rows=[["8GB", "16GB"]], header_row=["최소", "권장"], caption="사양표")
    assert table.to_text().splitlines() == ["사양표", "최소 | 권장", "8GB | 16GB"]


def test_table_data_preserves_row_column_structure():
    table = TableData(rows=[["a", "b"], ["c", "d"]], header_row=["h1", "h2"])
    chunk = make_chunk(type=ChunkType.TABLE, table=table)
    assert chunk.table.rows == [["a", "b"], ["c", "d"]]
    assert chunk.table.header_row == ["h1", "h2"]


def test_from_rows_promotes_first_row_to_header():
    table = TableData.from_rows([["h1", "h2"], ["a", "b"]])
    assert table.header_row == ["h1", "h2"]
    assert table.rows == [["a", "b"]]


def test_from_rows_keeps_single_row_as_data():
    """1행짜리 표를 헤더로 승격하면 rows가 비어 표 카드가 빈 표로 렌더링된다."""
    table = TableData.from_rows([["1과목 : 리눅스 운영 및 관리"]])
    assert table.rows == [["1과목 : 리눅스 운영 및 관리"]]
    assert table.header_row == []


def test_from_rows_drops_empty_rows():
    table = TableData.from_rows([["h1", "h2"], ["", "  "], ["a", "b"]])
    assert table.rows == [["a", "b"]]


@pytest.mark.parametrize("rows", [[], [["", ""]], [["  "], [""]]])
def test_from_rows_returns_none_when_empty(rows):
    assert TableData.from_rows(rows) is None


def test_from_rows_keeps_caption():
    assert TableData.from_rows([["a"]], caption="사양표").caption == "사양표"


def test_chunk_type_accepts_string():
    assert make_chunk(type="text").type is ChunkType.TEXT


def test_chunks_of_filters_by_type():
    document = ParsedDocument(doc_id="d1", file_path="/tmp/a", file_name="a", title="a")
    document.chunks.append(make_chunk())
    document.chunks.append(
        make_chunk(type=ChunkType.IMAGE, image=ImageData(image_path="/tmp/x.png"))
    )
    assert len(document.chunks_of(ChunkType.TEXT)) == 1
    assert len(document.chunks_of(ChunkType.IMAGE)) == 1
    assert document.chunks_of(ChunkType.TABLE) == []


def test_document_default_status_is_ok():
    document = ParsedDocument(doc_id="d1", file_path="/tmp/a", file_name="a", title="a")
    assert document.status is ParseStatus.OK
    assert document.to_dict()["status"] == "ok"

"""형식별 파서 파싱 정확도 테스트 (T1.13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from parser import ChunkType, ParseStatus, parse_file
from tests.fixtures.generate_samples import BODY_PARAGRAPH, BODY_TEXT, TABLE_HEADER, TABLE_ROWS

EXPECTED_TABLE_ROWS = [row for row in TABLE_ROWS]


def text_of(document) -> str:
    return "\n".join(c.content for c in document.chunks_of(ChunkType.TEXT))


# --- TXT (T1.4) ---------------------------------------------------------


def test_txt_parses_utf8(sample_txt):
    document = parse_file(sample_txt)
    assert document.status is ParseStatus.OK
    assert BODY_PARAGRAPH in text_of(document)
    assert document.title == BODY_TEXT


@pytest.mark.parametrize("encoding", ["cp949", "euc-kr", "utf-8-sig", "utf-16"])
def test_txt_detects_korean_encodings(tmp_path, encoding):
    path = tmp_path / f"sample_{encoding}.txt"
    path.write_bytes(f"{BODY_TEXT}\n{BODY_PARAGRAPH}".encode(encoding))
    document = parse_file(path)
    assert BODY_PARAGRAPH in text_of(document)


def test_txt_handles_korean_path(tmp_path):
    korean_dir = tmp_path / "한글폴더"
    korean_dir.mkdir()
    path = korean_dir / "한글문서.txt"
    path.write_text(BODY_PARAGRAPH, encoding="cp949")
    document = parse_file(path)
    assert BODY_PARAGRAPH in text_of(document)
    assert document.file_name == "한글문서.txt"


# --- PDF (T1.2) ---------------------------------------------------------


def test_pdf_extracts_text_table_and_images(sample_pdf):
    document = parse_file(sample_pdf)
    assert document.status is ParseStatus.OK
    assert BODY_TEXT in text_of(document)

    tables = document.chunks_of(ChunkType.TABLE)
    assert len(tables) == 1
    assert tables[0].table.header_row == TABLE_HEADER
    assert tables[0].table.rows == EXPECTED_TABLE_ROWS

    images = document.chunks_of(ChunkType.IMAGE)
    assert images, "삽입 이미지가 추출되지 않았습니다"
    assert all(Path(c.image.image_path).is_file() for c in images)


def test_pdf_renders_vector_diagram_page(sample_pdf):
    """벡터 도형은 이미지로 추출되지 않으므로 페이지 렌더링 캡처가 있어야 한다 (TECH 3.1절)."""
    document = parse_file(sample_pdf)
    rendered = [c for c in document.chunks_of(ChunkType.IMAGE) if c.image.origin == "rendered"]
    assert len(rendered) == 1
    assert rendered[0].page_or_slide == 2
    assert Path(rendered[0].image.image_path).is_file()


def test_pdf_records_page_numbers(sample_pdf):
    document = parse_file(sample_pdf)
    assert all(c.page_or_slide is not None for c in document.chunks)


# --- DOCX (T1.3) --------------------------------------------------------


def test_docx_extracts_text_table_and_image(sample_docx):
    document = parse_file(sample_docx)
    assert document.status is ParseStatus.OK
    assert document.title == BODY_TEXT
    assert BODY_PARAGRAPH in text_of(document)

    tables = document.chunks_of(ChunkType.TABLE)
    assert len(tables) == 1
    assert tables[0].table.header_row == TABLE_HEADER
    assert tables[0].table.rows == EXPECTED_TABLE_ROWS

    images = document.chunks_of(ChunkType.IMAGE)
    assert len(images) == 1
    assert Path(images[0].image.image_path).is_file()


def test_docx_keeps_paragraphs_around_table_separate(sample_docx):
    """표 앞뒤 문단이 하나의 청크로 뭉치지 않아야 한다."""
    document = parse_file(sample_docx)
    texts = document.chunks_of(ChunkType.TEXT)
    assert len(texts) == 2
    assert "표 아래 문단입니다." == texts[1].content


# --- XLSX (T1.5) --------------------------------------------------------


def test_xlsx_extracts_each_sheet_as_table(sample_xlsx):
    document = parse_file(sample_xlsx)
    assert document.status is ParseStatus.OK

    tables = document.chunks_of(ChunkType.TABLE)
    assert len(tables) == 2
    assert tables[0].table.caption == "사양표"
    assert tables[0].table.header_row == TABLE_HEADER
    assert tables[0].table.rows == EXPECTED_TABLE_ROWS
    assert tables[0].page_or_slide == 1


def test_xlsx_extracts_embedded_image(sample_xlsx):
    document = parse_file(sample_xlsx)
    images = document.chunks_of(ChunkType.IMAGE)
    assert len(images) == 1
    assert Path(images[0].image.image_path).is_file()


# --- PPTX (T1.6) --------------------------------------------------------


def test_pptx_extracts_per_slide_content(sample_pptx):
    document = parse_file(sample_pptx)
    assert document.status is ParseStatus.OK
    assert BODY_TEXT in text_of(document)

    tables = document.chunks_of(ChunkType.TABLE)
    assert len(tables) == 1
    assert tables[0].page_or_slide == 2
    assert tables[0].table.header_row == TABLE_HEADER
    assert tables[0].table.rows == EXPECTED_TABLE_ROWS

    images = document.chunks_of(ChunkType.IMAGE)
    assert len(images) == 1
    assert images[0].page_or_slide == 2


# --- HWPX (T1.8) --------------------------------------------------------


def test_hwpx_extracts_text_table_and_image(sample_hwpx):
    document = parse_file(sample_hwpx)
    assert document.status is ParseStatus.OK
    assert BODY_PARAGRAPH in text_of(document)

    tables = document.chunks_of(ChunkType.TABLE)
    assert len(tables) == 1
    assert tables[0].table.header_row == TABLE_HEADER
    assert tables[0].table.rows == EXPECTED_TABLE_ROWS

    images = document.chunks_of(ChunkType.IMAGE)
    assert len(images) == 1
    assert Path(images[0].image.image_path).is_file()


def test_hwpx_keeps_paragraphs_around_table_separate(sample_hwpx):
    document = parse_file(sample_hwpx)
    texts = document.chunks_of(ChunkType.TEXT)
    assert len(texts) == 2
    assert texts[1].content == "표 아래 문단입니다."


def test_hwpx_rejects_non_zip(tmp_path):
    from parser import DocumentReadError

    path = tmp_path / "broken.hwpx"
    path.write_bytes(b"not a zip file")
    with pytest.raises(DocumentReadError):
        parse_file(path)


# --- 공통 규칙 (T1.10 / T1.11 / T1.12) -----------------------------------

ALL_SAMPLE_KEYS = ["sample.txt", "sample.pdf", "sample.docx", "sample.xlsx", "sample.pptx", "sample.hwpx"]


@pytest.mark.parametrize("sample_key", ALL_SAMPLE_KEYS)
def test_every_parser_emits_common_schema(samples, sample_key):
    document = parse_file(samples[sample_key])
    assert document.chunks, f"{sample_key}에서 청크가 추출되지 않았습니다"
    for chunk in document.chunks:
        assert chunk.doc_id == document.doc_id
        assert chunk.file_name == document.file_name
        assert chunk.source_hash == document.source_hash
        assert chunk.source_mtime == document.source_mtime
        assert chunk.created_at
        assert chunk.embedding_vector is None  # Phase 3에서 채워진다


@pytest.mark.parametrize("sample_key", ALL_SAMPLE_KEYS)
def test_chunk_ids_are_unique(samples, sample_key):
    document = parse_file(samples[sample_key])
    ids = [c.chunk_id for c in document.chunks]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("sample_key", ALL_SAMPLE_KEYS)
def test_tables_are_never_merged_into_text_chunks(samples, sample_key):
    """표 내용이 텍스트 청크에 섞이면 행·열 구조가 소실된다 (TECH 3.1절)."""
    document = parse_file(samples[sample_key])
    for chunk in document.chunks_of(ChunkType.TEXT):
        assert "8GB | 16GB" not in chunk.content


@pytest.mark.parametrize("sample_key", ALL_SAMPLE_KEYS)
def test_table_chunks_carry_structured_data(samples, sample_key):
    document = parse_file(samples[sample_key])
    for chunk in document.chunks_of(ChunkType.TABLE):
        assert chunk.table is not None
        assert isinstance(chunk.table.rows, list)
        # 캡션/헤더는 FTS5 키워드 가중에 쓰인다 (TECH 4.3절)
        assert chunk.keywords


@pytest.mark.parametrize("sample_key", ALL_SAMPLE_KEYS)
def test_image_chunks_point_to_existing_files(samples, sample_key):
    document = parse_file(samples[sample_key])
    for chunk in document.chunks_of(ChunkType.IMAGE):
        assert chunk.image is not None
        assert Path(chunk.image.image_path).is_file()
        assert chunk.image.origin in {"extracted", "rendered"}

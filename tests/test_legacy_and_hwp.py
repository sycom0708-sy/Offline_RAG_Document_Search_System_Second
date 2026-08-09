"""구버전 포맷(T1.9)·HWP(T1.7) 테스트.

실제 변환/파싱 검증은 LibreOffice와 유효한 .hwp 샘플이 있어야 하므로
환경이 갖춰지지 않으면 사유를 명시하고 건너뛴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from parser import ChunkType, ParseStatus, parse_file
from parser.utils import libreoffice
from parser.utils.imaging import sniff_image_extension
from parser.utils.libreoffice import (
    ConversionFailedError,
    LibreOfficeNotFoundError,
)

requires_libreoffice = pytest.mark.skipif(
    not libreoffice.is_available(),
    reason="LibreOffice(soffice) 미설치 — SOFFICE_PATH 환경변수로 경로를 지정하면 실행됩니다",
)


# --- LibreOffice wrapper (환경 무관) -------------------------------------


def test_find_soffice_prefers_env_var(tmp_path, monkeypatch):
    fake = tmp_path / "soffice.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("SOFFICE_PATH", str(fake))
    assert libreoffice.find_soffice() == fake


def test_missing_libreoffice_raises_specific_error(tmp_path, monkeypatch):
    monkeypatch.delenv("SOFFICE_PATH", raising=False)
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: None)
    with pytest.raises(LibreOfficeNotFoundError) as excinfo:
        libreoffice.convert(tmp_path / "a.doc", "docx")
    message = str(excinfo.value)
    assert "SOFFICE_PATH" in message  # 고급 사용자용 대안 경로
    # T10.2: 일반 사용자에게는 포터블 설치 안내가 우선이어야 한다.
    assert libreoffice.is_missing_libreoffice_error(message)


def test_is_missing_libreoffice_error_only_matches_the_not_found_case():
    """변환 실패(soffice는 있는데 이 파일에서 오류)를 미설치로 오인하면 안 된다."""
    assert libreoffice.is_missing_libreoffice_error(
        f"LibreOffice(soffice)를 찾을 수 없습니다. {libreoffice.INSTALL_HINT} "
        "(또는 SOFFICE_PATH 환경변수로 실행 파일 경로를 지정하세요)"
    ) is True
    assert libreoffice.is_missing_libreoffice_error(
        "LibreOffice 변환 시간 초과(120초): a.doc"
    ) is False
    assert libreoffice.is_missing_libreoffice_error(
        "변환 결과 파일이 생성되지 않았습니다: a.doc → .docx"
    ) is False


def test_missing_source_file_raises_conversion_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: Path("soffice"))
    with pytest.raises(ConversionFailedError):
        libreoffice.convert(tmp_path / "없는파일.doc", "docx")


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"", ""),
        (b"normal ascii", "normal ascii"),
        ("한글 메시지".encode("utf-8"), "한글 메시지"),
        ("한글 메시지".encode("cp949"), "한글 메시지"),  # 한국어 Windows의 soffice 출력
        (b"\xff\xfe\x00 invalid", None),  # 깨져도 예외 없이 문자열 반환
    ],
)
def test_soffice_output_decoding_survives_non_utf8(raw, expected):
    """한국어 Windows에서 soffice는 CP949로 출력한다. UTF-8 고정 디코딩은 진단 정보를 잃는다."""
    decoded = libreoffice._decode_output(raw)
    assert isinstance(decoded, str)
    if expected is not None:
        assert decoded == expected


def test_conversion_failure_message_includes_soffice_detail(tmp_path):
    """변환 실패 시 soffice가 낸 사유가 메시지에 남아야 진단이 가능하다."""
    if not libreoffice.is_available():
        pytest.skip("LibreOffice 미설치")
    broken = tmp_path / "손상파일.doc"
    broken.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 200)

    with pytest.raises(ConversionFailedError) as excinfo:
        libreoffice.convert(broken, "docx", output_dir=tmp_path)
    assert "soffice:" in str(excinfo.value)


def test_legacy_parser_reports_failure_without_libreoffice(tmp_path, monkeypatch):
    """변환 실패는 예외로 삼키지 않고 status/errors로 전파되어야 한다."""
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: None)
    path = tmp_path / "구버전.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0")  # OLE 시그니처만 있는 더미

    document = parse_file(path)
    assert document.status is ParseStatus.FAILED
    assert document.errors
    assert document.chunks == []


# --- 실제 변환 (LibreOffice 필요) ----------------------------------------


@requires_libreoffice
@pytest.mark.parametrize(
    "source_key,legacy_ext,convert_to",
    [
        ("sample.docx", ".doc", "doc"),
        ("sample.xlsx", ".xls", "xls"),
        ("sample.pptx", ".ppt", "ppt"),
    ],
)
def test_legacy_formats_parse_via_conversion(samples, tmp_path, source_key, legacy_ext, convert_to):
    legacy = libreoffice.convert(samples[source_key], convert_to, output_dir=tmp_path)
    assert legacy.suffix == legacy_ext

    document = parse_file(legacy)
    assert document.status is not ParseStatus.FAILED, document.errors
    assert document.chunks
    assert document.file_name == legacy.name
    assert all(c.file_name == legacy.name for c in document.chunks)


@requires_libreoffice
def test_legacy_conversion_preserves_korean_and_table_structure(samples, tmp_path):
    """변환을 거쳐도 한글과 표 행·열 구조가 보존되어야 한다."""
    from tests.fixtures.generate_samples import TABLE_HEADER, TABLE_ROWS

    legacy = libreoffice.convert(samples["sample.docx"], "doc", output_dir=tmp_path)
    document = parse_file(legacy)

    tables = document.chunks_of(ChunkType.TABLE)
    assert len(tables) == 1
    assert tables[0].table.header_row == TABLE_HEADER
    assert tables[0].table.rows == TABLE_ROWS


@requires_libreoffice
@pytest.mark.parametrize(
    "sample_key,parser_path,location_field",
    [
        ("sample.pptx", "parser.formats.pptx_parser:PptxParser", "슬라이드"),
        ("sample.docx", "parser.formats.docx_parser:DocxParser", "쪽"),
    ],
)
def test_vector_shape_capture_produces_rendered_images(
    samples, tmp_path, sample_key, parser_path, location_field
):
    import importlib

    module_name, class_name = parser_path.split(":")
    parser_cls = getattr(importlib.import_module(module_name), class_name)

    document = parser_cls(asset_dir=tmp_path, capture_vector_shapes=True).parse(samples[sample_key])
    rendered = [c for c in document.chunks_of(ChunkType.IMAGE) if c.image.origin == "rendered"]
    assert rendered, "벡터 도형 캡처가 생성되지 않았습니다"
    for chunk in rendered:
        assert Path(chunk.image.image_path).is_file()
        assert chunk.page_or_slide is not None
        assert location_field in chunk.image.caption


@requires_libreoffice
def test_vector_capture_is_off_by_default(samples, tmp_path):
    """변환 비용이 크므로 기본값은 OFF여야 한다."""
    from parser.formats.pptx_parser import PptxParser

    document = PptxParser(asset_dir=tmp_path).parse(samples["sample.pptx"])
    assert not [c for c in document.chunks_of(ChunkType.IMAGE) if c.image.origin == "rendered"]


# --- HWP (실제 샘플 필요) ------------------------------------------------


def test_hwp_parses_real_sample(sample_hwp):
    document = parse_file(sample_hwp)
    assert document.status is not ParseStatus.FAILED, document.errors
    assert document.chunks
    assert document.title


def test_hwp_extracts_text(sample_hwp):
    document = parse_file(sample_hwp)
    texts = document.chunks_of(ChunkType.TEXT)
    assert texts, "본문 텍스트가 추출되지 않았습니다"
    assert sum(len(c.content) for c in texts) > 500


def test_hwp_tables_keep_structure(sample_hwp):
    """표는 행·열 구조를 유지해야 하며, 1행짜리 표도 데이터를 잃지 않아야 한다."""
    document = parse_file(sample_hwp)
    tables = document.chunks_of(ChunkType.TABLE)
    assert tables, "표가 추출되지 않았습니다"
    for chunk in tables:
        assert chunk.table is not None
        assert chunk.table.rows, "표 데이터 행이 비어 있습니다"
        assert all(isinstance(row, list) for row in chunk.table.rows)


def test_hwp_extracts_bindata_images(sample_hwp):
    """BinData 항목은 확장자가 .tmp라 시그니처로 판별해야 한다."""
    document = parse_file(sample_hwp)
    images = document.chunks_of(ChunkType.IMAGE)
    assert images, "BinData 이미지가 추출되지 않았습니다"
    for chunk in images:
        path = Path(chunk.image.image_path)
        assert path.is_file()
        assert path.suffix != ".tmp", "실제 이미지 형식으로 확장자가 교정되지 않았습니다"
        assert sniff_image_extension(path.read_bytes()) is not None


def test_hwp_handles_korean_filename(sample_hwp):
    document = parse_file(sample_hwp)
    assert document.file_name == sample_hwp.name
    assert all(c.file_name == sample_hwp.name for c in document.chunks)


def test_hwp_rejects_invalid_file(tmp_path):
    from parser import DocumentReadError

    path = tmp_path / "broken.hwp"
    path.write_bytes(b"not an OLE compound file")
    with pytest.raises(DocumentReadError):
        parse_file(path)

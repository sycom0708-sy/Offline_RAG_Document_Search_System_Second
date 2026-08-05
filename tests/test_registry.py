"""확장자 라우팅 및 예외 처리 테스트."""

from __future__ import annotations

import pytest

from parser import DocumentReadError, UnsupportedFormatError, get_parser_class, is_supported, parse_file
from parser.formats.docx_parser import DocxParser
from parser.formats.hwp_parser import HwpParser
from parser.formats.hwpx_parser import HwpxParser
from parser.formats.legacy_parser import LegacyOfficeParser
from parser.formats.pdf_parser import PdfParser
from parser.formats.pptx_parser import PptxParser
from parser.formats.txt_parser import TxtParser
from parser.formats.xlsx_parser import XlsxParser

# PRD 5.1절이 요구하는 9종 형식
REQUIRED_EXTENSIONS = [
    (".doc", LegacyOfficeParser),
    (".docx", DocxParser),
    (".pdf", PdfParser),
    (".xls", LegacyOfficeParser),
    (".xlsx", XlsxParser),
    (".ppt", LegacyOfficeParser),
    (".pptx", PptxParser),
    (".txt", TxtParser),
    (".hwp", HwpParser),
    (".hwpx", HwpxParser),
]


@pytest.mark.parametrize("extension,expected", REQUIRED_EXTENSIONS)
def test_required_formats_route_to_expected_parser(extension, expected):
    assert get_parser_class(f"doc{extension}") is expected


@pytest.mark.parametrize("extension,_expected", REQUIRED_EXTENSIONS)
def test_required_formats_are_supported(extension, _expected):
    assert is_supported(f"doc{extension}")


def test_extension_matching_is_case_insensitive():
    assert get_parser_class("DOC.PDF") is PdfParser


def test_unsupported_extension_raises():
    with pytest.raises(UnsupportedFormatError):
        get_parser_class("archive.zip")


def test_missing_file_raises_read_error(tmp_path):
    with pytest.raises(DocumentReadError):
        parse_file(tmp_path / "없는파일.txt")


def test_same_path_yields_stable_doc_id(sample_txt):
    assert parse_file(sample_txt).doc_id == parse_file(sample_txt).doc_id

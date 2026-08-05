"""확장자 → 파서 매핑 및 파싱 진입점."""

from __future__ import annotations

from pathlib import Path

from parser.base import BaseParser, UnsupportedFormatError
from parser.formats.docx_parser import DocxParser
from parser.formats.hwp_parser import HwpParser
from parser.formats.hwpx_parser import HwpxParser
from parser.formats.legacy_parser import LegacyOfficeParser
from parser.formats.pdf_parser import PdfParser
from parser.formats.pptx_parser import PptxParser
from parser.formats.txt_parser import TxtParser
from parser.formats.xlsx_parser import XlsxParser
from parser.schema import ParsedDocument

PARSER_CLASSES: tuple[type[BaseParser], ...] = (
    TxtParser,
    PdfParser,
    DocxParser,
    XlsxParser,
    PptxParser,
    HwpParser,
    HwpxParser,
    LegacyOfficeParser,
)

_REGISTRY: dict[str, type[BaseParser]] = {
    ext: parser_cls for parser_cls in PARSER_CLASSES for ext in parser_cls.extensions
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(_REGISTRY)


def get_parser_class(file_path: str | Path) -> type[BaseParser]:
    suffix = Path(file_path).suffix.lower()
    parser_cls = _REGISTRY.get(suffix)
    if parser_cls is None:
        raise UnsupportedFormatError(f"지원하지 않는 형식입니다: {suffix or '(확장자 없음)'}")
    return parser_cls


def is_supported(file_path: str | Path) -> bool:
    return Path(file_path).suffix.lower() in _REGISTRY


def parse_file(file_path: str | Path, **parser_kwargs) -> ParsedDocument:
    """파일을 형식에 맞는 파서로 파싱한다.

    UnsupportedFormatError / DocumentReadError는 그대로 전파된다.
    변환 실패처럼 부분적으로 진행 가능한 경우는 ParsedDocument.status로 표현된다.
    """
    parser_cls = get_parser_class(file_path)
    return parser_cls(**parser_kwargs).parse(file_path)

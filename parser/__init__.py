"""문서 파서 모듈 (Phase 1).

형식별 파서는 모두 동일한 청크 스키마(TECH 4.2절)로 결과를 반환한다.
"""

from parser.base import (
    BaseParser,
    DocumentReadError,
    ParserError,
    UnsupportedFormatError,
)
from parser.registry import (
    SUPPORTED_EXTENSIONS,
    get_parser_class,
    is_supported,
    parse_file,
)
from parser.schema import (
    Chunk,
    ChunkType,
    ImageData,
    ParseStatus,
    ParsedDocument,
    TableData,
)

__all__ = [
    "BaseParser",
    "Chunk",
    "ChunkType",
    "DocumentReadError",
    "ImageData",
    "ParseStatus",
    "ParsedDocument",
    "ParserError",
    "SUPPORTED_EXTENSIONS",
    "TableData",
    "UnsupportedFormatError",
    "get_parser_class",
    "is_supported",
    "parse_file",
]

"""파서 공통 기반 클래스 및 예외."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from parser.schema import (
    Chunk,
    ChunkType,
    ImageData,
    ParseStatus,
    ParsedDocument,
    TableData,
)
from parser.utils.hashing import file_mtime, file_sha256
from parser.utils.ids import make_chunk_id, make_doc_id


class ParserError(Exception):
    """파싱 실패의 최상위 예외."""


class UnsupportedFormatError(ParserError):
    """등록된 파서가 없는 확장자."""


class DocumentReadError(ParserError):
    """파일을 열거나 읽는 데 실패."""


class BaseParser(ABC):
    """형식별 파서의 공통 골격.

    상속 클래스는 `extensions`와 `_parse()`만 구현하면 되고,
    doc_id/chunk_id 발급·해시·mtime 기록은 여기서 일괄 처리한다.
    """

    extensions: tuple[str, ...] = ()

    def __init__(self, asset_dir: str | Path | None = None) -> None:
        # 추출·캡처된 이미지를 저장할 위치. 지정하지 않으면 원본 파일 옆 .assets 폴더.
        self._asset_dir = Path(asset_dir) if asset_dir else None

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path)
        if not path.is_file():
            raise DocumentReadError(f"파일을 찾을 수 없습니다: {path}")

        doc_id = make_doc_id(path)
        document = ParsedDocument(
            doc_id=doc_id,
            file_path=str(path.resolve()),
            file_name=path.name,
            title=path.stem,
            source_mtime=file_mtime(path),
            source_hash=file_sha256(path),
        )
        self._counters: dict[str, int] = {}
        self._parse(path, document)
        self._stamp_source_info(document)
        return document

    @abstractmethod
    def _parse(self, path: Path, document: ParsedDocument) -> None:
        """파싱 결과를 document.chunks에 채운다."""

    def asset_dir_for(self, path: Path) -> Path:
        target = self._asset_dir or path.parent / ".assets" / path.stem
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _next_ordinal(self, chunk_type: ChunkType) -> int:
        key = chunk_type.value
        ordinal = self._counters.get(key, 0)
        self._counters[key] = ordinal + 1
        return ordinal

    def _stamp_source_info(self, document: ParsedDocument) -> None:
        for chunk in document.chunks:
            chunk.source_mtime = document.source_mtime
            chunk.source_hash = document.source_hash

    def make_text_chunk(
        self,
        document: ParsedDocument,
        content: str,
        page_or_slide: int | None = None,
        keywords: list[str] | None = None,
        heading: str = "",
    ) -> Chunk:
        return Chunk(
            chunk_id=make_chunk_id(document.doc_id, "text", self._next_ordinal(ChunkType.TEXT)),
            doc_id=document.doc_id,
            file_path=document.file_path,
            file_name=document.file_name,
            type=ChunkType.TEXT,
            page_or_slide=page_or_slide,
            content=content,
            heading=heading,
            keywords=keywords or [],
        )

    def make_table_chunk(
        self,
        document: ParsedDocument,
        table: TableData,
        page_or_slide: int | None = None,
        heading: str = "",
    ) -> Chunk:
        # 캡션·헤더는 벡터 유사도만으로 잡히기 어려워 키워드로도 함께 남긴다 (TECH 4.3절).
        keywords = [kw for kw in ([table.caption] + table.header_row) if kw]
        return Chunk(
            chunk_id=make_chunk_id(document.doc_id, "table", self._next_ordinal(ChunkType.TABLE)),
            doc_id=document.doc_id,
            file_path=document.file_path,
            file_name=document.file_name,
            type=ChunkType.TABLE,
            page_or_slide=page_or_slide,
            content=table.to_text(),
            heading=heading,
            keywords=keywords,
            table=table,
        )

    def make_image_chunk(
        self,
        document: ParsedDocument,
        image: ImageData,
        page_or_slide: int | None = None,
        heading: str = "",
    ) -> Chunk:
        keywords = [kw for kw in [image.caption, image.origin] if kw]
        return Chunk(
            chunk_id=make_chunk_id(document.doc_id, "image", self._next_ordinal(ChunkType.IMAGE)),
            doc_id=document.doc_id,
            file_path=document.file_path,
            file_name=document.file_name,
            type=ChunkType.IMAGE,
            page_or_slide=page_or_slide,
            content=image.caption or Path(image.image_path).name,
            heading=heading,
            keywords=keywords,
            image=image,
        )

    @staticmethod
    def record_error(document: ParsedDocument, message: str) -> None:
        document.errors.append(message)
        if document.status is ParseStatus.OK:
            document.status = ParseStatus.PARTIAL

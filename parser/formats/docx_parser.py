"""DOCX 파서 (T1.3) — python-docx 기반 문단/표/이미지 분리 추출."""

from __future__ import annotations

from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from parser.base import BaseParser, DocumentReadError
from parser.schema import ImageData, ParsedDocument, TableData
from parser.utils.libreoffice import LibreOfficeError
from parser.utils.render import render_pages

_IMAGE_CONTENT_PREFIX = "image/"
# 제목 길이 상한 (T10.31) — PDF 쪽과 같은 기준으로 맞춘다.
_MAX_HEADING_CHARS = 40


class DocxParser(BaseParser):
    extensions = (".docx",)

    def __init__(self, asset_dir: str | Path | None = None, capture_vector_shapes: bool = False) -> None:
        super().__init__(asset_dir)
        # 벡터 도형 캡처는 LibreOffice 변환을 거쳐 느리므로 기본 OFF.
        self._capture_vector_shapes = capture_vector_shapes

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        try:
            source = docx.Document(str(path))
        except Exception as exc:
            raise DocumentReadError(f"DOCX를 열 수 없습니다: {path.name} ({exc})") from exc

        document.title = self._extract_title(source) or path.stem
        asset_dir = self.asset_dir_for(path)

        paragraph_buffer: list[str] = []
        for block in self._iter_blocks(source):
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if text:
                    paragraph_buffer.append(text)
            else:
                # 표를 만나면 앞선 문단을 먼저 확정해 텍스트/표 청크가 섞이지 않게 한다.
                self._flush_text(document, paragraph_buffer)
                table_data = self._read_table(block)
                if table_data is not None:
                    document.chunks.append(self.make_table_chunk(document, table_data))

        self._flush_text(document, paragraph_buffer)
        self._extract_images(document, source, asset_dir)
        self._capture_drawings(document, path, asset_dir)

    @staticmethod
    def _iter_blocks(source: DocxDocument):
        """본문 요소를 문서 순서대로 순회한다 (문단/표 위치 관계 보존)."""
        body = source.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, source)
            elif child.tag == qn("w:tbl"):
                yield Table(child, source)

    def _flush_text(
        self, document: ParsedDocument, buffer: list[str], heading: str = ""
    ) -> None:
        body = "\n".join(buffer).strip()
        if body:
            document.chunks.append(self.make_text_chunk(document, body, heading=heading))
        buffer.clear()

    @staticmethod
    def _is_heading(paragraph: Paragraph) -> bool:
        """Heading/Title 스타일 문단인가 (T10.31).

        `_extract_title()`이 문서 제목을 찾을 때 쓰는 것과 같은 판정이지만,
        이쪽은 **문서 전체가 아니라 지금 지나온 절**을 추적한다.
        """
        style = getattr(paragraph, "style", None)
        name = getattr(style, "name", "") or ""
        return name.startswith("Heading") or name.startswith("Title")

    @staticmethod
    def _read_table(table: Table) -> TableData | None:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        return TableData.from_rows(rows)

    def _extract_images(self, document: ParsedDocument, source: DocxDocument, asset_dir: Path) -> None:
        for index, part in enumerate(source.part.related_parts.values()):
            content_type = getattr(part, "content_type", "") or ""
            if not content_type.startswith(_IMAGE_CONTENT_PREFIX):
                continue
            try:
                blob = part.blob
            except Exception as exc:
                self.record_error(document, f"이미지 추출 실패({part.partname}): {exc}")
                continue

            ext = Path(str(part.partname)).suffix or ".png"
            out_path = asset_dir / f"img{index:02d}{ext}"
            out_path.write_bytes(blob)
            document.chunks.append(
                self.make_image_chunk(
                    document,
                    ImageData(image_path=str(out_path), origin="extracted"),
                )
            )

    def _capture_drawings(self, document: ParsedDocument, path: Path, asset_dir: Path) -> None:
        if not self._capture_vector_shapes:
            return
        try:
            captured = render_pages(path, asset_dir)
        except LibreOfficeError as exc:
            self.record_error(document, f"벡터 도형 캡처 실패: {exc}")
            return

        for page_no, image_path in sorted(captured.items()):
            document.chunks.append(
                self.make_image_chunk(
                    document,
                    ImageData(
                        image_path=str(image_path),
                        caption=f"{page_no}쪽 도면 캡처",
                        origin="rendered",
                    ),
                    page_or_slide=page_no,
                )
            )

    @staticmethod
    def _extract_title(source: DocxDocument) -> str:
        core_title = (source.core_properties.title or "").strip()
        if core_title:
            return core_title
        for paragraph in source.paragraphs:
            text = paragraph.text.strip()
            if text and paragraph.style is not None and paragraph.style.name.startswith("Heading"):
                return text[:200]
        for paragraph in source.paragraphs:
            if paragraph.text.strip():
                return paragraph.text.strip()[:200]
        return ""

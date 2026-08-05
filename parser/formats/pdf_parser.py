"""PDF 파서 (T1.2) — PyMuPDF 기반 텍스트/표/이미지 추출."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from parser.base import BaseParser, DocumentReadError
from parser.schema import ImageData, ParsedDocument, TableData

# 렌더링 캡처 해상도. 72dpi 기준 2배 = 144dpi로 썸네일·확대 보기 모두 감당 가능한 수준.
_RENDER_ZOOM = 2.0
# 이보다 작은 도형 묶음은 밑줄·표 괘선 같은 장식일 가능성이 높아 다이어그램으로 보지 않는다.
_MIN_DRAWING_CLUSTER_AREA = 10000.0
_MIN_DRAWING_COUNT = 5


class PdfParser(BaseParser):
    extensions = (".pdf",)

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        try:
            pdf = pymupdf.open(path)
        except Exception as exc:
            raise DocumentReadError(f"PDF를 열 수 없습니다: {path.name} ({exc})") from exc

        with pdf:
            title = (pdf.metadata or {}).get("title") or ""
            document.title = title.strip() or path.stem
            asset_dir = self.asset_dir_for(path)

            for page_index, page in enumerate(pdf):
                page_no = page_index + 1
                table_bboxes = self._extract_tables(document, page, page_no)
                self._extract_text(document, page, page_no, table_bboxes)
                extracted_count = self._extract_images(document, pdf, page, page_no, asset_dir)
                self._capture_vector_drawings(
                    document, page, page_no, asset_dir, extracted_count, bool(table_bboxes)
                )

    def _extract_tables(
        self, document: ParsedDocument, page: pymupdf.Page, page_no: int
    ) -> list[pymupdf.Rect]:
        bboxes: list[pymupdf.Rect] = []
        try:
            found = page.find_tables()
        except Exception as exc:
            self.record_error(document, f"{page_no}쪽 표 인식 실패: {exc}")
            return bboxes

        for table in found.tables:
            rows = [
                [(cell or "").strip() for cell in row]
                for row in table.extract()
            ]
            table_data = TableData.from_rows(rows)
            if table_data is None:
                continue

            bboxes.append(pymupdf.Rect(table.bbox))
            document.chunks.append(
                self.make_table_chunk(document, table_data, page_or_slide=page_no)
            )
        return bboxes

    def _extract_text(
        self,
        document: ParsedDocument,
        page: pymupdf.Page,
        page_no: int,
        table_bboxes: list[pymupdf.Rect],
    ) -> None:
        # 표 영역 텍스트가 본문 청크에 섞이면 행·열 구조가 소실되므로 제외한다 (TECH 3.1절).
        lines: list[str] = []
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
            if not text or not text.strip():
                continue
            block_rect = pymupdf.Rect(x0, y0, x1, y1)
            if any(self._mostly_inside(block_rect, bbox) for bbox in table_bboxes):
                continue
            lines.append(text.strip())

        body = "\n".join(lines).strip()
        if body:
            document.chunks.append(self.make_text_chunk(document, body, page_or_slide=page_no))

    def _extract_images(
        self,
        document: ParsedDocument,
        pdf: pymupdf.Document,
        page: pymupdf.Page,
        page_no: int,
        asset_dir: Path,
    ) -> int:
        count = 0
        for image_index, image_info in enumerate(page.get_images(full=True)):
            xref = image_info[0]
            try:
                extracted = pdf.extract_image(xref)
            except Exception as exc:
                self.record_error(document, f"{page_no}쪽 이미지 추출 실패(xref={xref}): {exc}")
                continue

            ext = extracted.get("ext", "png")
            out_path = asset_dir / f"p{page_no:04d}_img{image_index:02d}.{ext}"
            out_path.write_bytes(extracted["image"])
            document.chunks.append(
                self.make_image_chunk(
                    document,
                    ImageData(
                        image_path=str(out_path),
                        width=extracted.get("width", 0),
                        height=extracted.get("height", 0),
                        origin="extracted",
                    ),
                    page_or_slide=page_no,
                )
            )
            count += 1
        return count

    def _capture_vector_drawings(
        self,
        document: ParsedDocument,
        page: pymupdf.Page,
        page_no: int,
        asset_dir: Path,
        extracted_count: int,
        has_table: bool,
    ) -> None:
        """벡터 도형 다이어그램은 이미지로 추출되지 않으므로 페이지를 렌더링해 캡처한다 (TECH 3.1절)."""
        if extracted_count or not self._has_meaningful_drawings(page, has_table):
            return

        try:
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(_RENDER_ZOOM, _RENDER_ZOOM))
        except Exception as exc:
            self.record_error(document, f"{page_no}쪽 렌더링 캡처 실패: {exc}")
            return

        out_path = asset_dir / f"p{page_no:04d}_render.png"
        pixmap.save(out_path)
        document.chunks.append(
            self.make_image_chunk(
                document,
                ImageData(
                    image_path=str(out_path),
                    caption=f"{page_no}쪽 도면 캡처",
                    width=pixmap.width,
                    height=pixmap.height,
                    origin="rendered",
                ),
                page_or_slide=page_no,
            )
        )

    @staticmethod
    def _has_meaningful_drawings(page: pymupdf.Page, has_table: bool) -> bool:
        drawings = page.get_drawings()
        if len(drawings) < _MIN_DRAWING_COUNT:
            return False
        # 표가 있는 페이지는 괘선이 도형으로 잡히므로, 표와 무관한 큰 도형 묶음이 있을 때만 캡처한다.
        clusters = page.cluster_drawings()
        threshold = _MIN_DRAWING_CLUSTER_AREA * (3 if has_table else 1)
        return any(abs(rect.get_area()) >= threshold for rect in clusters)

    @staticmethod
    def _mostly_inside(block: pymupdf.Rect, container: pymupdf.Rect) -> bool:
        overlap = block & container
        if overlap.is_empty or block.get_area() <= 0:
            return False
        return overlap.get_area() / block.get_area() > 0.5

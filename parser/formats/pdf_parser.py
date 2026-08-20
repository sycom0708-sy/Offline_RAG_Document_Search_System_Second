"""PDF 파서 (T1.2) — PyMuPDF 기반 텍스트/표/이미지 추출."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from parser.base import BaseParser, DocumentReadError
from parser.schema import ImageData, ParsedDocument, TableData
from parser.utils.headings import clean_heading, pick_largest_line

# 렌더링 캡처 해상도. 72dpi 기준 2배 = 144dpi로 썸네일·확대 보기 모두 감당 가능한 수준.
_RENDER_ZOOM = 2.0
# 이보다 작은 도형 묶음은 밑줄·표 괘선 같은 장식일 가능성이 높아 다이어그램으로 보지 않는다.
_MIN_DRAWING_CLUSTER_AREA = 10000.0
_MIN_DRAWING_COUNT = 5

# 표 안에 섞인 절 제목 행 패턴 — "4.2 업무개시 수신 응답 ..." 같은 번호+텍스트.
# `headings.py`가 페이지 단위 제목에는 번호 패턴을 일부러 안 쓰는 것과 달리
# (날짜 오탐), 여기서는 "표의 고립된 행"이라는 구조적 조건과 함께 써 오탐
# 위험을 좁힌다 — 실측: 기아차 앱미터기 결제 프로토콜정의서(PDF)가 여러 절의
# 표를 하나의 연속된 표로 그리면서 절 제목을 표 행으로 끼워 넣었다.
_TABLE_HEADING_PATTERN = re.compile(r"^\d+(\.\d+)+\s+\S.*$")


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
                heading = self._page_heading(page)
                table_bboxes = self._extract_tables(document, page, page_no, heading)
                self._extract_text(document, page, page_no, table_bboxes, heading)
                extracted_count = self._extract_images(
                    document, pdf, page, page_no, asset_dir, heading
                )
                self._capture_vector_drawings(
                    document, page, page_no, asset_dir, extracted_count,
                    bool(table_bboxes), heading,
                )

    def _page_heading(self, page: pymupdf.Page) -> str:
        """이 페이지의 제목을 **가장 큰 글꼴**로 판별한다 (T10.31).

        🔴 텍스트 패턴(`1-1.` 같은 번호)으로 찾지 않는다. 이 코퍼스의 PDF는
        페이지 전체가 줄바꿈 없는 한 덩어리로 추출돼(실측: text 청크 972개 중
        952개가 단일 줄) "짧은 줄"이라는 단서가 아예 없고, 번호 패턴은 날짜
        (`2021. 04`)를 제목으로 오인한다. 반면 글꼴 크기는 문서가 실제로 가진
        구조라 훨씬 안정적이다 — 실측: AICA 안내서 3쪽에서 제목
        "1-1. AICA 취득 절차"가 20pt, 본문이 전부 14pt였다.

        본문과 크기 차이가 뚜렷할 때만(`HEADING_SIZE_RATIO`) 제목으로 본다 —
        글꼴이 균일한 문서(보고서·논문 등)에서 아무 줄이나 제목으로 올리면
        노이즈만 된다. 그런 문서는 빈 문자열이 되고 카드에 제목 줄이 안 뜬다.
        """
        try:
            blocks = page.get_text("dict")["blocks"]
        except Exception:
            return ""  # 제목은 부가 정보다 — 실패해도 본문 추출을 막지 않는다

        # (글꼴 크기 → 그 크기로 쓰인 텍스트) 를 모은다. 같은 크기의 조각이
        # 여러 span으로 쪼개져 있으므로(실측: "I." + "AICA 소개") 줄 단위로 잇는다.
        sized_lines: list[tuple[float, str]] = []
        for block in blocks:
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                size = max(round(s["size"], 1) for s in spans)
                text = "".join(s["text"] for s in spans).strip()
                if text:
                    sized_lines.append((size, text))

        return pick_largest_line(sized_lines)

    def _extract_tables(
        self, document: ParsedDocument, page: pymupdf.Page, page_no: int, heading: str = ""
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
            segments = self._split_table_rows_on_heading(rows)
            if not segments:
                continue

            bboxes.append(pymupdf.Rect(table.bbox))
            current_heading = heading
            for heading_override, segment_rows in segments:
                if heading_override:
                    current_heading = heading_override
                table_data = TableData.from_rows(segment_rows)
                if table_data is None:
                    continue
                document.chunks.append(
                    self.make_table_chunk(
                        document, table_data, page_or_slide=page_no, heading=current_heading
                    )
                )
        return bboxes

    @staticmethod
    def _split_table_rows_on_heading(
        rows: list[list[str]],
    ) -> list[tuple[str, list[list[str]]]]:
        """표 안에 섞인 절 제목 행을 기준으로 표를 여러 구간으로 쪼갠다.

        첫 칸만 채워지고 나머지 칸은 전부 빈, 구조적으로 고립된 행만 후보로
        보고, 그 첫 칸이 `_TABLE_HEADING_PATTERN`에 맞을 때만 제목으로
        인정한다. 그런 행이 없으면 전체가 구간 하나로 그대로 돌아온다
        (기존 동작과 동일).
        """
        segments: list[tuple[str, list[list[str]]]] = []
        current: list[list[str]] = []
        pending_heading = ""

        for row in rows:
            candidate = PdfParser._table_heading_candidate(row)
            if candidate:
                if current:
                    segments.append((pending_heading, current))
                current = []
                pending_heading = candidate
                continue
            current.append(row)

        if current:
            segments.append((pending_heading, current))

        return segments

    @staticmethod
    def _table_heading_candidate(row: list[str]) -> str:
        if not row:
            return ""
        first, *rest = row
        first = (first or "").strip()
        if not first or any((cell or "").strip() for cell in rest):
            return ""
        if not _TABLE_HEADING_PATTERN.match(first):
            return ""
        return clean_heading(first)

    def _extract_text(
        self,
        document: ParsedDocument,
        page: pymupdf.Page,
        page_no: int,
        table_bboxes: list[pymupdf.Rect],
        heading: str = "",
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
            document.chunks.append(
                self.make_text_chunk(document, body, page_or_slide=page_no, heading=heading)
            )

    def _extract_images(
        self,
        document: ParsedDocument,
        pdf: pymupdf.Document,
        page: pymupdf.Page,
        page_no: int,
        asset_dir: Path,
        heading: str = "",
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
                    heading=heading,
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
        heading: str = "",
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
                heading=heading,
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

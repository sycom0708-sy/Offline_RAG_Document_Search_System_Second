"""XLSX 파서 (T1.5) — openpyxl 기반 시트별 표 추출."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from parser.base import BaseParser, DocumentReadError
from parser.schema import ImageData, ParsedDocument, TableData
from parser.utils.headings import clean_heading


def _squash(text: str) -> str:
    """시트명 대조용 정규화 — 공백·구분기호 차이만으로 "다르다"고 보지 않는다.

    실측: 시트명 `1-1.요금 정책 조회(신규)` ↔ 1행 `1-2.요금 정책 조회(신규)`처럼
    번호만 어긋난 경우는 실제로 다른 정보라 남기고, `2.주행 시작` ↔ `2.주행 시작`
    같은 완전 일치만 지운다.
    """
    return "".join(ch for ch in text if not ch.isspace())


class XlsxParser(BaseParser):
    extensions = (".xlsx", ".xlsm")

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        try:
            # data_only=True로 수식 대신 계산된 값을 읽는다 (검색 대상은 결과값).
            workbook = openpyxl.load_workbook(path, data_only=True)
        except Exception as exc:
            raise DocumentReadError(f"XLSX를 열 수 없습니다: {path.name} ({exc})") from exc

        document.title = (workbook.properties.title or "").strip() or path.stem
        asset_dir = self.asset_dir_for(path)

        try:
            for sheet_index, sheet in enumerate(workbook.worksheets, start=1):
                table_data = self._read_sheet(sheet)
                heading = self._sheet_heading(table_data, sheet.title)
                if table_data is not None:
                    document.chunks.append(
                        self.make_table_chunk(
                            document, table_data, page_or_slide=sheet_index, heading=heading
                        )
                    )
                self._extract_images(document, sheet, sheet_index, asset_dir, heading)
        finally:
            workbook.close()

    @staticmethod
    def _read_sheet(sheet) -> TableData | None:
        rows = [
            ["" if value is None else str(value).strip() for value in row]
            for row in sheet.iter_rows(values_only=True)
        ]
        return TableData.from_rows(rows, caption=sheet.title)

    @staticmethod
    def _sheet_heading(table: TableData | None, sheet_title: str) -> str:
        """시트 1행의 **제목 칸**을 절 제목으로 쓴다 (T10.32).

        표 카드는 이미 시트명을 위치로 보여주므로(Phase 5), 같은 문자열을 제목
        줄에 한 번 더 띄우면 두 줄이 겹쳐 보인다. 그래서 **시트명과 사실상 같으면
        비운다**[사용자 확정] — 시트명이 `Sheet1`처럼 무의미한 문서에서만 값이 남는다.

        1행에 채워진 칸이 **하나뿐**일 때만 제목으로 본다. 여러 칸이 차 있으면
        그건 제목이 아니라 열 머리글이고, 열 머리글은 표 카드가 이미 격자로
        보여주고 있다(실측: 이 코퍼스의 API 정의서 시트들은 1행이
        `2.주행 시작` 한 칸이었다).
        """
        if table is None or not table.header_row:
            return ""

        filled = [cell.strip() for cell in table.header_row if cell and cell.strip()]
        if len(filled) != 1:
            return ""

        heading = clean_heading(filled[0])
        if _squash(heading) == _squash(sheet_title):
            return ""
        return heading

    def _extract_images(
        self, document: ParsedDocument, sheet, sheet_index: int, asset_dir: Path,
        heading: str = "",
    ) -> None:
        for image_index, image in enumerate(getattr(sheet, "_images", [])):
            try:
                blob = image.ref.getvalue() if hasattr(image.ref, "getvalue") else Path(image.ref).read_bytes()
            except Exception as exc:
                self.record_error(document, f"'{sheet.title}' 시트 이미지 추출 실패: {exc}")
                continue

            out_path = asset_dir / f"sheet{sheet_index:02d}_img{image_index:02d}.png"
            out_path.write_bytes(blob)
            document.chunks.append(
                self.make_image_chunk(
                    document,
                    ImageData(
                        image_path=str(out_path),
                        caption=f"{sheet.title} 시트 이미지",
                        origin="extracted",
                    ),
                    page_or_slide=sheet_index,
                    heading=heading,
                )
            )

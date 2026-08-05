"""XLSX 파서 (T1.5) — openpyxl 기반 시트별 표 추출."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from parser.base import BaseParser, DocumentReadError
from parser.schema import ImageData, ParsedDocument, TableData


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
                if table_data is not None:
                    document.chunks.append(
                        self.make_table_chunk(document, table_data, page_or_slide=sheet_index)
                    )
                self._extract_images(document, sheet, sheet_index, asset_dir)
        finally:
            workbook.close()

    @staticmethod
    def _read_sheet(sheet) -> TableData | None:
        rows = [
            ["" if value is None else str(value).strip() for value in row]
            for row in sheet.iter_rows(values_only=True)
        ]
        return TableData.from_rows(rows, caption=sheet.title)

    def _extract_images(self, document: ParsedDocument, sheet, sheet_index: int, asset_dir: Path) -> None:
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
                )
            )

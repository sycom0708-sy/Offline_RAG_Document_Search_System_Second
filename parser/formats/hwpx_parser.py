"""HWPX 파서 (T1.8) — zip+XML 자체 파싱.

HWPX는 OWPML 기반의 zip+XML 구조로, 본문은 Contents/section*.xml에 담긴다.
버전에 따라 네임스페이스 URI가 달라지므로 태그의 지역명(local name)만으로 판별한다.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from parser.base import BaseParser, DocumentReadError
from parser.schema import ImageData, ParsedDocument, TableData
from parser.utils.imaging import sniff_image_extension

_SECTION_PATTERN = re.compile(r"^Contents/section\d+\.xml$", re.IGNORECASE)
_BINDATA_PREFIX = "BinData/"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


class HwpxParser(BaseParser):
    extensions = (".hwpx",)

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        try:
            archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise DocumentReadError(f"HWPX를 열 수 없습니다: {path.name} ({exc})") from exc

        with archive:
            sections = sorted(name for name in archive.namelist() if _SECTION_PATTERN.match(name))
            if not sections:
                raise DocumentReadError(f"HWPX 본문(Contents/section*.xml)을 찾을 수 없습니다: {path.name}")

            for section_index, section_name in enumerate(sections, start=1):
                try:
                    root = ElementTree.fromstring(archive.read(section_name))
                except ElementTree.ParseError as exc:
                    self.record_error(document, f"{section_name} 파싱 실패: {exc}")
                    continue

                buffer: list[str] = []
                self._walk(root, document, buffer, section_index)
                self._flush_text(document, buffer, section_index)

            self._extract_images(document, archive, self.asset_dir_for(path))

        first_text = next(
            (c.content for c in document.chunks if c.type.value == "text" and c.content.strip()), ""
        )
        document.title = first_text.splitlines()[0][:200] if first_text else path.stem

    def _walk(
        self,
        element: ElementTree.Element,
        document: ParsedDocument,
        buffer: list[str],
        section_index: int,
    ) -> None:
        for child in element:
            tag = _local_name(child.tag)
            if tag == "tbl":
                # 표를 만나면 앞선 문단을 먼저 확정해 텍스트/표 청크가 섞이지 않게 한다.
                self._flush_text(document, buffer, section_index)
                table_data = self._read_table(child)
                if table_data is not None:
                    document.chunks.append(
                        self.make_table_chunk(document, table_data, page_or_slide=section_index)
                    )
                continue

            if tag == "p":
                text = self._own_text(child)
                if text:
                    buffer.append(text)

            self._walk(child, document, buffer, section_index)

    def _flush_text(self, document: ParsedDocument, buffer: list[str], section_index: int) -> None:
        body = "\n".join(buffer).strip()
        if body:
            document.chunks.append(
                self.make_text_chunk(document, body, page_or_slide=section_index)
            )
        buffer.clear()

    def _read_table(self, table_element: ElementTree.Element) -> TableData | None:
        rows: list[list[str]] = []
        for child in table_element:
            if _local_name(child.tag) != "tr":
                continue
            cells = [
                self._own_text(cell)
                for cell in child
                if _local_name(cell.tag) == "tc"
            ]
            rows.append(cells)

        return TableData.from_rows(rows)

    @classmethod
    def _own_text(cls, element: ElementTree.Element) -> str:
        """중첩된 표에 속한 텍스트는 제외하고 해당 요소가 직접 담고 있는 본문만 모은다."""
        parts: list[str] = []
        for child in element:
            tag = _local_name(child.tag)
            if tag == "tbl":
                continue
            if tag == "t" and child.text:
                parts.append(child.text)
            nested = cls._own_text(child)
            if nested:
                parts.append(nested)
        return "".join(parts).strip()

    def _extract_images(
        self, document: ParsedDocument, archive: zipfile.ZipFile, asset_dir: Path
    ) -> None:
        for name in archive.namelist():
            if not name.startswith(_BINDATA_PREFIX) or name.endswith("/"):
                continue
            try:
                blob = archive.read(name)
            except Exception as exc:
                self.record_error(document, f"이미지 추출 실패({name}): {exc}")
                continue

            # 확장자가 없거나 실제 내용과 다른 경우가 있어 시그니처로 판별한다.
            extension = sniff_image_extension(blob)
            if extension is None:
                continue

            out_path = (asset_dir / Path(name).name).with_suffix(f".{extension}")
            out_path.write_bytes(blob)
            document.chunks.append(
                self.make_image_chunk(
                    document,
                    ImageData(image_path=str(out_path), origin="extracted"),
                )
            )

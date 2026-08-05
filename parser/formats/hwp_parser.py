"""HWP 파서 (T1.7) — pyhwp 기반.

pyhwp의 중간 XML(xhwp5)을 거쳐 문단·표를 구조 그대로 읽고,
BinData 스토리지에서 삽입 이미지를 추출한다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from xml.etree import ElementTree

from parser.base import BaseParser, DocumentReadError
from parser.schema import ImageData, ParsedDocument, TableData
from parser.utils.imaging import sniff_image_extension


class HwpParser(BaseParser):
    extensions = (".hwp",)

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        root = self._load_xml(path)
        asset_dir = self.asset_dir_for(path)

        text_buffer: list[str] = []
        self._walk(root, document, text_buffer)
        self._flush_text(document, text_buffer)
        self._extract_images(document, path, asset_dir)

        first_text = next(
            (c.content for c in document.chunks if c.type.value == "text" and c.content.strip()), ""
        )
        document.title = first_text.splitlines()[0][:200] if first_text else path.stem

    def _load_xml(self, path: Path) -> ElementTree.Element:
        try:
            from hwp5.xmlmodel import Hwp5File
        except ImportError as exc:
            raise DocumentReadError(
                "pyhwp가 설치되어 있지 않아 HWP를 파싱할 수 없습니다. "
                "`pip install pyhwp six`로 설치하세요."
            ) from exc

        try:
            hwp5file = Hwp5File(str(path))
        except Exception as exc:
            raise DocumentReadError(f"HWP를 열 수 없습니다: {path.name} ({exc})") from exc

        try:
            with tempfile.TemporaryDirectory(prefix="hwp5_xml_") as tmp_dir:
                xml_path = Path(tmp_dir) / "doc.xml"
                with open(xml_path, "wb") as out:
                    hwp5file.xmlevents(embedbin=False).dump(out)
                return ElementTree.parse(xml_path).getroot()
        except ElementTree.ParseError as exc:
            raise DocumentReadError(f"HWP 내부 구조를 해석하지 못했습니다: {path.name} ({exc})") from exc
        except Exception as exc:
            raise DocumentReadError(f"HWP 변환에 실패했습니다: {path.name} ({exc})") from exc
        finally:
            hwp5file.close()

    def _walk(
        self, element: ElementTree.Element, document: ParsedDocument, buffer: list[str]
    ) -> None:
        """본문을 문서 순서대로 훑는다. 표는 별도 청크로 분리하고 본문 텍스트에 섞지 않는다."""
        for child in element:
            if child.tag == "TableControl":
                self._flush_text(document, buffer)
                table_data = self._read_table(child)
                if table_data is not None:
                    document.chunks.append(self.make_table_chunk(document, table_data))
                continue

            if child.tag == "Paragraph":
                text = self._own_text(child)
                if text:
                    buffer.append(text)

            self._walk(child, document, buffer)

    def _flush_text(self, document: ParsedDocument, buffer: list[str]) -> None:
        body = "\n".join(buffer).strip()
        if body:
            document.chunks.append(self.make_text_chunk(document, body))
        buffer.clear()

    def _read_table(self, table_element: ElementTree.Element) -> TableData | None:
        rows: list[list[str]] = []
        for row_element in table_element.iter("TableRow"):
            rows.append([self._own_text(cell) for cell in row_element.iter("TableCell")])

        return TableData.from_rows(rows)

    @classmethod
    def _own_text(cls, element: ElementTree.Element) -> str:
        """중첩된 표에 속한 텍스트는 제외하고 해당 요소가 직접 담고 있는 본문만 모은다."""
        parts: list[str] = []
        for child in element:
            if child.tag == "TableControl":
                continue
            if child.tag == "Text" and child.text:
                parts.append(child.text)
            nested = cls._own_text(child)
            if nested:
                parts.append(nested)
        return "".join(parts).strip()

    def _extract_images(self, document: ParsedDocument, path: Path, asset_dir: Path) -> None:
        try:
            from hwp5.storage import unpack
            from hwp5.xmlmodel import Hwp5File
        except ImportError:
            return

        try:
            hwp5file = Hwp5File(str(path))
        except Exception as exc:
            self.record_error(document, f"이미지 추출을 위해 HWP를 다시 열지 못했습니다: {exc}")
            return

        try:
            if "BinData" not in hwp5file:
                return
            bindata_dir = asset_dir / "bindata"
            bindata_dir.mkdir(parents=True, exist_ok=True)
            unpack(hwp5file["BinData"], str(bindata_dir))
        except Exception as exc:
            self.record_error(document, f"BinData 이미지 추출 실패: {exc}")
            return
        finally:
            hwp5file.close()

        # BinData 항목은 확장자가 .tmp로 저장되므로 시그니처로 실제 형식을 판별한다.
        for item in sorted(bindata_dir.iterdir()):
            if not item.is_file():
                continue
            extension = sniff_image_extension(item.read_bytes())
            if extension is None:
                continue

            image_path = item.with_suffix(f".{extension}")
            item.replace(image_path)
            document.chunks.append(
                self.make_image_chunk(
                    document,
                    ImageData(image_path=str(image_path), origin="extracted"),
                )
            )

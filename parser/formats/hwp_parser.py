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
from parser.utils.headings import body_size_of, clean_heading, is_heading_size
from parser.utils.imaging import sniff_image_extension


class _Heading:
    """재귀 순회 중에 "지금까지 지나온 제목"을 들고 다니는 상자 (T10.32).

    `_walk()`가 재귀 호출이라 지역 변수로는 갱신이 위로 전달되지 않는다.
    """

    __slots__ = ("text",)

    def __init__(self, text: str = "") -> None:
        self.text = text


class HwpParser(BaseParser):
    extensions = (".hwp",)

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        root = self._load_xml(path)
        asset_dir = self.asset_dir_for(path)

        char_sizes = self._char_sizes(root)
        # 본문 크기는 문서 전체를 보고 정한다 (T10.32) — hwpx와 같은 방식.
        body_size = body_size_of(self._sized_paragraphs(root, char_sizes))

        text_buffer: list[str] = []
        heading = _Heading()
        self._walk(root, document, text_buffer, char_sizes, body_size, heading)
        self._flush_text(document, text_buffer, heading.text)
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
        self,
        element: ElementTree.Element,
        document: ParsedDocument,
        buffer: list[str],
        char_sizes: list[float],
        body_size: float,
        heading: _Heading,
    ) -> None:
        """본문을 문서 순서대로 훑는다. 표는 별도 청크로 분리하고 본문 텍스트에 섞지 않는다."""
        for child in element:
            if child.tag == "TableControl":
                self._flush_text(document, buffer, heading.text)
                table_data = self._read_table(child)
                if table_data is not None:
                    document.chunks.append(
                        self.make_table_chunk(document, table_data, heading=heading.text)
                    )
                continue

            if child.tag == "Paragraph":
                text = self._own_text(child)
                if text:
                    size = self._paragraph_size(child, char_sizes)
                    if is_heading_size(size, body_size):
                        candidate = clean_heading(text)
                        if candidate:
                            # 앞선 문단은 **이전** 제목으로 확정하고 나서 갈아 끼운다
                            # (docx·hwpx와 같은 순서).
                            self._flush_text(document, buffer, heading.text)
                            heading.text = candidate
                    buffer.append(text)

            self._walk(child, document, buffer, char_sizes, body_size, heading)

    def _flush_text(
        self, document: ParsedDocument, buffer: list[str], heading: str = ""
    ) -> None:
        body = "\n".join(buffer).strip()
        if body:
            document.chunks.append(self.make_text_chunk(document, body, heading=heading))
        buffer.clear()

    @staticmethod
    def _char_sizes(root: ElementTree.Element) -> list[float]:
        """`CharShape` 정의의 `basesize`를 **정의 순서대로** 모은다 (T10.32).

        pyhwp의 중간 XML에서 각 `Text`는 `charshape-id`(정수 인덱스)로 글자 모양을
        가리키고, 실제 크기는 문서 앞쪽 `CharShape` 정의에 `basesize`(1/100 pt)로
        들어 있다 — HWPX가 header.xml을 따로 읽어야 하는 것과 같은 구조다.
        """
        sizes: list[float] = []
        for element in root.iter("CharShape"):
            try:
                sizes.append(float(element.get("basesize", "0")))
            except ValueError:
                sizes.append(0.0)
        return sizes

    @classmethod
    def _paragraph_size(cls, element: ElementTree.Element, char_sizes: list[float]) -> float:
        """이 문단에 쓰인 가장 큰 글꼴 크기. 중첩 표 안쪽은 세지 않는다."""
        found: list[float] = []
        for child in element:
            if child.tag == "TableControl":
                continue
            if child.tag == "Text":
                try:
                    index = int(child.get("charshape-id", "-1"))
                except ValueError:
                    index = -1
                if 0 <= index < len(char_sizes):
                    found.append(char_sizes[index])
            nested = cls._paragraph_size(child, char_sizes)
            if nested:
                found.append(nested)
        return max(found) if found else 0.0

    @classmethod
    def _sized_paragraphs(
        cls, element: ElementTree.Element, char_sizes: list[float]
    ) -> list[tuple[float, str]]:
        """문서 순서대로 (글꼴 크기, 문단 텍스트)를 모은다 — 본문 크기 판정용."""
        collected: list[tuple[float, str]] = []
        for child in element:
            if child.tag == "Paragraph":
                text = cls._own_text(child)
                if text:
                    collected.append((cls._paragraph_size(child, char_sizes), text))
            collected.extend(cls._sized_paragraphs(child, char_sizes))
        return collected

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

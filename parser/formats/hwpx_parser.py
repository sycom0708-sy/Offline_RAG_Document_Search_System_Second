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
from parser.utils.headings import body_size_of, clean_heading, is_heading_size
from parser.utils.imaging import sniff_image_extension

_SECTION_PATTERN = re.compile(r"^Contents/section\d+\.xml$", re.IGNORECASE)
_BINDATA_PREFIX = "BinData/"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


class _Heading:
    """재귀 순회 중에 "지금까지 지나온 제목"을 들고 다니는 상자 (T10.32).

    `_walk()`가 자기 자신을 재귀 호출하므로 지역 변수로는 갱신이 위로 전달되지
    않는다 — 문자열 대신 이 객체를 넘겨 한 자리를 공유한다.
    """

    __slots__ = ("text",)

    def __init__(self, text: str = "") -> None:
        self.text = text


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

            char_heights = self._char_heights(archive)
            roots: dict[str, ElementTree.Element] = {}
            for section_name in sections:
                try:
                    roots[section_name] = ElementTree.fromstring(archive.read(section_name))
                except ElementTree.ParseError as exc:
                    self.record_error(document, f"{section_name} 파싱 실패: {exc}")

            # 본문 크기는 **문서 전체**를 보고 정한다 (T10.32). 섹션마다 따로 정하면
            # 표지처럼 큰 글씨만 있는 섹션에서 제목 크기가 본문으로 잡힌다.
            body_size = body_size_of(
                [pair for root in roots.values() for pair in self._sized_paragraphs(root, char_heights)]
            )

            for section_index, section_name in enumerate(sections, start=1):
                root = roots.get(section_name)
                if root is None:
                    continue

                buffer: list[str] = []
                tracker = _Heading()
                self._walk(root, document, buffer, section_index, char_heights, body_size, tracker)
                self._flush_text(document, buffer, section_index, tracker.text)

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
        char_heights: dict[str, float],
        body_size: float,
        heading: _Heading,
    ) -> None:
        for child in element:
            tag = _local_name(child.tag)
            if tag == "tbl":
                # 표를 만나면 앞선 문단을 먼저 확정해 텍스트/표 청크가 섞이지 않게 한다.
                self._flush_text(document, buffer, section_index, heading.text)
                table_data = self._read_table(child)
                if table_data is not None:
                    document.chunks.append(
                        self.make_table_chunk(
                            document, table_data, page_or_slide=section_index,
                            heading=heading.text,
                        )
                    )
                continue

            if tag == "p":
                text = self._own_text(child)
                if text:
                    size = self._paragraph_size(child, char_heights)
                    if is_heading_size(size, body_size):
                        candidate = clean_heading(text)
                        if candidate:
                            # 새 절이 시작된다 — 앞선 문단을 **이전** 제목으로 확정한다
                            # (docx와 같은 순서).
                            self._flush_text(document, buffer, section_index, heading.text)
                            heading.text = candidate
                    buffer.append(text)

            self._walk(child, document, buffer, section_index, char_heights, body_size, heading)

    def _flush_text(
        self, document: ParsedDocument, buffer: list[str], section_index: int, heading: str = ""
    ) -> None:
        body = "\n".join(buffer).strip()
        if body:
            document.chunks.append(
                self.make_text_chunk(
                    document, body, page_or_slide=section_index, heading=heading
                )
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

    @staticmethod
    def _char_heights(archive: zipfile.ZipFile) -> dict[str, float]:
        """`Contents/header.xml`의 글자 모양 정의에서 `id -> 글꼴 크기`를 뽑는다 (T10.32).

        HWPX는 문단 안 각 `run`이 `charPrIDRef`로 글자 모양을 가리키고, 실제 크기는
        header.xml의 `charPr height`(1/100 pt 단위)에 있다. 즉 크기를 알려면 두 파일을
        맞물려 읽어야 한다 — PDF가 span에서 바로 크기를 읽는 것과 다른 점이다.

        실측한 두 문서 모두 `outlineLevel` 속성이 **없어서** 개요 수준으로는 제목을
        가릴 수 없었다. 그래서 PDF와 같은 글꼴 크기 방식으로 간다.
        """
        try:
            root = ElementTree.fromstring(archive.read("Contents/header.xml"))
        except (KeyError, ElementTree.ParseError, OSError):
            return {}  # 제목은 부가 정보다 — 없으면 제목 없이 간다

        heights: dict[str, float] = {}
        for element in root.iter():
            if _local_name(element.tag) != "charPr":
                continue
            char_id, height = element.get("id"), element.get("height")
            if char_id is None or height is None:
                continue
            try:
                heights[char_id] = float(height)
            except ValueError:
                continue
        return heights

    @classmethod
    def _paragraph_size(
        cls, element: ElementTree.Element, char_heights: dict[str, float]
    ) -> float:
        """이 문단에 쓰인 가장 큰 글꼴 크기. 중첩 표 안쪽은 세지 않는다."""
        sizes: list[float] = []
        for child in element:
            if _local_name(child.tag) == "tbl":
                continue
            ref = child.get("charPrIDRef")
            if ref is not None and ref in char_heights:
                sizes.append(char_heights[ref])
            nested = cls._paragraph_size(child, char_heights)
            if nested:
                sizes.append(nested)
        return max(sizes) if sizes else 0.0

    @classmethod
    def _sized_paragraphs(
        cls, element: ElementTree.Element, char_heights: dict[str, float]
    ) -> list[tuple[float, str]]:
        """문서 순서대로 (글꼴 크기, 문단 텍스트)를 모은다 — 본문 크기 판정용."""
        collected: list[tuple[float, str]] = []
        for child in element:
            if _local_name(child.tag) == "p":
                text = cls._own_text(child)
                if text:
                    collected.append((cls._paragraph_size(child, char_heights), text))
            collected.extend(cls._sized_paragraphs(child, char_heights))
        return collected

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

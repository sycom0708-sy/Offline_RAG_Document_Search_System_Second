"""TXT 파서 (T1.4) — chardet 인코딩 자동 감지."""

from __future__ import annotations

from pathlib import Path

from parser.base import BaseParser, DocumentReadError
from parser.schema import ParsedDocument
from parser.utils.encoding import read_text_file


class TxtParser(BaseParser):
    extensions = (".txt", ".md", ".csv", ".log")

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        try:
            text, encoding = read_text_file(path)
        except UnicodeDecodeError as exc:
            raise DocumentReadError(f"인코딩을 감지하지 못했습니다: {path.name}") from exc

        document.title = self._extract_title(text) or path.stem
        if text.strip():
            document.chunks.append(
                self.make_text_chunk(document, text.strip(), keywords=[f"encoding:{encoding}"])
            )

    @staticmethod
    def _extract_title(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                return stripped[:200]
        return ""

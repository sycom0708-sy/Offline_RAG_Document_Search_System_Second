"""구버전 포맷 파서 (T1.9) — doc/xls/ppt를 LibreOffice로 변환 후 순정 파서에 위임."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from parser.base import BaseParser
from parser.formats.docx_parser import DocxParser
from parser.formats.pptx_parser import PptxParser
from parser.formats.xlsx_parser import XlsxParser
from parser.schema import ParseStatus, ParsedDocument
from parser.utils.libreoffice import LibreOfficeError, convert

_CONVERSION_TARGETS = {
    ".doc": ("docx", DocxParser),
    ".xls": ("xlsx", XlsxParser),
    ".ppt": ("pptx", PptxParser),
    ".rtf": ("docx", DocxParser),
}


class LegacyOfficeParser(BaseParser):
    extensions = tuple(_CONVERSION_TARGETS)

    def _parse(self, path: Path, document: ParsedDocument) -> None:
        target_ext, parser_cls = _CONVERSION_TARGETS[path.suffix.lower()]

        with tempfile.TemporaryDirectory(prefix="legacy_convert_") as tmp_dir:
            try:
                converted = convert(path, target_ext, output_dir=tmp_dir)
            except LibreOfficeError as exc:
                # 변환 실패는 삼키지 않고 상태로 전파해 상위(인덱서)가 재시도·보고할 수 있게 한다.
                document.status = ParseStatus.FAILED
                document.errors.append(f"{path.suffix} 변환 실패: {exc}")
                return

            # 변환본에서 추출한 이미지는 임시 폴더와 함께 사라지므로 원본 기준 asset 폴더로 받는다.
            asset_dir = self.asset_dir_for(path)
            delegate = parser_cls(asset_dir=asset_dir)
            try:
                converted_doc = delegate.parse(converted)
            except Exception as exc:
                document.status = ParseStatus.FAILED
                document.errors.append(f"변환본 파싱 실패({converted.name}): {exc}")
                return

        # 변환본이 아니라 원본 파일을 가리키도록 메타데이터를 되돌린다.
        document.title = converted_doc.title if converted_doc.title != converted.stem else path.stem
        document.errors.extend(converted_doc.errors)
        if converted_doc.status is not ParseStatus.OK:
            document.status = converted_doc.status

        for chunk in converted_doc.chunks:
            chunk.doc_id = document.doc_id
            chunk.chunk_id = chunk.chunk_id.replace(converted_doc.doc_id, document.doc_id, 1)
            chunk.file_path = document.file_path
            chunk.file_name = document.file_name
            document.chunks.append(chunk)

    def asset_dir_for(self, path: Path) -> Path:
        target = super().asset_dir_for(path)
        # 재변환 시 이전 산출물이 남지 않도록 비우고 시작한다.
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        return target

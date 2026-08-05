"""벡터 도형 페이지 캡처 (T1.11).

docx/pptx의 SmartArt·차트·도형은 이미지 파트로 존재하지 않아 추출되지 않는다.
LibreOffice로 PDF 변환 후 해당 페이지·슬라이드를 렌더링해 캡처한다 (TECH 3.1절).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pymupdf

from parser.utils.libreoffice import convert

_RENDER_ZOOM = 2.0


def render_pages(source: str | Path, output_dir: str | Path, pages: set[int] | None = None) -> dict[int, Path]:
    """source를 PDF로 변환해 페이지를 PNG로 캡처하고 {페이지번호(1-based): 경로}를 반환한다.

    LibreOffice 미설치·변환 실패 시 libreoffice 모듈의 예외가 그대로 전파된다.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[int, Path] = {}

    with tempfile.TemporaryDirectory(prefix="lo_render_") as tmp_dir:
        pdf_path = convert(source, "pdf", output_dir=tmp_dir)
        with pymupdf.open(pdf_path) as pdf:
            for index, page in enumerate(pdf):
                page_no = index + 1
                if pages is not None and page_no not in pages:
                    continue
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(_RENDER_ZOOM, _RENDER_ZOOM))
                target = out_dir / f"p{page_no:04d}_render.png"
                pixmap.save(target)
                captured[page_no] = target

    return captured

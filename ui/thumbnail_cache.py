"""이미지 썸네일 캐시 (T5.5, TECH 4.4).

검색 시점마다 원본 이미지를 다시 디코딩하지 않도록 폭 300px 축소판을
`data/thumbnails/`에 캐시해둔다 — "캐시만 조회 → 속도 확보"(TECH 4.4).
캐시 키는 `chunk_id`다. 문서가 재인덱싱되면 청크가 새로 생성되어 chunk_id도
바뀌므로 별도 mtime 비교 없이도 오래된 캐시를 자연스럽게 남겨두지 않게
된다 — mtime 기반의 명시적 무효화가 필요해지면 Phase 8 증분 인덱싱에서
다룬다.

Pillow 등 별도 이미지 라이브러리를 추가하지 않고 PySide6에 이미 포함된
`QImage`만으로 디코딩·축소·저장한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ui.state import DATA_DIR

THUMBNAIL_DIR = DATA_DIR / "thumbnails"
THUMBNAIL_WIDTH = 300

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _safe_name(chunk_id: str) -> str:
    return _UNSAFE_CHARS.sub("_", chunk_id)


def get_thumbnail_path(chunk_id: str, source_path: Path) -> Path | None:
    """캐시된 썸네일 경로를 돌려준다. 없으면 새로 만든다.

    원본이 없거나 디코딩할 수 없으면 None — 호출부(ImageCard)가 방어적으로
    "미리보기 없음" 상태를 보여준다.
    """
    cache_path = THUMBNAIL_DIR / f"{_safe_name(chunk_id)}.png"
    if cache_path.is_file():
        return cache_path

    if not source_path.is_file():
        return None

    image = QImage(str(source_path))
    if image.isNull():
        return None

    if image.width() > THUMBNAIL_WIDTH:
        image = image.scaledToWidth(THUMBNAIL_WIDTH, Qt.TransformationMode.SmoothTransformation)

    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    if not image.save(str(cache_path)):
        return None
    return cache_path

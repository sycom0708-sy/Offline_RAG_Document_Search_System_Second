"""이미지 썸네일 캐시 (T5.5, TECH 4.4).

검색 시점마다 원본 이미지를 다시 디코딩하지 않도록 폭 300px 축소판을
`data/thumbnails/`에 캐시해둔다 — "캐시만 조회 → 속도 확보"(TECH 4.4).
캐시 키는 `chunk_id`다. `chunk_id`는 `doc_id+type+ordinal` 기반이라, 이미지
내용이 바뀌어도 같은 위치(ordinal)면 chunk_id가 그대로다 — 즉 재인덱싱만으로는
캐시가 자연 무효화되지 않는다. `evict_thumbnails()`가 이 무효화를 담당한다
(Phase 8, T8.4) — `indexer.fts5.store.store_document()`가 교체되기 전 문서의
이미지 청크 id를 돌려주고, `IndexReport.stale_image_chunk_ids`를 거쳐
`MainWindow`가 인덱싱 완료 시 이 함수를 호출해 캐시 파일을 지운다. 다음 조회
때 최신 원본으로 재생성된다.

Pillow 등 별도 이미지 라이브러리를 추가하지 않고 PySide6에 이미 포함된
`QImage`만으로 디코딩·축소·저장한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

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


def evict_thumbnails(chunk_ids: Iterable[str]) -> None:
    """지정한 chunk_id들의 캐시된 썸네일 파일을 지운다 (Phase 8, T8.4).

    캐시가 없는 id를 넘겨도 조용히 무시한다 — 호출부가 "혹시 있었을지도
    모르는" id를 넘기는 것도 허용하기 위해서다.
    """
    for chunk_id in chunk_ids:
        cache_path = THUMBNAIL_DIR / f"{_safe_name(chunk_id)}.png"
        cache_path.unlink(missing_ok=True)

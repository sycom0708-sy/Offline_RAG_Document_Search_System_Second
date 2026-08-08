"""검색 결과 청크를 사람이 읽는 형태로 푸는 유틸 (UI 비의존).

원래 `ui/widgets/card_common.py`에 있던 함수들이다. Phase 6의 근거 강제
프롬프트가 발췌에 붙일 출처 라벨을 **UI 카드와 같은 규칙**으로 써야 하는데
(Phase 7 T7.3의 출처 표기와 형식이 어긋나면 안 된다), `card_common`은
PySide6를 import하므로 sLM 쪽에서 가져다 쓸 수 없어 여기로 옮겼다.
`card_common`은 이 모듈을 그대로 재노출한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType, ImageData, TableData


def parse_table_data(result: SearchResult) -> TableData | None:
    """`chunks.table_json`을 역직렬화한다. 값이 없거나 손상됐으면 None."""
    if not result.table_json:
        return None
    try:
        return TableData(**json.loads(result.table_json))
    except (json.JSONDecodeError, TypeError):
        return None


def parse_image_data(result: SearchResult) -> ImageData | None:
    """`chunks.image_json`을 역직렬화한다. 값이 없거나 손상됐으면 None."""
    if not result.image_json:
        return None
    try:
        return ImageData(**json.loads(result.image_json))
    except (json.JSONDecodeError, TypeError):
        return None


def format_location(result: SearchResult) -> str:
    """DESIGN §5.2 위치 표기.

    xlsx 표는 `page_or_slide`(시트 **인덱스**)가 아니라 `TableData.caption`
    (시트 **이름**, `XlsxParser`가 `sheet.title`을 넣어둔다)을 우선 쓴다 —
    안 그러면 "2페이지"처럼 목업과 어긋난 표기가 된다.
    """
    ext = Path(result.file_name).suffix.lower()

    if result.type is ChunkType.TABLE and ext == ".xlsx":
        table = parse_table_data(result)
        if table and table.caption:
            return table.caption

    if result.page_or_slide is None:
        return "-"
    if ext == ".pptx":
        return f"{result.page_or_slide}번 슬라이드"
    return f"{result.page_or_slide}페이지"

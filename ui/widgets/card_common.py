"""텍스트·표·이미지 세 카드 타입이 공유하는 부품 (T5.1, DESIGN §5.1/§5.7).

세 카드는 상속이 아니라 이 모듈의 함수들을 조합해서 만든다 — 헤더 구성은
동일하지만 뒤에 붙는 버튼(표 복사/확대)이 카드마다 달라, `QFrame` 다중상속
보다 빌더 함수 하나를 공유하는 쪽이 단순하다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

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


def open_source_file(file_path: str) -> str | None:
    """원문을 OS 기본 프로그램으로 연다. 성공 시 None, 실패 시 사유 문자열."""
    path = Path(file_path)
    if not path.is_file():
        return f"파일을 찾을 수 없습니다: {path}"
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    return None


def build_card_header(
    hybrid_result,
    extra_buttons: Sequence[QPushButton] = (),
) -> tuple[QHBoxLayout, QPushButton]:
    """파일명 · 위치 · (관련성 낮음) · 부가 버튼 · 원문 열기 순으로 헤더를 조립한다.

    `open_button`은 클릭 시그널 연결을 호출부가 하도록 그대로 반환한다 —
    "원문 열기 실패" 처리(사유 emit 등)는 카드마다 어느 시그널에 실어 보낼지
    다르기 때문이다.
    """
    header = QHBoxLayout()
    header.setSpacing(6)

    name_label = QLabel(hybrid_result.file_name)
    name_label.setObjectName("ResultCardFileName")
    header.addWidget(name_label)

    sep = QLabel("·")
    sep.setObjectName("ResultCardSeparator")
    header.addWidget(sep)

    location_label = QLabel(format_location(hybrid_result.result))
    location_label.setObjectName("ResultCardLocation")
    header.addWidget(location_label)

    header.addStretch()

    if hybrid_result.is_low_relevance:
        relevance_label = QLabel("관련성 낮음")
        relevance_label.setObjectName("ResultCardRelevanceLabel")
        header.addWidget(relevance_label)

    for button in extra_buttons:
        header.addWidget(button)

    open_button = QPushButton("원문 열기 ↗")
    open_button.setObjectName("ResultCardOpenButton")
    open_button.setCursor(Qt.CursorShape.PointingHandCursor)
    header.addWidget(open_button)

    return header, open_button

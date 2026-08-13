"""텍스트·표·이미지 세 카드 타입이 공유하는 부품 (T5.1, DESIGN §5.1/§5.7).

세 카드는 상속이 아니라 이 모듈의 함수들을 조합해서 만든다 — 헤더 구성은
동일하지만 뒤에 붙는 버튼(표 복사/확대)이 카드마다 달라, `QFrame` 다중상속
보다 빌더 함수 하나를 공유하는 쪽이 단순하다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton

# 청크 해석·위치 표기는 sLM 프롬프트(Phase 6)도 같은 규칙으로 써야 해서
# PySide6에 묶이지 않는 `search/chunk_view.py`로 옮겼다. 카드 쪽 호출부가
# 그대로 쓰도록 여기서 재노출한다.
from search.chunk_view import format_location, parse_image_data, parse_table_data

__all__ = [
    "format_location",
    "parse_image_data",
    "parse_table_data",
    "open_source_file",
    "build_card_header",
    "apply_low_relevance_style",
]

# DESIGN §5.6 / §11 — 0.5 이하로 내리지 않는다.
LOW_RELEVANCE_OPACITY = 0.5


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
    """파일명 · 위치 · (관련성 낮음) · (파일명 매치) · 부가 버튼 · 원문 열기 순으로 헤더를 조립한다.

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

    if hybrid_result.is_filename_only_match:
        # T10.6: 검색어가 파일명에만 있고 본문·캡션엔 없는 결과 — "관련성
        # 낮음"과 독립적인 신호라 동시에 뜰 수 있다.
        filename_match_label = QLabel("파일명 매치")
        filename_match_label.setObjectName("ResultCardFileNameMatchLabel")
        header.addWidget(filename_match_label)

    for button in extra_buttons:
        header.addWidget(button)

    open_button = QPushButton("원문 열기 ↗")
    open_button.setObjectName("ResultCardOpenButton")
    open_button.setCursor(Qt.CursorShape.PointingHandCursor)
    header.addWidget(open_button)

    return header, open_button


def apply_low_relevance_style(card: QFrame, hybrid_result) -> None:
    """DESIGN §5.6 — 카드 전체를 흐리게 + QSS `[relevance="low"]` 훅.

    🔴 원래 `ResultCard`(텍스트 카드)에만 있던 로직이다. "관련성 낮음" **라벨**은
    `build_card_header()`가 세 카드 공통으로 붙여주지만, 실제로 흐려 보이게
    하는 이 효과는 표·이미지 카드에 옮겨 붙이는 걸 빠뜨렸다 — 라벨은 있는데
    흐림은 없는 카드가 나온 이유(실사용에서 발견, 2026-08-11). 세 카드
    생성자 마지막에서 반드시 이 함수를 불러야 한다.
    """
    if hybrid_result.is_low_relevance:
        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(LOW_RELEVANCE_OPACITY)
        card.setGraphicsEffect(effect)
        card.setProperty("relevance", "low")
    else:
        card.setProperty("relevance", "normal")

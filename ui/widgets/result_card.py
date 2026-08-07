"""결과 카드 — 공통 프레임 + 텍스트 카드 (T4.12~T4.14).

DESIGN §5.1(공통 프레임) / §5.2(위치 표기) / §5.3(텍스트 카드) / §5.6(관련성
낮음) / §8(확정 3·4: 이미지·관련성낮음 카드에도 "원문 열기" 병기)를 반영한다.
표/이미지 카드는 Phase 5 범위라 이 파일은 텍스트 카드만 다룬다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from search.hybrid_search import HybridResult
from ui.highlight import highlighted_excerpt

LOW_RELEVANCE_OPACITY = 0.5  # DESIGN §5.6 / §11 (0.5 이하로 내리지 않음)


def format_location(result) -> str:
    """DESIGN §5.2 위치 표기.

    이번 Phase는 텍스트 카드만 다루므로 페이지/슬라이드 번호만 처리한다.
    xlsx 시트명 표기(caption 기반)는 Phase 5 표 카드에서 `TableData.caption`을
    직접 써서 구현한다 — 여기 `result.caption`은 BM25 가중치용으로 헤더까지
    이어붙인 문자열이라 시트명만 뽑기에 적합하지 않다.
    """
    if result.page_or_slide is None:
        return "-"
    ext = Path(result.file_name).suffix.lower()
    if ext == ".pptx":
        return f"{result.page_or_slide}번 슬라이드"
    return f"{result.page_or_slide}페이지"


class ResultCard(QFrame):
    open_failed = Signal(str)  # 원문 열기 실패 시 사유

    def __init__(
        self,
        hybrid_result: HybridResult,
        query: str,
        case_sensitive: bool = False,
        exact_word: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ResultCard")
        self._result = hybrid_result

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

        open_button = QPushButton("원문 열기 ↗")
        open_button.setObjectName("ResultCardOpenButton")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.clicked.connect(self._open_source)
        header.addWidget(open_button)

        body_label = QLabel()
        body_label.setObjectName("ResultCardBody")
        body_label.setWordWrap(True)
        body_label.setTextFormat(Qt.TextFormat.RichText)
        body_label.setText(
            highlighted_excerpt(hybrid_result.content, query, case_sensitive, exact_word)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)  # DESIGN §10.5 카드 내부 여백
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(body_label)

        if hybrid_result.is_low_relevance:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(LOW_RELEVANCE_OPACITY)
            self.setGraphicsEffect(effect)
            self.setProperty("relevance", "low")
        else:
            self.setProperty("relevance", "normal")

    def _open_source(self) -> None:
        path = Path(self._result.result.file_path)
        if not path.is_file():
            self.open_failed.emit(f"파일을 찾을 수 없습니다: {path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

"""결과 카드 — 텍스트 카드 (T4.12~T4.14).

DESIGN §5.1(공통 프레임) / §5.3(텍스트 카드) / §5.6(관련성 낮음) / §8(확정
3·4: 이미지·관련성낮음 카드에도 "원문 열기" 병기)를 반영한다. 표/이미지
카드는 `table_card.py`/`image_card.py` — 헤더 구성은 `card_common.py`를
공유한다(Phase 5).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QLabel, QVBoxLayout

from search.hybrid_search import HybridResult
from ui.highlight import highlighted_excerpt
from ui.widgets.card_common import build_card_header, open_source_file

LOW_RELEVANCE_OPACITY = 0.5  # DESIGN §5.6 / §11 (0.5 이하로 내리지 않음)


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

        header, open_button = build_card_header(hybrid_result)
        open_button.clicked.connect(self._open_source)

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
        error = open_source_file(self._result.result.file_path)
        if error:
            self.open_failed.emit(error)

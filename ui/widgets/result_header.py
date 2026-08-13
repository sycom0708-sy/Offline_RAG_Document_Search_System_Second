"""검색 결과 헤더 — 현재 검색어 + 닫기(✕) (Phase 7.7).

목업(`rag_ui_concept_searchresult.html`)의 상단 헤더를 옮긴다. 검색 결과
모드에서만 보이고, 챗봇 모드(`ResultList.show_chat_mode`)에서는 숨긴다
(DESIGN §5.8: 챗봇 패널은 별도 화면으로 완전히 교체되므로 검색어 헤더가
필요 없다).

✕를 누르면 검색어·결과를 모두 지우고 초기 안내로 돌아간다 — `MainWindow`가
`close_requested`를 받아 처리한다(이 위젯은 상태를 모르므로 판단하지 않는다).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

CLOSE_BUTTON_TEXT = "✕"


class ResultHeader(QWidget):
    close_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultHeader")

        self._title = QLabel()
        self._title.setObjectName("ResultHeaderTitle")
        self._title.setWordWrap(False)

        self._close_button = QPushButton(CLOSE_BUTTON_TEXT)
        self._close_button.setObjectName("ResultHeaderClose")
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setFlat(True)
        self._close_button.clicked.connect(self.close_requested.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.addWidget(self._title, stretch=1)
        layout.addWidget(self._close_button)

    def set_query(self, query: str) -> None:
        self._title.setText(query)

    def query_text(self) -> str:
        return self._title.text()

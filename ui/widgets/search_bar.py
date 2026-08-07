"""검색 입력창 (T4.2~T4.3).

DESIGN §3.1~3.2: 돋보기 아이콘 + placeholder, Enter는 즉시 검색·입력 중엔
300ms debounce. Qt `QLineEdit`은 IME `compositionend`를 직접 노출하지
않으므로, 각 키 입력마다 debounce 타이머를 재시작하는 방식으로 조합 중간
상태를 근사적으로 걸러낸다(완벽한 조합 감지는 아님 — PLAN §4-B ③ 참고).
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

DEBOUNCE_MS = 300
PLACEHOLDER = "계약서 검토 기준이 뭐였지"


class SearchBar(QWidget):
    """`search_requested(str)`를 debounce 또는 Enter 시 emit한다."""

    search_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SearchBar")

        icon = QLabel("🔍")
        icon.setObjectName("SearchIcon")

        self._input = QLineEdit()
        self._input.setObjectName("SearchInput")
        self._input.setPlaceholderText(PLACEHOLDER)
        self._input.setClearButtonEnabled(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        layout.addWidget(icon)
        layout.addWidget(self._input)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_search)

        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._on_return_pressed)

    def _on_text_changed(self, _text: str) -> None:
        self._debounce.start()

    def _on_return_pressed(self) -> None:
        self._debounce.stop()
        self._emit_search()

    def _emit_search(self) -> None:
        self.search_requested.emit(self._input.text())

    def text(self) -> str:
        return self._input.text()

    def set_text(self, value: str) -> None:
        self._input.setText(value)

    def clear(self) -> None:
        self._input.clear()

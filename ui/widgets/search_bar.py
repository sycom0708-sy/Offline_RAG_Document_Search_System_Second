"""하단 입력창 (Phase 7.7) — 검색 결과 모드·챗봇 모드가 공유하는 단일 입력 지점.

목업(`rag_ui_concept_*.html`)은 두 화면 모두 하단 입력창 하나로 통일돼 있다.
기존에는 상단 검색바(300ms debounce 자동검색)와 챗봇 패널 자체 입력창(Enter·
버튼 제출) 두 곳에서 입력을 받았는데, 이 위젯이 그 자리를 통합한다 — 검색
모드였던 시절의 debounce는 폐기하고 Enter·검색 버튼으로만 제출한다
(`MainWindow`가 현재 모드를 보고 검색 실행/챗봇 메시지 전송으로 분기한다).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

# Phase 7.7: 목업(rag_ui_concept_*.html) 두 화면이 동일한 문구를 쓴다 —
# 검색·챗봇 두 모드를 한 입력창이 같이 받으므로 모드별로 나누지 않는다.
# (기존 "계약서 검토 기준이 뭐였지" 예시 placeholder는 검색 전용이던
# 시절의 문구라 폐기한다.)
PLACEHOLDER = "질문을 입력하세요"
SEND_BUTTON_LABEL = "검색"  # 2026-08-21, 사용자 요청 — 기존 "보내기"


class InputBar(QWidget):
    """Enter 또는 보내기 버튼 클릭 시 `submitted(str)`을 emit한다.

    제출과 동시에 입력창을 비운다 — 챗봇 모드에서 "보낸 메시지가 입력창에
    남아 있는" 것은 어색하고, 검색 모드에서는 현재 검색어를 `ResultHeader`가
    대신 보여주므로 입력창이 비어도 무엇을 검색했는지 알 수 있다.

    빈 문자열도 그대로 emit한다 — 검색 모드에서는 "빈 질의 → 초기 안내로
    복귀"가 기존 동작이고, 챗봇 모드(`ChatPanel.send_message`)는 스스로
    빈 문자열을 걸러낸다. 이 위젯은 어느 쪽 의미인지 모르므로 판단하지 않는다.
    """

    submitted = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("InputBar")

        self._input = QLineEdit()
        self._input.setObjectName("InputBarField")
        self._input.setPlaceholderText(PLACEHOLDER)
        self._input.setClearButtonEnabled(True)
        self._input.returnPressed.connect(self._emit_submit)

        self._send_button = QPushButton(SEND_BUTTON_LABEL)
        self._send_button.setObjectName("InputBarSendButton")
        self._send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_button.clicked.connect(self._emit_submit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._input, stretch=1)
        layout.addWidget(self._send_button)

    def _emit_submit(self) -> None:
        text = self._input.text()
        self._input.clear()
        self.submitted.emit(text)

    def text(self) -> str:
        return self._input.text()

    def set_text(self, value: str) -> None:
        self._input.setText(value)

    def clear(self) -> None:
        self._input.clear()

    def submit_text(self, value: str) -> None:
        """텍스트를 넣고 즉시 제출한다 — 테스트에서 검색을 흉내 낼 때 쓴다.

        2026-08-21부터 최근 검색 클릭은 이 메서드를 쓰지 않는다(`set_text`만
        호출) — 클릭 즉시 검색되던 것을, 사용자가 Enter·검색 버튼을 직접
        눌러야 시작하도록 바꿨다[사용자 요청]."""
        self.set_text(value)
        self._emit_submit()

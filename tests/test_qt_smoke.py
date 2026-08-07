"""pytest-qt 동작 확인용 스모크 테스트. UI 구현 시작 전 인프라 검증."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton


def test_qtbot_can_create_and_interact_with_widget(qtbot):
    label = QLabel("초기값")
    qtbot.addWidget(label)
    assert label.text() == "초기값"


def test_qtbot_button_click_signal(qtbot):
    button = QPushButton("클릭")
    qtbot.addWidget(button)

    clicked = []
    button.clicked.connect(lambda: clicked.append(True))
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    assert clicked == [True]


def test_korean_text_round_trips(qtbot):
    label = QLabel("한글 렌더링 테스트: 가나다라마바사")
    qtbot.addWidget(label)
    assert "가나다라마바사" in label.text()

"""간단한 안내 팝업 — 앱의 카드형 디자인과 맞춘 `QMessageBox.information()` 대체.

`QMessageBox.information()`은 OS 기본 스타일(아이콘·타이틀바·OK 버튼)로 뜨는데,
Phase 11-E가 폴더 관리·모델 관리·인덱싱 진행률 팝업을 카드형 디자인으로
맞춘 뒤에는 이런 네이티브 팝업만 눈에 튄다(T10.40 이후 실사용 지적). 새
색을 만들지 않고 `FolderDialog`와 같은 토큰(`PageEyebrow`·`PrimaryButton`)을
재사용한다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


def show_info(title: str, message: str, parent: QWidget | None = None) -> None:
    """확인 버튼 하나짜리 안내 팝업을 띄우고 닫힐 때까지 기다린다."""
    dialog = QDialog(parent)
    dialog.setObjectName("InfoDialog")
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(380)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(16)

    body = QLabel(message)
    body.setObjectName("InfoDialogBody")
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(body)

    buttons = QHBoxLayout()
    buttons.addStretch()
    ok_button = QPushButton("확인")
    ok_button.setObjectName("PrimaryButton")
    ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
    ok_button.clicked.connect(dialog.accept)
    buttons.addWidget(ok_button)
    layout.addLayout(buttons)

    dialog.exec()

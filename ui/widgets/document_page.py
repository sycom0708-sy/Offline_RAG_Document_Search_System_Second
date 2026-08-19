"""문서 관리 페이지 (Phase 11-A 뼈대, DESIGN §14.4).

11-A에서는 **기존 기능의 이동만** 한다 — 사이드바 하단에 있던 `폴더 관리`
버튼이 여기 `폴더 선택`으로 옮겨 왔고, 선택된 폴더 경로를 보여준다.

인덱스 작업 카드·통계 7칸·파일별 오류는 11-B에서 채운다. 지금 비어 있는
자리를 안내 문구로 채워두는 이유는, 11-A만 끝난 상태에서도 화면이 "고장난
것"이 아니라 "아직 안 만든 것"으로 읽히게 하기 위해서다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

NO_FOLDER_TEXT = "선택되지 않음"
FOLDER_BUTTON_LABEL = "폴더 선택"


def _card(parent_layout: QVBoxLayout) -> QVBoxLayout:
    """흰 카드 한 장을 만들어 그 안쪽 레이아웃을 돌려준다 (DESIGN §14.4)."""
    card = QFrame()
    card.setObjectName("PageCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)
    parent_layout.addWidget(card)
    return layout


def _eyebrow(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageEyebrow")
    return label


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageCardTitle")
    return label


class DocumentPage(QWidget):
    """문서 관리 페이지."""

    folder_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DocumentPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # --- 헤더 카드 ---
        header = _card(root)
        header.addWidget(_eyebrow("LOCAL LIBRARY"))
        header.addWidget(_title("문서 관리"))
        description = QLabel("선택한 로컬 폴더만 색인하고 상태를 추적합니다.")
        description.setObjectName("PageCardBody")
        header.addWidget(description)

        # --- 선택 폴더 카드 ---
        folder_card = _card(root)
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(12)

        folder_text = QVBoxLayout()
        folder_text.setContentsMargins(0, 0, 0, 0)
        folder_text.setSpacing(2)
        folder_text.addWidget(_eyebrow("선택 폴더"))
        self._folder_label = QLabel(NO_FOLDER_TEXT)
        self._folder_label.setObjectName("PageCardBody")
        self._folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        folder_text.addWidget(self._folder_label)
        folder_row.addLayout(folder_text, 1)

        self.folder_button = QPushButton(FOLDER_BUTTON_LABEL)
        self.folder_button.setObjectName("PrimaryButton")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.clicked.connect(self.folder_requested)
        folder_row.addWidget(self.folder_button, 0, Qt.AlignmentFlag.AlignTop)

        folder_card.addLayout(folder_row)

        # --- 11-B 자리 ---
        pending = _card(root)
        pending.addWidget(_eyebrow("INDEX OVERVIEW"))
        pending.addWidget(_title("인덱스 작업"))
        placeholder = QLabel("인덱스 진행 상황과 이번 실행 통계는 준비 중입니다.")
        placeholder.setObjectName("PageCardBody")
        pending.addWidget(placeholder)

        root.addStretch()

    def set_folder(self, folder: str | None) -> None:
        self._folder_label.setText(folder or NO_FOLDER_TEXT)

    def folder_text(self) -> str:
        """테스트·검증용."""
        return self._folder_label.text()

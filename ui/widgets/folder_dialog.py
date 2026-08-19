"""폴더 관리 진입점 (T4.17, Phase 11-E 재도색).

대상 폴더 선택 + 재인덱싱 트리거 + 실시간 감시 토글(T8.5)을 제공하는
작은 팝업이다. Phase 4에서 "최소 구현"으로 만든 뒤 Phase 11의 카드형
디자인 시스템(`PageEyebrow`/`PrimaryButton`/`SidebarFooterButton`)이
자리 잡는 동안 이 다이얼로그만 재도색을 안 받아, 문서 관리·설정 페이지의
카드들 옆에서 이 팝업만 기본 Qt 버튼·여백 없는 레이아웃으로 튀었다
(실사용 스크린샷으로 확인). 새 클래스를 만들지 않고 기존 토큰만
재사용한다 — 이 다이얼로그만 다른 색이 되면 또 하나의 예외가 생긴다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ui.widgets.toggle_switch import ToggleSwitch

NO_FOLDER_TEXT = "대상 폴더가 지정되지 않았습니다."
WATCH_TOGGLE_LABEL = "실시간 감시"
FOLDER_EYEBROW_TEXT = "대상 폴더"


class FolderDialog(QDialog):
    reindex_requested = Signal(str)  # 선택된 폴더 경로
    watch_toggled = Signal(bool)  # 실시간 감시 켬/끔 (T8.5)

    def __init__(
        self,
        current_folder: str | None,
        current_watch_enabled: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FolderDialog")
        self.setWindowTitle("폴더 관리")
        self.setMinimumWidth(420)

        self._folder = current_folder

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        eyebrow = QLabel(FOLDER_EYEBROW_TEXT)
        eyebrow.setObjectName("PageEyebrow")  # 문서 관리 카드와 같은 토큰(Phase 11-B)
        layout.addWidget(eyebrow)

        self.folder_label = QLabel()
        self.folder_label.setObjectName("FolderDialogPath")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.folder_label)
        self._refresh_label()

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.select_button = QPushButton("폴더 선택")
        self.select_button.setObjectName("PrimaryButton")
        self.select_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_button.clicked.connect(self._select_folder)
        buttons.addWidget(self.select_button)

        self.reindex_button = QPushButton("다시 인덱싱")
        self.reindex_button.setObjectName("SidebarFooterButton")  # 문서 관리 카드의 보조 버튼과 같은 톤
        self.reindex_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reindex_button.clicked.connect(self._start_reindex)
        self.reindex_button.setEnabled(current_folder is not None)
        buttons.addWidget(self.reindex_button)

        layout.addLayout(buttons)

        divider = QLabel()
        divider.setObjectName("FolderDialogDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # 폴더가 지정돼야 감시할 대상이 있다 — 없으면 비활성.
        self.watch_toggle = ToggleSwitch(WATCH_TOGGLE_LABEL)
        self.watch_toggle.setChecked(current_watch_enabled)
        self.watch_toggle.setEnabled(current_folder is not None)
        self.watch_toggle.toggled.connect(self.watch_toggled)
        layout.addWidget(self.watch_toggle)

    def _refresh_label(self) -> None:
        # 라벨 자체는 바로 위 `PageEyebrow`("대상 폴더")가 말해준다 — 여기서
        # 또 "대상 폴더: "를 붙이면 문서 관리 카드에는 없는 중복이 생긴다.
        self.folder_label.setText(self._folder if self._folder else NO_FOLDER_TEXT)

    def _select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "인덱싱할 폴더 선택", self._folder or "")
        if folder:
            self._folder = folder
            self._refresh_label()
            self.reindex_button.setEnabled(True)
            self.watch_toggle.setEnabled(True)

    def _start_reindex(self) -> None:
        if self._folder:
            self.reindex_requested.emit(self._folder)
            self.accept()

"""폴더 관리 진입점 — 최소 구현 (T4.17).

전체 "폴더 관리 화면"은 이번 Phase 범위 밖이다(TASK 문서가 별도 작업으로
분리 허용). 대상 폴더 선택 + 재인덱싱 트리거만 제공한다.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

NO_FOLDER_TEXT = "대상 폴더가 지정되지 않았습니다."


class FolderDialog(QDialog):
    reindex_requested = Signal(str)  # 선택된 폴더 경로

    def __init__(self, current_folder: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("폴더 관리")
        self.setMinimumWidth(420)

        self._folder = current_folder

        layout = QVBoxLayout(self)

        self.folder_label = QLabel()
        self.folder_label.setObjectName("FolderDialogPath")
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        self._refresh_label()

        buttons = QHBoxLayout()
        self.select_button = QPushButton("폴더 선택")
        self.select_button.clicked.connect(self._select_folder)
        buttons.addWidget(self.select_button)

        self.reindex_button = QPushButton("다시 인덱싱")
        self.reindex_button.clicked.connect(self._start_reindex)
        self.reindex_button.setEnabled(current_folder is not None)
        buttons.addWidget(self.reindex_button)

        layout.addLayout(buttons)

    def _refresh_label(self) -> None:
        text = self._folder if self._folder else NO_FOLDER_TEXT
        self.folder_label.setText(f"대상 폴더: {text}")

    def _select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "인덱싱할 폴더 선택", self._folder or "")
        if folder:
            self._folder = folder
            self._refresh_label()
            self.reindex_button.setEnabled(True)

    def _start_reindex(self) -> None:
        if self._folder:
            self.reindex_requested.emit(self._folder)
            self.accept()

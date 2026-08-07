"""문서 형식 필터 (T4.4~T4.5).

DESIGN §4.1 — "전체"의 상호작용 규칙(제안, 목업에 동작 정의 없음):
  · 전체 체크 → 나머지 전부 해제
  · 개별 형식 체크 → 전체 자동 해제
  · 개별 형식을 모두 해제 → 전체로 자동 복귀 (결과 0건 상태 방지)
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

ALL_LABEL = "전체"


class FormatFilter(QWidget):
    """선택된 확장자 집합이 바뀔 때마다 `selection_changed`를 emit한다.

    `None`은 "전체"(형식 조건 없음)를 뜻한다.
    """

    selection_changed = Signal(object)  # set[str] | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._all_checkbox = QCheckBox(ALL_LABEL)
        self._all_checkbox.setChecked(True)
        self._all_checkbox.toggled.connect(self._on_all_toggled)
        self._layout.addWidget(self._all_checkbox)

        self._format_checkboxes: dict[str, QCheckBox] = {}
        self._updating = False  # 상호 배타 로직이 서로를 재귀 호출하지 않도록

    def set_available_formats(self, extensions: list[str]) -> None:
        """인덱싱된 형식 목록으로 체크박스를 다시 그린다 (T4.4, FTS5 메타데이터 기반)."""
        for cb in self._format_checkboxes.values():
            self._layout.removeWidget(cb)
            cb.deleteLater()
        self._format_checkboxes.clear()

        for ext in sorted(extensions):
            cb = QCheckBox(ext.lstrip("."))
            cb.toggled.connect(self._on_format_toggled)
            self._layout.addWidget(cb)
            self._format_checkboxes[ext] = cb

        self._all_checkbox.setChecked(True)

    def selected_extensions(self) -> set[str] | None:
        if self._all_checkbox.isChecked():
            return None
        selected = {ext for ext, cb in self._format_checkboxes.items() if cb.isChecked()}
        return selected or None

    def _on_all_toggled(self, checked: bool) -> None:
        if self._updating:
            return

        if checked:
            self._updating = True
            for cb in self._format_checkboxes.values():
                cb.setChecked(False)
            self._updating = False
        elif not any(cb.isChecked() for cb in self._format_checkboxes.values()):
            # 전체를 해제했는데 개별 항목도 하나 없다 — 형식 조건이 하나도
            # 없어 0건이 되는 상태라 되돌린다.
            self._updating = True
            self._all_checkbox.setChecked(True)
            self._updating = False
            return

        self.selection_changed.emit(self.selected_extensions())

    def _on_format_toggled(self, _checked: bool) -> None:
        if self._updating:
            return

        any_checked = any(cb.isChecked() for cb in self._format_checkboxes.values())
        self._updating = True
        self._all_checkbox.setChecked(not any_checked)
        self._updating = False

        self.selection_changed.emit(self.selected_extensions())

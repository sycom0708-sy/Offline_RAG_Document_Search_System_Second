"""문서 형식 필터 (T4.4~T4.5).

DESIGN §4.1 — "전체"의 상호작용 규칙(제안, 목업에 동작 정의 없음):
  · 전체 체크 → 나머지 전부 해제
  · 개별 형식 체크 → 전체 자동 해제
  · 개별 형식을 모두 해제 → 전체로 자동 복귀 (결과 0건 상태 방지)

Phase 7.7: "전체"는 단독 행, 개별 형식 체크박스는 2열 그리드로 배치한다
(사용자 확정, 첨부 이미지 기준) — 세로로 길게 늘어놓던 것보다 사이드바
공간을 덜 쓴다. 채우는 순서는 **세로 우선**(1열을 다 채운 뒤 2열로) —
알파벳순 목록을 반으로 잘라 왼쪽·오른쪽 열에 나눠 담는다(가로 우선이면
`doc,docx / hwp,hwpx / pdf,txt / ...`처럼 관련 없는 형식이 한 줄에
묶이는데, 세로 우선은 `doc,pdf / docx,txt / hwp,xls / hwpx,xlsx`로
훨씬 안정적으로 보인다).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from ui.widgets.styled_checkbox import StyledCheckbox

ALL_LABEL = "전체"
GRID_COLUMNS = 2


class FormatFilter(QWidget):
    """선택된 확장자 집합이 바뀔 때마다 `selection_changed`를 emit한다.

    `None`은 "전체"(형식 조건 없음)를 뜻한다.
    """

    selection_changed = Signal(object)  # set[str] | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._all_checkbox = StyledCheckbox(ALL_LABEL)
        self._all_checkbox.setChecked(True)
        self._all_checkbox.toggled.connect(self._on_all_toggled)
        outer.addWidget(self._all_checkbox)

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(4)
        outer.addLayout(self._grid)

        self._format_checkboxes: dict[str, StyledCheckbox] = {}
        self._updating = False  # 상호 배타 로직이 서로를 재귀 호출하지 않도록

    def set_available_formats(self, extensions: list[str]) -> None:
        """인덱싱된 형식 목록으로 체크박스를 다시 그린다 (T4.4, FTS5 메타데이터 기반)."""
        for cb in self._format_checkboxes.values():
            self._grid.removeWidget(cb)
            cb.deleteLater()
        self._format_checkboxes.clear()

        sorted_extensions = sorted(extensions)
        rows = (len(sorted_extensions) + GRID_COLUMNS - 1) // GRID_COLUMNS or 1
        for i, ext in enumerate(sorted_extensions):
            cb = StyledCheckbox(ext.lstrip("."))
            cb.toggled.connect(self._on_format_toggled)
            self._grid.addWidget(cb, i % rows, i // rows)
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

"""사이드바 — 3블록 컨테이너 (DESIGN §2.2, §4).

문서 형식 필터 → 검색 옵션 3토글 → PC 성능 선택 순서로 배치한다.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from ui.widgets.format_filter import FormatFilter
from ui.widgets.performance_combo import PerformanceCombo
from ui.widgets.search_options import SearchOptions

SIDEBAR_WIDTH = 220  # DESIGN §2.2 [제안]


class Sidebar(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(24)  # DESIGN §10.5 사이드바 블록 간격

        format_label = QLabel("문서 형식")
        format_label.setObjectName("SidebarSectionLabel")
        layout.addWidget(format_label)

        self.format_filter = FormatFilter()
        layout.addWidget(self.format_filter)

        self.search_options = SearchOptions()
        layout.addWidget(self.search_options)

        self.performance_combo = PerformanceCombo()
        layout.addWidget(self.performance_combo)

        layout.addStretch()

"""사이드바 — 3블록 컨테이너 + 최근 검색 + 관리 버튼 (DESIGN §2.2, §4, Phase 7.7).

문서 형식 필터 → 검색 옵션 3토글 → PC 성능 선택 → 최근 검색 → (여백) →
모델·폴더 관리 버튼 순서로 배치한다[사용자 확정, 2026-08-13 재배치 —
최근 검색을 PC 성능 선택 바로 아래에 두고 나머지 여백은 관리 버튼 앞으로
옮겼다]. 모델·폴더 관리는 Phase 7.7 이전엔 `PerformanceCombo`(모델 관리)와
상태바(폴더 관리)에 각각 흩어져 있었는데, 목업(`rag_ui_concept_*.html`)
사이드바 하단에 두 버튼이 나란히 있어 그 자리로 옮겼다 — `PerformanceCombo`의
중복 링크는 제거했다(성능 콤보 참고).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ui.widgets.format_filter import FormatFilter
from ui.widgets.performance_combo import PerformanceCombo
from ui.widgets.recent_searches import RecentSearches
from ui.widgets.search_options import SearchOptions

SIDEBAR_WIDTH = 220  # DESIGN §2.2 [제안]
MODEL_MANAGER_BUTTON_LABEL = "모델 관리"
FOLDER_BUTTON_LABEL = "폴더 관리"


class Sidebar(QFrame):
    model_manager_requested = Signal(str)  # 현재 활성 프로파일을 실어 보낸다
    folder_requested = Signal()
    recent_search_selected = Signal(str)

    def __init__(self, parent=None, initial_profile: str | None = None) -> None:
        """`initial_profile`은 `PerformanceCombo`가 저장된 프로파일로 바로
        시작하도록 넘긴다 — 생성 후 별도 setter로 다시 맞추면 콤보의
        `refresh()`가 두 번 타 실제로 테스트 스위트를 불안정하게 만들었다
        (`performance_combo.py` 참고)."""
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(24)  # DESIGN §10.5 사이드바 블록 간격

        self._format_label = QLabel("문서 형식")
        self._format_label.setObjectName("SidebarSectionLabel")
        self._layout.addWidget(self._format_label)

        self.format_filter = FormatFilter()
        self._layout.addWidget(self.format_filter)

        self.search_options = SearchOptions()
        self._layout.addWidget(self.search_options)

        self.performance_combo = PerformanceCombo(initial_key=initial_profile)
        self._layout.addWidget(self.performance_combo)

        # PC 성능 선택 바로 아래에서 시작해 아래로 자란다 — 남는 공간은
        # 이 아래 stretch가 흡수하고, 관리 버튼은 그 뒤에서 하단에 붙는다.
        self.recent_searches = RecentSearches()
        self.recent_searches.item_selected.connect(self.recent_search_selected)
        self._layout.addWidget(self.recent_searches)

        self._layout.addStretch()

        self._footer = QHBoxLayout()
        self._footer.setContentsMargins(0, 0, 0, 0)
        self._footer.setSpacing(8)

        self.model_button = QPushButton(MODEL_MANAGER_BUTTON_LABEL)
        self.model_button.setObjectName("SidebarFooterButton")
        self.model_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_button.clicked.connect(
            lambda: self.model_manager_requested.emit(self.performance_combo.current_profile())
        )
        self._footer.addWidget(self.model_button)

        self.folder_button = QPushButton(FOLDER_BUTTON_LABEL)
        self.folder_button.setObjectName("SidebarFooterButton")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.clicked.connect(self.folder_requested)
        self._footer.addWidget(self.folder_button)

        self._layout.addLayout(self._footer)

        # 생성 시점엔 아직 실제 창 높이가 아니다(부착 전) — 여기서 계산해
        # `set_max_height(0)`을 부르면 "실측 결과 공간이 없다"와 "아직
        # 모른다"를 구분 못 해 최근 검색이 계속 안 보이게 된다. 첫
        # resizeEvent가 실제로 부를 때까지는 RecentSearches 자체의
        # 폴백(`_max_height is None`)에 맡긴다.

    def set_recent_searches(self, items: list[str]) -> None:
        self.recent_searches.set_items(items)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self._update_recent_searches_max_height()

    def _update_recent_searches_max_height(self) -> None:
        """최근 검색이 쓸 수 있는 세로 공간을 실측해 넘긴다.

        다른 고정 블록(문서 형식·검색 옵션·PC 성능·관리 버튼)의 sizeHint
        높이와 레이아웃 여백·간격을 사이드바 실제 높이에서 뺀 나머지다.
        생성 직후(첫 resizeEvent 전)엔 `self.height()`가 실제 값이 아니다
        (Phase 4·5·7·7.7에서 반복된 "부착 전/후" 함정) — 그 경우
        `RecentSearches`가 자체 대체값(`_FALLBACK_VISIBLE_COUNT`)으로
        버틴다.
        """
        margins = self._layout.contentsMargins()
        spacing = self._layout.spacing()
        fixed_height = (
            self._format_label.sizeHint().height()
            + self.format_filter.sizeHint().height()
            + self.search_options.sizeHint().height()
            + self.performance_combo.sizeHint().height()
            + self._footer.sizeHint().height()
        )
        # 레이아웃 항목 순서: 라벨·필터·옵션·콤보·최근검색·stretch·풋터 —
        # 항목 6개 사이의 간격 6칸.
        gap_count = 6
        available = (
            self.height()
            - margins.top()
            - margins.bottom()
            - fixed_height
            - spacing * gap_count
        )
        self.recent_searches.set_max_height(available)

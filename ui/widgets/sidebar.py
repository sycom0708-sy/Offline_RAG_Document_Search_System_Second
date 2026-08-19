"""사이드바 — 3페이지 네비게이션 + 확장 영역 + 최근 검색 (DESIGN §14.2, Phase 11).

Phase 7.7까지는 필터·옵션·PC 성능·최근 검색·관리 버튼을 한 줄로 쌓은 패널이었다.
Phase 11에서 **네비게이션**으로 바뀌었다 — `PC 성능 선택`·`모델 관리`는 설정
페이지로, `폴더 관리`는 문서 관리 페이지의 `폴더 선택`으로 옮겼고,
`문서 형식`·`AI 챗봇 사용`은 `검색/대화` 옆 확장 영역으로 들어갔다.
`대/소문자 구분`·`일치되는 단어`는 화면에서 제거했다(기능은 유지, DESIGN §14.7).

🔴 **확장 버튼은 네비게이션과 독립이다** — `검색/대화` 항목 본체를 누르면
페이지 이동만, 오른쪽 확장 버튼을 누르면 펼침/접힘만 일어난다. 하나로 묶으면
"필터 좀 보려고 눌렀는데 페이지가 바뀐다"가 된다.

최근 검색은 **위치를 유지했다**[사용자 확정] — Phase 7.7에서 자리잡은 그대로
사이드바 아래쪽에 남는다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.format_filter import FormatFilter
from ui.widgets.recent_searches import RecentSearches
from ui.widgets.search_options import SearchOptions

SIDEBAR_WIDTH = 220  # DESIGN §2.2 [제안]
APP_TITLE = "오프라인 문서 검색"

PAGE_SEARCH = "search"
PAGE_DOCUMENTS = "documents"
PAGE_SETTINGS = "settings"

NAV_LABELS = {
    PAGE_SEARCH: "검색/대화",
    PAGE_DOCUMENTS: "문서 관리",
    PAGE_SETTINGS: "설정",
}

EXPAND_COLLAPSED_TEXT = "⌄"
EXPAND_EXPANDED_TEXT = "⌃"


class _NavButton(QPushButton):
    """네비게이션 항목 하나. 활성 상태는 QSS의 `[active="true"]`로 표현한다."""

    def __init__(self, page: str, parent=None) -> None:
        super().__init__(NAV_LABELS[page], parent)
        self.page = page
        self.setObjectName("SidebarNavButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(False)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        # QSS 동적 프로퍼티는 스타일을 다시 계산해줘야 화면에 반영된다.
        self.style().unpolish(self)
        self.style().polish(self)


class Sidebar(QFrame):
    page_requested = Signal(str)
    expand_toggled = Signal(bool)
    recent_search_selected = Signal(str)

    def __init__(self, parent=None, expanded: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 12, 16, 12)
        # 블록 간격을 좁게 잡는다[사용자 확정, 2026-08-18] — 항목이 7개라
        # 간격이 16이면 6칸에 96px를 쓰고, 확장을 펼친 낮은 창에서는 그만큼
        # 최근 검색이 사이드바 밖으로 밀려난다.
        self._layout.setSpacing(10)

        self._title = QLabel(APP_TITLE)
        self._title.setObjectName("SidebarTitle")
        self._layout.addWidget(self._title)

        # --- 검색/대화 + 확장 버튼 (한 행) ---
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(4)

        self._nav_buttons: dict[str, _NavButton] = {}
        search_button = _NavButton(PAGE_SEARCH)
        search_button.clicked.connect(lambda: self.page_requested.emit(PAGE_SEARCH))
        self._nav_buttons[PAGE_SEARCH] = search_button
        search_row.addWidget(search_button, 1)

        self.expand_button = QPushButton(EXPAND_COLLAPSED_TEXT)
        self.expand_button.setObjectName("SidebarExpandButton")
        self.expand_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_button.setFixedWidth(28)
        self.expand_button.setToolTip("검색 옵션 펼치기/접기")
        self.expand_button.clicked.connect(self._on_expand_clicked)
        search_row.addWidget(self.expand_button, 0)

        self._layout.addLayout(search_row)

        # --- 확장 영역 ---
        self._expansion = QWidget()
        self._expansion.setObjectName("SidebarExpansion")
        expansion_layout = QVBoxLayout(self._expansion)
        expansion_layout.setContentsMargins(8, 0, 0, 0)
        expansion_layout.setSpacing(12)

        self.search_options = SearchOptions()
        expansion_layout.addWidget(self.search_options)

        self._format_label = QLabel("문서 형식")
        self._format_label.setObjectName("SidebarSectionLabel")
        expansion_layout.addWidget(self._format_label)

        self.format_filter = FormatFilter()
        expansion_layout.addWidget(self.format_filter)

        self._layout.addWidget(self._expansion)

        # --- 나머지 네비게이션 ---
        for page in (PAGE_DOCUMENTS, PAGE_SETTINGS):
            button = _NavButton(page)
            button.clicked.connect(lambda _=False, p=page: self.page_requested.emit(p))
            self._nav_buttons[page] = button
            self._layout.addWidget(button)

        # --- 최근 검색 ---
        # 🔴 stretch가 **앞에** 온다 — 최근 검색을 사이드바 하단에 붙이기
        # 위해서다[사용자 확정, 2026-08-18]. 위젯 자체는 10건 자리를 미리
        # 잡으므로(`RecentSearches._RESERVED_ROWS`) 검색이 쌓여도 블록의
        # 위치가 흔들리지 않는다.
        self._layout.addStretch()

        self.recent_searches = RecentSearches()
        self.recent_searches.item_selected.connect(self.recent_search_selected)
        self._layout.addWidget(self.recent_searches)

        self._expanded = not expanded  # set_expanded가 실제로 반영하도록 반대로 둔다
        self.set_expanded(expanded)
        self.set_active_page(PAGE_SEARCH)

    # --- 확장 -------------------------------------------------------

    def _on_expand_clicked(self) -> None:
        self.set_expanded(not self._expanded)
        self.expand_toggled.emit(self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._expansion.setVisible(expanded)
        self.expand_button.setText(
            EXPAND_EXPANDED_TEXT if expanded else EXPAND_COLLAPSED_TEXT
        )
        self._update_recent_searches_max_height()

    def is_expanded(self) -> bool:
        return self._expanded

    # --- 네비게이션 -------------------------------------------------

    def set_active_page(self, page: str) -> None:
        for key, button in self._nav_buttons.items():
            button.set_active(key == page)

    # --- 최근 검색 --------------------------------------------------

    def set_recent_searches(self, items: list[str]) -> None:
        self.recent_searches.set_items(items)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self.refresh_recent_searches_space()

    def refresh_recent_searches_space(self) -> None:
        """창 크기가 바뀌었을 때 바깥(`MainWindow`)에서도 부른다.

        🔴 사이드바 자신의 `resizeEvent`만 믿으면 안 된다. 최근 검색이 고정
        높이를 잡으면 그것이 사이드바의 **최소 높이**가 되어, 창을 그보다
        작게 줄여도 사이드바 높이는 그대로 멈춘다 — 높이가 안 바뀌니
        `resizeEvent`도 안 오고, 재계산이 영영 돌지 않아 최근 검색이 창 밖에
        걸린 채 갇힌다(실측: 창 450px인데 사이드바 706px 고정). 창 쪽에서
        직접 불러 이 고리를 끊는다.
        """
        self._update_recent_searches_max_height()

    def _update_recent_searches_max_height(self) -> None:
        """최근 검색이 쓸 수 있는 세로 공간을 실측해 넘긴다.

        🔴 **위젯 높이를 손으로 더해 추정하지 않는다.** 예전에는 각 블록의
        `sizeHint()`를 합쳐 뺐는데, 실제 배치 높이와 어긋나 낮은 창에서 최근
        검색이 사이드바 밖으로 밀려났다(실측: 창 650px에서 bottom 694px).
        대신 **이미 배치가 끝난 `설정` 버튼의 실제 좌표**를 기준점으로 쓴다 —
        위쪽 블록들은 레이아웃이 먼저 확정하므로 이 좌표는 항상 정확하고,
        넘치는 것은 언제나 아래쪽(최근 검색)뿐이다.

        🔴 **기준 높이로 `self.height()`만 믿으면 안 된다.** 최근 검색이 자리를
        잡고 확장까지 펼치면 사이드바의 최소 높이가 창보다 커지고, 그러면
        `self.height()`가 창을 넘은 값으로 부풀어 오른다(실측: 창 500px에
        사이드바 719px). 그 값으로 계산하면 "자리가 넉넉하다"는 잘못된 결론이
        나온다 — 자기 크기가 입력이자 결과가 되는 순환이다. 창 높이와 비교해
        더 작은 쪽에서 끊는다.

        생성 직후(첫 resizeEvent 전)엔 좌표가 아직 실제 값이 아니므로
        (Phase 4·5·7·7.7에서 반복된 "부착 전/후" 함정) `RecentSearches`의
        자체 대체값에 맡긴다.
        """
        anchor = self._nav_buttons[PAGE_SETTINGS].geometry().bottom()
        if anchor <= 0:
            return  # 아직 배치 전 — RecentSearches의 폴백에 맡긴다

        basis = self.height()
        window = self.window()
        if window is not None and window.height() > 0:
            basis = min(basis, window.height())

        margins = self._layout.contentsMargins()
        available = basis - anchor - self._layout.spacing() - margins.bottom()
        if available <= 0:
            # 아직 제대로 배치되지 않았다(창을 띄우지 않은 상태 등) — 0을 넘기면
            # "자리가 없다"로 읽혀 목록이 통째로 사라진다. 판단을 미루고
            # `RecentSearches`의 폴백에 맡긴다.
            return
        self.recent_searches.set_max_height(available)

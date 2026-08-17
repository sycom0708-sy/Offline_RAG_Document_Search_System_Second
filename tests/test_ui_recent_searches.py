"""사이드바 "최근 검색" 위젯 테스트 (Phase 7.7 재작업, 2026-08-13).

PC 성능 선택 바로 아래에서 시작해 아래로 자라고, 공간이 부족하면 아래쪽
(오래된 항목)부터 빠지는 동작을 검증한다 — `AppState.recent_searches`와
같은 순서(최신이 맨 위)로 그대로 보여준다(T10.22, 2026-08-15 사용자 요청).
그 전에는 뒤집어서 최신을 맨 아래에 뒀는데, 방금 쓴 검색어를 목록 아래에서
찾아야 해서 불편하다는 지적이 있었다.
"""

from __future__ import annotations

from ui.widgets.recent_searches import RecentSearches
from ui.widgets.sidebar import Sidebar

# newest-first, AppState.recent_searches와 같은 순서.
_NEWEST_FIRST = ["n1", "n2", "n3", "n4", "n5"]


class TestRecentSearchesGrowthAndEviction:
    def test_no_items_hides_the_widget(self, qtbot):
        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_items([])
        assert widget.isVisibleTo(widget.parentWidget() or widget) is False or not widget.isVisible()

    def test_before_any_max_height_uses_fallback_count(self, qtbot):
        """`set_max_height()`가 아직 안 불린 상태(첫 resizeEvent 전)에서도
        일부는 보여야 한다 — 안 그러면 창이 뜨기 전까지 목록이 통째로
        비어 보인다."""
        widget = RecentSearches()
        qtbot.addWidget(widget)

        widget.set_items(_NEWEST_FIRST)

        assert widget._list_layout.count() == 5  # fallback = 5, 저장도 5건뿐

    def test_items_render_newest_at_top(self, qtbot):
        """T10.22: 마지막 검색이 맨 위여야 한다(사용자 요청)."""
        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_max_height(10_000)  # 넉넉한 공간 — 5건 모두 표시

        widget.set_items(_NEWEST_FIRST)

        texts = [widget._list_layout.itemAt(i).widget().toolTip() for i in range(widget._list_layout.count())]
        assert texts == ["n1", "n2", "n3", "n4", "n5"]  # 최신(n1) 위 → 오래된(n5) 아래

    def test_limited_height_keeps_only_the_newest_and_evicts_from_bottom(self, qtbot):
        """공간이 2줄만 허용하면 최신 2건(n1, n2)만 남고 나머지(오래된 것부터)는 빠진다."""
        widget = RecentSearches()
        qtbot.addWidget(widget)
        row_height = widget._row_height
        label_height = widget._label.sizeHint().height() + widget._outer.spacing()

        widget.set_max_height(label_height + row_height * 2 + 1)
        widget.set_items(_NEWEST_FIRST)

        texts = [widget._list_layout.itemAt(i).widget().toolTip() for i in range(widget._list_layout.count())]
        assert texts == ["n1", "n2"]  # n3~n5(오래된 항목)는 빠지고 최신 2건만 남는다

    def test_zero_or_negative_max_height_shows_nothing(self, qtbot):
        widget = RecentSearches()
        qtbot.addWidget(widget)

        widget.set_max_height(0)
        widget.set_items(_NEWEST_FIRST)

        assert widget._list_layout.count() == 0
        assert widget.isVisible() is False

    def test_adding_a_newer_search_appears_at_the_top(self, qtbot):
        """T10.22: 새 검색이 추가되면 화면 맨 위에 나타나야 한다."""
        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_max_height(10_000)
        widget.set_items(["b", "a"])  # b가 최신

        widget.set_items(["c", "b", "a"])  # c를 새로 추가(newest-first 맨 앞)

        texts = [widget._list_layout.itemAt(i).widget().toolTip() for i in range(widget._list_layout.count())]
        assert texts[0] == "c"  # 새로 추가된 항목이 맨 위

    def test_click_emits_full_query_even_when_elided(self, qtbot):
        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_max_height(10_000)
        long_query = "아주 길어서 화면에 다 안 들어가고 잘려야 하는 매우 긴 검색어 예시입니다"
        widget.set_items([long_query])

        received = []
        widget.item_selected.connect(received.append)
        button = widget._list_layout.itemAt(0).widget()
        assert button.text() != long_query  # 실제로 잘렸는지 확인
        button.click()

        assert received == [long_query]


class TestSidebarRecentSearchesPlacement:
    def test_recent_searches_comes_right_after_performance_combo(self, qtbot):
        """PC 성능 선택 바로 아래에서 시작해야 한다[사용자 확정, 2026-08-13]."""
        sidebar = Sidebar()
        qtbot.addWidget(sidebar)

        combo_index = sidebar._layout.indexOf(sidebar.performance_combo)
        recent_index = sidebar._layout.indexOf(sidebar.recent_searches)

        assert recent_index == combo_index + 1

    def test_stretch_sits_between_recent_searches_and_footer(self, qtbot):
        """관리 버튼(풋터)이 하단에 붙도록, stretch는 최근 검색 다음·풋터 이전에 있어야 한다."""
        sidebar = Sidebar()
        qtbot.addWidget(sidebar)

        recent_index = sidebar._layout.indexOf(sidebar.recent_searches)
        stretch_item = sidebar._layout.itemAt(recent_index + 1)

        assert stretch_item.spacerItem() is not None

    def test_resize_feeds_available_height_to_recent_searches(self, qtbot):
        sidebar = Sidebar()
        qtbot.addWidget(sidebar)
        sidebar.show()
        sidebar.resize(220, 900)  # 넉넉한 높이 — 최근 검색이 여러 건 보일 수 있어야 한다
        qtbot.wait(50)

        assert sidebar.recent_searches._max_height is not None
        assert sidebar.recent_searches._max_height > 0

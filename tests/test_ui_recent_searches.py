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
    def test_recent_searches_is_pinned_to_the_bottom(self, qtbot):
        """최근 검색은 사이드바 **맨 아래**에 붙는다[사용자 확정, 2026-08-18].

        stretch가 최근 검색 **앞에** 있어야 아래로 밀린다. 순서가 뒤집히면
        목록이 네비게이션 바로 밑에 딸려 올라간다.
        """
        sidebar = Sidebar()
        qtbot.addWidget(sidebar)

        recent_index = sidebar._layout.indexOf(sidebar.recent_searches)

        assert recent_index == sidebar._layout.count() - 1  # 마지막 항목
        assert sidebar._layout.itemAt(recent_index - 1).spacerItem() is not None

    def test_reserves_room_for_the_full_list(self, qtbot):
        """10건 자리를 미리 잡아, 검색이 쌓여도 블록 위치가 흔들리지 않는다.

        [사용자 확정, 2026-08-18] 항목이 늘 때마다 목록이 위로 자라면 눈이 계속
        따라가야 한다.
        """
        from ui.widgets.recent_searches import RecentSearches

        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_max_height(1000)  # 10건이 충분히 들어가는 높이

        widget.set_items(["검색어 1"])
        one_item_height = widget.height()

        widget.set_items([f"검색어 {i}" for i in range(10)])

        assert widget.height() == one_item_height  # 1건일 때도 10건 자리를 잡고 있다

    def test_capacity_change_is_not_swallowed_by_the_rerender_guard(self, qtbot):
        """🔴 깜박임 억제가 자리 확보까지 막으면 안 된다.

        표시 개수만 비교하면, 항목이 1건인데 창이 커져 10건 자리를 새로 잡아야
        하는 상황에서 개수가 그대로라 갱신이 통째로 막힌다 — 실제로 겪었고
        (블록이 아래로 내려간 채 고정되지 않았다), 확보 높이도 함께 비교하도록
        고쳤다.
        """
        from ui.widgets.recent_searches import RecentSearches

        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_items(["검색어 하나"])
        widget.set_max_height(80)  # 자리가 좁다
        cramped = widget.height()

        widget.set_max_height(1000)  # 넓어졌으니 10건 자리를 잡아야 한다

        assert widget.height() > cramped

    def test_resize_feeds_available_height_to_recent_searches(self, qtbot):
        sidebar = Sidebar()
        qtbot.addWidget(sidebar)
        sidebar.show()
        sidebar.resize(220, 900)  # 넉넉한 높이 — 최근 검색이 여러 건 보일 수 있어야 한다
        qtbot.wait(50)

        assert sidebar.recent_searches._max_height is not None
        assert sidebar.recent_searches._max_height > 0


class TestNoRerenderChurnOnResize:
    """창 크기를 드래그로 바꿀 때 목록을 다시 그리지 않는다 (2026-08-18 사용자 보고).

    🔴 증상: "창 상/하 사이즈를 변경할 때마다 수많은 팝업이 떴다 사라진다."
    `set_max_height()`가 리사이즈마다 무조건 `_render()`를 불렀고, `_render()`는
    목록 위젯을 전부 지웠다 새로 만든다. 드래그 중에는 resizeEvent가 초당 수십 번
    들어오므로 그만큼 반복됐다. 게다가 항목 버튼을 **부모 없이** 만들고 있었는데,
    부모 없는 QWidget은 Qt에서 최상위 창이라 레이아웃에 들어가기 전 순간에 별도
    창처럼 보일 수 있다.
    """

    def test_same_visible_count_does_not_rerender(self, qtbot):
        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_items([f"검색어 {i}" for i in range(5)])
        widget.set_max_height(400)

        calls = []
        original = widget._render
        widget._render = lambda: calls.append(1) or original()

        # 개수가 바뀌지 않는 범위에서 높이만 흔든다.
        for px in (399, 398, 397, 396, 395, 396, 397, 398, 399, 400):
            widget.set_max_height(px)

        assert calls == [], f"높이만 바뀌었는데 {len(calls)}번 다시 그렸다"

    def test_changing_visible_count_still_rerenders(self, qtbot):
        """억제가 지나쳐 갱신 자체가 막히면 안 된다."""
        from PySide6.QtWidgets import QPushButton

        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_items([f"검색어 {i}" for i in range(10)])

        widget.set_max_height(400)
        many = len(widget.findChildren(QPushButton))

        widget.set_max_height(120)
        few = len(widget.findChildren(QPushButton))

        assert many > few > 0

    def test_item_buttons_are_parented_on_creation(self, qtbot):
        """부모 없는 위젯은 최상위 창이 된다 — 생성 시점에 부모를 준다."""
        from PySide6.QtWidgets import QPushButton

        widget = RecentSearches()
        qtbot.addWidget(widget)
        widget.set_items(["계약서"])
        widget.set_max_height(400)

        for button in widget.findChildren(QPushButton):
            assert button.parent() is not None
            assert not button.isWindow()

"""사이드바 "최근 검색" 목록 (Phase 7.7).

PC 성능 선택 바로 아래에서 시작해 아래 방향으로 자란다 — 오래된 항목이
위, 최신 검색이 아래에 쌓인다. 사이드바에 남는 공간만큼만 보여주고,
공간이 부족해지면 위쪽(오래된 항목)부터 빠진다[사용자 확정, 2026-08-13]
— 대화창이 아래로 스크롤되며 오래된 메시지가 위로 밀려나는 것과 같은
방향 감각이다.

저장은 `AppState.recent_searches`(최신이 맨 앞, 최대
`ui.state.RECENT_SEARCHES_LIMIT`건)에 그대로 하고, 이 위젯은 `Sidebar.
resizeEvent`가 실측해 넘겨주는 가용 높이(`set_max_height()`)에 맞춰 보여줄
개수만 스스로 정한다.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QLayout, QPushButton, QVBoxLayout, QWidget

SECTION_LABEL = "최근 검색"
# 실제 배치 전(첫 resizeEvent 전)에는 가용 높이를 알 수 없다 — Phase
# 4·5·7·7.7에서 반복된 "부착 전/후" 함정과 같은 종류다. 잠정적으로 이만큼만
# 보여주고 resizeEvent가 실제 높이로 바로잡는다(챗봇 말풍선 최대 폭 계산과
# 같은 패턴).
_FALLBACK_VISIBLE_COUNT = 5
# 목록이 늘어도 블록 위치가 흔들리지 않도록 **10건 자리를 미리 잡는다**
# [사용자 확정, 2026-08-18]. `AppState.RECENT_SEARCHES_LIMIT`와 같은 값이다 —
# 그보다 많이 쌓이지 않으므로 이 높이면 항상 충분하다.
_RESERVED_ROWS = 10
# 사이드바 폭(220px) - 좌우 여백(16px * 2) = 188px. eliding 계산에 쓴다.
_ITEM_WIDTH = 188
# QSS #RecentSearchItem의 padding(5px 0)만큼 폰트 높이에 더한다 — 근사치.
_ROW_PADDING = 10


class RecentSearches(QWidget):
    item_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._label = QLabel(SECTION_LABEL)
        self._label.setObjectName("SidebarSectionLabel")

        self._list_layout = QVBoxLayout()
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(6)
        # 🔴 안쪽 내용이 이 위젯의 최소 높이를 밀어올리지 못하게 한다. 그대로
        # 두면 목록 높이가 사이드바를 거쳐 **창 전체의 최소 높이**가 되어,
        # 창을 그 아래로 줄일 수 없게 된다(실측: 창 최소 706px에 갇힘).
        self._outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._outer.addWidget(self._label)
        self._outer.addLayout(self._list_layout)
        # 🔴 stretch가 없으면 예약해 둔 10건분 여유 공간을 **라벨 자신**이
        # 늘어나서 채운다(QLabel의 기본 세로 정책이 Preferred라 이 레이아웃
        # 안에서 유일하게 자랄 수 있는 위젯이었다) — "최근 검색" 라벨과 항목
        # 사이에 큰 빈틈이 생기고 항목이 위젯 맨 아래로 밀려나는 원인이었다
        # (실사용 보고, T10.49). 여유는 라벨 밑, 항목들 뒤에 붙는 stretch가
        # 흡수해야 라벨+항목이 위쪽에 붙어 있는다.
        self._outer.addStretch()

        self._all_items: list[str] = []  # 최신이 맨 앞(AppState와 같은 순서)
        self._max_height: int | None = None
        # 지금 화면에 실제로 그려져 있는 항목 수와, 확보해 둔 자리(행 수).
        # 둘 다 그대로면 다시 그릴 이유가 없다(아래 set_max_height 참고).
        self._rendered_count: int | None = None
        self._rendered_capacity: int | None = None
        # 자리로 잡아둔 높이. sizeHint로 돌려줘 "공간이 있으면 이만큼"이 되게
        # 하되, 최소 높이로는 쓰지 않는다(위 SetNoConstraint 참고).
        self._reserved_height: int | None = None
        self._row_height = QFontMetrics(self.font()).height() + _ROW_PADDING

        self.set_items([])

    def set_items(self, items: list[str]) -> None:
        """`AppState.recent_searches`와 같은 순서(최신이 맨 앞)로 받는다."""
        self._all_items = items
        self._render()

    def set_max_height(self, px: int) -> None:
        """`Sidebar.resizeEvent`가 남는 공간을 계산해 알려준다.

        🔴 **높이가 바뀔 때마다 다시 그리지 않는다.** 창 아래쪽 테두리를
        드래그하는 동안 `resizeEvent`가 초당 수십 번 들어오는데, 그때마다
        목록 위젯을 전부 지웠다 새로 만들면 화면이 심하게 깜박인다 —
        실사용에서 "리사이즈할 때마다 수많은 팝업이 떴다 사라진다"로
        보고됐다(2026-08-18). 실제로 보여줄 개수가 달라질 때만 다시 그린다.
        """
        self._max_height = px
        # 🔴 표시 개수뿐 아니라 **확보 높이**도 함께 본다. 개수만 비교하면,
        # 항목이 1건인데 창이 커져 10건 자리를 잡아야 하는 상황에서 개수가
        # 그대로라 갱신이 통째로 막힌다(실제로 겪었다 — 블록이 아래로 내려간
        # 채 고정되지 않았다).
        target_count = min(self._visible_count(), len(self._all_items))
        target_capacity = min(self._visible_count(), _RESERVED_ROWS)
        if (
            target_count != self._rendered_count
            or target_capacity != self._rendered_capacity
        ):
            self._render()

    def _visible_count(self) -> int:
        if self._max_height is None:
            # 아직 실측 전(첫 resizeEvent 전)이다 — 0은 "실측 결과 공간이
            # 없다"는 뜻이라 폴백과 구분해야 한다.
            return _FALLBACK_VISIBLE_COUNT
        if self._max_height <= 0:
            return 0
        label_height = self._label.sizeHint().height() + self._outer.spacing()
        available = self._max_height - label_height
        if available <= 0:
            return 0
        return available // self._row_height

    def _render(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        count = min(self._visible_count(), len(self._all_items))
        # 최신이 맨 위다(T10.22, 사용자 요청) — `_all_items`가 이미 최신순
        # (`AppState.add_recent_search()`가 맨 앞에 넣는다)이라 앞에서부터
        # count건을 그대로 쓴다. 공간이 부족해지면 아래쪽(오래된 항목)부터
        # 빠진다. 예전에는 뒤집어서 최신을 아래에 쌓았는데, 새로 검색할
        # 때마다 방금 쓴 검색어를 목록 아래에서 찾아야 했다.
        visible = self._all_items[:count] if count else []
        self._rendered_count = count

        # 실제 항목 수와 무관하게 10건 높이를 확보한다 — 검색을 할수록 목록이
        # 자라면서 블록이 위로 밀려 올라가면 눈이 계속 따라가야 한다. 다만
        # 창이 짧아 그만큼 못 쓰는 상황(`_visible_count()`가 10 미만)에서는
        # 쓸 수 있는 만큼만 잡는다 — 안 그러면 사이드바 밖으로 넘친다.
        capacity = min(self._visible_count(), _RESERVED_ROWS)
        self._rendered_capacity = capacity
        self._reserved_height = (
            self._label.sizeHint().height()
            + self._outer.spacing()
            + capacity * self._row_height
        )
        # 상한만 건다. 최소는 0으로 열어둬야 창이 작아질 때 이 블록이 먼저
        # 양보하고, 창의 최소 높이가 목록 길이에 끌려가지 않는다.
        self.setMinimumHeight(0)
        self.setMaximumHeight(self._reserved_height)
        # 레이아웃 안에 있으면 부모가 곧 다시 배치하므로 이 resize는 덮어써진다.
        # 부모 레이아웃 없이 단독으로 쓰이는 경우(테스트 등)에는 아무도 크기를
        # 정해주지 않으므로, 예약한 높이를 여기서 직접 반영해야 한다 — 예전
        # `setFixedHeight`가 겸하던 역할이다.
        self.resize(self.width(), self._reserved_height)
        self.updateGeometry()

        self.setVisible(bool(visible))
        for query in visible:
            # 🔴 부모를 생성 시점에 준다. 부모 없는 QWidget은 Qt에서 최상위
            # 창이라, 레이아웃에 넣기 전 짧은 순간이라도 화면에 별도 창처럼
            # 튀어나올 수 있다 — 위 재렌더 억제와 함께 깜박임의 원인이었다.
            button = QPushButton(self)
            button.setObjectName("RecentSearchItem")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFlat(True)
            button.setToolTip(query)  # 잘린 전체 문구는 툴팁으로
            metrics = QFontMetrics(button.font())
            elided = metrics.elidedText(query, Qt.TextElideMode.ElideRight, _ITEM_WIDTH)
            button.setText(elided)
            button.clicked.connect(lambda _checked=False, q=query: self.item_selected.emit(q))
            self._list_layout.addWidget(button)

    def sizeHint(self) -> QSize:
        """자리로 잡아둔 높이를 선호 크기로 돌려준다.

        최소 높이가 아니라 **선호 크기**여야 한다 — 공간이 넉넉하면 이만큼을
        차지해 블록 위치가 고정되고, 공간이 모자라면 그냥 줄어든다.
        """
        base = super().sizeHint()
        if self._reserved_height is None:
            return base
        return QSize(base.width(), self._reserved_height)

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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

SECTION_LABEL = "최근 검색"
# 실제 배치 전(첫 resizeEvent 전)에는 가용 높이를 알 수 없다 — Phase
# 4·5·7·7.7에서 반복된 "부착 전/후" 함정과 같은 종류다. 잠정적으로 이만큼만
# 보여주고 resizeEvent가 실제 높이로 바로잡는다(챗봇 말풍선 최대 폭 계산과
# 같은 패턴).
_FALLBACK_VISIBLE_COUNT = 5
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
        self._outer.addWidget(self._label)
        self._outer.addLayout(self._list_layout)

        self._all_items: list[str] = []  # 최신이 맨 앞(AppState와 같은 순서)
        self._max_height: int | None = None
        self._row_height = QFontMetrics(self.font()).height() + _ROW_PADDING

        self.set_items([])

    def set_items(self, items: list[str]) -> None:
        """`AppState.recent_searches`와 같은 순서(최신이 맨 앞)로 받는다."""
        self._all_items = items
        self._render()

    def set_max_height(self, px: int) -> None:
        """`Sidebar.resizeEvent`가 남는 공간을 계산해 알려준다."""
        self._max_height = px
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
        # 오래된 → 최신 순으로 뒤집은 뒤 뒤쪽(최신) count건만 남긴다 —
        # 공간이 부족해지면 위쪽(오래된 항목)부터 빠지고, 새 검색은 아래에
        # 쌓인다.
        visible = list(reversed(self._all_items))[-count:] if count else []

        self.setVisible(bool(visible))
        for query in visible:
            button = QPushButton()
            button.setObjectName("RecentSearchItem")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFlat(True)
            button.setToolTip(query)  # 잘린 전체 문구는 툴팁으로
            metrics = QFontMetrics(button.font())
            elided = metrics.elidedText(query, Qt.TextElideMode.ElideRight, _ITEM_WIDTH)
            button.setText(elided)
            button.clicked.connect(lambda _checked=False, q=query: self.item_selected.emit(q))
            self._list_layout.addWidget(button)

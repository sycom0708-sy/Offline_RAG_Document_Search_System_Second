"""결과 리스트 — 스크롤 + 상태별 화면 (T4.15, DESIGN §7).

검색바·사이드바·상태바는 고정하고 이 영역만 스크롤한다 (DESIGN §2.3).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from parser.schema import ChunkType
from search.hybrid_search import HybridResult
from ui.widgets.image_card import ImageCard
from ui.widgets.result_card import ResultCard
from ui.widgets.table_card import TableCard

INITIAL_MESSAGE = "검색어를 입력해 문서를 찾아보세요."
SEARCHING_MESSAGE = "검색 중…"
NO_INDEX_MESSAGE = "먼저 대상 폴더를 지정해 주세요."
NO_RESULTS_MESSAGE = "검색 결과가 없습니다."
ERROR_MESSAGE_PREFIX = "검색 중 오류가 발생했습니다: "


class ResultList(QScrollArea):
    # 카드(ResultCard/TableCard/ImageCard)가 각자 내보내는 open_failed를 이
    # 한 자리로 모은다 — 세 카드 타입 각각에 MainWindow가 개별 연결할 필요가
    # 없다. 지금까지 이 연결 자체가 없어 "원문 열기"가 실패해도 사용자에게
    # 아무 알림도 가지 않았다(신호는 emit되지만 받는 곳이 없었다).
    open_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setObjectName("ResultList")
        self.setFrameShape(self.Shape.NoFrame)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)  # DESIGN §10.5 카드 간 간격
        self._layout.addStretch()
        self.setWidget(self._container)

        self.show_initial()

    def _clear(self) -> None:
        """이전 카드·메시지를 치운다.

        `deleteLater()`만 부르면 실제 파괴는 다음 이벤트 루프까지 미뤄져,
        그 사이엔 같은 objectName의 이전 위젯이 `findChild` 등으로 여전히
        붙잡힌다(실측 확인). `setParent(None)`으로 자식 트리에서 즉시
        떼어낸 뒤 파괴를 예약한다.
        """
        while self._layout.count() > 1:  # 마지막 stretch는 남긴다
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _show_message(self, text: str) -> None:
        self._clear()
        label = QLabel(text)
        label.setObjectName("ResultListMessage")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        self._layout.insertWidget(0, label)

    def show_initial(self) -> None:
        self._show_message(INITIAL_MESSAGE)

    def show_searching(self) -> None:
        self._show_message(SEARCHING_MESSAGE)

    def show_no_index(self) -> None:
        self._show_message(NO_INDEX_MESSAGE)

    def show_error(self, message: str) -> None:
        self._show_message(f"{ERROR_MESSAGE_PREFIX}{message}")

    def show_empty(self, hint: str | None = None) -> None:
        """DESIGN §7: 형식 필터·검색 옵션이 원인일 수 있어 완화 힌트를 함께 준다."""
        text = NO_RESULTS_MESSAGE if not hint else f"{NO_RESULTS_MESSAGE}\n{hint}"
        self._show_message(text)

    def show_results(
        self,
        results: list[HybridResult],
        query: str,
        case_sensitive: bool = False,
        exact_word: bool = False,
    ) -> None:
        self._clear()
        for result in results:
            card = _make_card(result, query, case_sensitive, exact_word)
            card.open_failed.connect(self.open_failed)
            self._layout.insertWidget(self._layout.count() - 1, card)

    def card_count(self) -> int:
        """텍스트/표/이미지 세 카드 타입 모두 `objectName("ResultCard")`를
        공유한다(DESIGN §5.1 공통 프레임) — 타입별 isinstance 대신 이걸로 센다."""
        return sum(
            1
            for i in range(self._layout.count())
            if (widget := self._layout.itemAt(i).widget()) is not None
            and widget.objectName() == "ResultCard"
        )


def _make_card(
    result: HybridResult,
    query: str,
    case_sensitive: bool,
    exact_word: bool,
) -> QWidget:
    """청크 타입에 따라 카드를 분기한다 (T5.1, DESIGN §5.7) — 검색 로직은
    타입과 무관하게 동일하고, 렌더링 단계에서만 갈린다."""
    if result.type is ChunkType.TABLE:
        return TableCard(result)
    if result.type is ChunkType.IMAGE:
        return ImageCard(result)
    return ResultCard(result, query, case_sensitive, exact_word)

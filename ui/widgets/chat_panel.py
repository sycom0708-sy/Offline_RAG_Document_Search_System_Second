"""AI 챗봇 패널 (T7.6) — 검색 결과 영역을 통째로 차지하는 채팅 화면.

Phase 7의 "AI 요약 보기"(검색마다 자동 1회 요약)를 대체한다. 실측(속도
79초 vs 은행 앱 수준 요구)이 부딪혀 응답을 2단으로 나눴다
[사용자 확정, 2026-08-11]:

    1단계(즉시, LLM 미사용)  hybrid_search()의 top-1 발췌를 원문 그대로 보여준다
                             (검색 지연 7~14ms — 이미 측정된 값)
    2단계(선택, LLM 사용)    "AI 요약 보기"를 눌렀을 때만 SummaryWorker가 돈다.
                             그 턴이 1단계에서 이미 받아둔 결과를 그대로 넘기므로
                             검색을 다시 하지 않는다.

이 패널 자체는 워커를 만들지 않는다 — SearchWorker/SummaryWorker 기동은
MainWindow가 하고, 이 패널은 신호로 요청하고 결과를 받아 그리기만 한다
(sqlite·sLM 서비스에 직접 접근하지 않는다 — SearchWorker/SummaryWorker가
이미 그 경계를 지키는 것과 같은 이유).

메시지마다 독립 처리(stateless)다 — 매번 검색어 전체 범위로 새로 검색한다.
"두 번째는요?" 같은 대명사 참조는 지원하지 않는다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from search.chunk_view import format_location
from search.hybrid_search import HybridResult
from slm.summarize import Summary
from ui.widgets.card_common import open_source_file
from ui.widgets.summary_card import SummaryCard

SEND_BUTTON_LABEL = "보내기"
INPUT_PLACEHOLDER = "질문을 입력하세요"
SUMMARIZE_BUTTON_LABEL = "AI 요약 보기"
OPEN_BUTTON_LABEL = "파일 열기 ↗"
SEARCHING_TEXT = "검색하는 중…"
NO_RESULTS_TEXT = "관련 문서를 찾을 수 없습니다."


class _AnswerBubble(QFrame):
    """AI 말풍선 1턴 — 즉시 발췌(1단계) + AI 요약 자리(2단계, 선택).

    `results`는 1단계 검색 결과를 그대로 들고 있다가 "AI 요약 보기"를 누르는
    순간 그대로 재사용된다 — 이게 이번 설계가 검색을 두 번 안 하는 이유다.
    """

    summarize_requested = Signal()
    open_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatAnswerBubble")
        self.results: list[HybridResult] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # --- 1단계: 즉시 발췌 ---
        self._excerpt_label = QLabel()
        self._excerpt_label.setObjectName("ChatExcerptBody")
        self._excerpt_label.setWordWrap(True)
        # PlainText 고정 — 문서 내용에 `<`가 섞이면 RichText에서 글자가
        # 사라진다(AiSummaryBody와 같은 이유, summary_card.py 참고).
        self._excerpt_label.setTextFormat(Qt.TextFormat.PlainText)
        self._excerpt_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._excerpt_label)

        self._source_label = QLabel()
        self._source_label.setObjectName("ChatExcerptSource")
        layout.addWidget(self._source_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        # 기존 카드 버튼과 같은 스타일(#ResultCardCopyButton/#ResultCardOpenButton)을
        # 그대로 재사용한다 — 새 QSS 없이 시각적 일관성을 맞춘다.
        self._summarize_button = QPushButton(SUMMARIZE_BUTTON_LABEL)
        self._summarize_button.setObjectName("ResultCardCopyButton")
        self._summarize_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._summarize_button.setEnabled(False)
        self._summarize_button.clicked.connect(self.summarize_requested.emit)
        button_row.addWidget(self._summarize_button)

        self._open_button = QPushButton(OPEN_BUTTON_LABEL)
        self._open_button.setObjectName("ResultCardOpenButton")
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self.open_requested.emit)
        button_row.addWidget(self._open_button)
        layout.addLayout(button_row)

        # --- 2단계: AI 요약(선택, 클릭 전까지 숨김) ---
        # `SummaryCard`를 그대로 끼워 넣는다 — 상태 분기(정상/기권/근거없음/
        # 실패 + "확인 필요" 배지)를 다시 만들 이유가 없다. Phase 7에서
        # 검증된 그 위젯을 턴마다 하나씩 갖는 구조로 재사용한다.
        self._summary_card = SummaryCard()
        self._summary_card.setVisible(False)
        layout.addWidget(self._summary_card)

    # --- 1단계: 즉시 발췌 --------------------------------------------

    def show_searching(self) -> None:
        self._excerpt_label.setText(SEARCHING_TEXT)
        self._source_label.setText("")

    def show_excerpt(self, results: list[HybridResult]) -> None:
        self.results = results
        if not results:
            self._excerpt_label.setText(NO_RESULTS_TEXT)
            self._source_label.setText("")
            return

        top = results[0]
        self._excerpt_label.setText(top.content)
        self._source_label.setText(f"{top.file_name} · {format_location(top.result)}")
        self._summarize_button.setEnabled(True)
        self._open_button.setEnabled(True)

    def show_search_error(self, message: str) -> None:
        self.results = []
        self._excerpt_label.setText(f"검색 중 오류가 발생했습니다: {message}")
        self._source_label.setText("")

    # --- 2단계: AI 요약(선택) — SummaryCard에 그대로 위임 ------------------

    def show_summary_starting(self) -> None:
        self._summary_card.setVisible(True)
        self._summarize_button.setEnabled(False)
        self._summary_card.show_starting()

    def show_summary_generating(self) -> None:
        self._summary_card.setVisible(True)
        self._summarize_button.setEnabled(False)
        self._summary_card.show_generating()

    def show_summary(self, summary: Summary) -> None:
        self._summary_card.setVisible(True)
        self._summarize_button.setEnabled(True)
        self._summary_card.show_summary(summary)

    def show_summary_error(self, message: str) -> None:
        self._summary_card.setVisible(True)
        self._summarize_button.setEnabled(True)
        self._summary_card.show_error(message)

    # --- 테스트·검증용 --------------------------------------------------

    def excerpt_text(self) -> str:
        return self._excerpt_label.text()

    def summary_text(self) -> str:
        return self._summary_card.body_text()

    def is_summary_visible(self) -> bool:
        return self._summary_card.isVisibleTo(self)

    def is_review_badge_visible(self) -> bool:
        return self._summary_card.is_review_visible()


class ChatPanel(QWidget):
    """검색 결과 영역 전체를 차지하는 채팅 화면.

    `ResultList.show_chat_mode()`가 카드 목록 대신 이 위젯 하나로 레이아웃을
    채운다. objectName은 `ResultCard`를 쓰지 않는다 — 쓰면
    `ResultList.card_count()`("검색 결과 N건")에 잡혀버린다
    (`AiSummaryCard`가 이미 피한 것과 같은 함정).
    """

    message_sent = Signal(int, str)  # (request_id, question)
    summarize_requested = Signal(int, list)  # (request_id, 그 턴의 검색 결과)
    open_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatPanel")
        self._next_id = 0
        self._bubbles: dict[int, _AnswerBubble] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("ChatTranscript")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._transcript = QWidget()
        self._transcript_layout = QVBoxLayout(self._transcript)
        self._transcript_layout.setContentsMargins(12, 12, 12, 12)
        self._transcript_layout.setSpacing(12)
        self._transcript_layout.addStretch()
        self._scroll.setWidget(self._transcript)
        layout.addWidget(self._scroll, stretch=1)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(12, 8, 12, 12)
        input_row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setObjectName("ChatInput")
        self._input.setPlaceholderText(INPUT_PLACEHOLDER)
        self._input.returnPressed.connect(self._send)
        input_row.addWidget(self._input, stretch=1)

        self._send_button = QPushButton(SEND_BUTTON_LABEL)
        self._send_button.setObjectName("ChatSendButton")
        self._send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_button.clicked.connect(self._send)
        input_row.addWidget(self._send_button)
        layout.addLayout(input_row)

    # --- 공개 API -------------------------------------------------------

    def send_message(self, text: str) -> None:
        """외부(MainWindow)에서 메시지를 대신 보낼 때 쓴다 — 챗봇 모드를 켜면
        검색어를 그대로 첫 질문 삼아 자동 전송하는 데 쓰인다."""
        self._input.setText(text)
        self._send()

    def show_excerpt(self, request_id: int, results: list) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_excerpt(results)

    def show_search_error(self, request_id: int, message: str) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_search_error(message)

    def show_summary_starting(self, request_id: int) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary_starting()

    def show_summary_generating(self, request_id: int) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary_generating()

    def show_summary(self, request_id: int, summary: Summary) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary(summary)

    def show_summary_error(self, request_id: int, message: str) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary_error(message)

    def turn_count(self) -> int:
        """테스트·검증용 — 지금까지 오간 턴 수."""
        return len(self._bubbles)

    def bubble_for(self, request_id: int) -> _AnswerBubble | None:
        """테스트·검증용."""
        return self._bubbles.get(request_id)

    # --- 내부 -----------------------------------------------------------

    def _send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()

        self._next_id += 1
        request_id = self._next_id

        user_label = QLabel(text)
        user_label.setObjectName("ChatUserMessage")
        user_label.setWordWrap(True)
        self._transcript_layout.insertWidget(self._transcript_layout.count() - 1, user_label)

        bubble = _AnswerBubble()
        bubble.show_searching()
        bubble.summarize_requested.connect(
            lambda rid=request_id, b=bubble: self.summarize_requested.emit(rid, b.results)
        )
        bubble.open_requested.connect(lambda b=bubble: self._open_top_result(b))
        self._bubbles[request_id] = bubble
        self._transcript_layout.insertWidget(self._transcript_layout.count() - 1, bubble)

        self._scroll_to_bottom()
        self.message_sent.emit(request_id, text)

    def _open_top_result(self, bubble: _AnswerBubble) -> None:
        if not bubble.results:
            return
        error = open_source_file(bubble.results[0].result.file_path)
        if error:
            self.open_failed.emit(error)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

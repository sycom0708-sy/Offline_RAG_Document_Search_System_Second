"""AI 챗봇 패널 (T7.6, Phase 7.7에서 입력창을 MainWindow 공용으로 이관) —
검색 결과 영역을 통째로 차지하는 채팅 화면.

Phase 7의 "AI 요약 보기"(검색마다 자동 1회 요약)를 대체한다. 실측(속도
79초 vs 은행 앱 수준 요구)이 부딪혀 응답을 2단으로 나눴다
[사용자 확정, 2026-08-11]:

    1단계(즉시, LLM 미사용)  hybrid_search()의 상위 결과를 원문 그대로 보여준다
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

Phase 7.7부터 이 패널은 자체 입력창을 갖지 않는다 — 목업(`rag_ui_concept_
chatbot.html`)이 검색 결과 모드와 챗봇 모드에서 입력 지점을 하나로 통일해,
`MainWindow`가 소유한 공용 입력창(`InputBar`)이 `send_message()`를 호출한다.

**2026-08-14**: 즉시 발췌를 top-1 하나에서 상위 5개로 늘렸다(검색 결과
카드 목록과 개수 기준을 맞추는 사용자 요청) — 각 항목은 검색 카드
(`ResultCard`/`TableCard`/`ImageCard`)를 그대로 재사용해 자기 몫의 원문
열기·표 복사·확대를 스스로 처리한다(`card_dispatch.make_result_card`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from search.hybrid_search import HybridResult
from slm.summarize import Summary
from ui.widgets.card_dispatch import make_result_card
from ui.widgets.summary_card import SummaryCard

SUMMARIZE_BUTTON_LABEL = "AI 요약 보기"
SEARCHING_TEXT = "검색하는 중…"
NO_RESULTS_TEXT = "관련 문서를 찾을 수 없습니다."

# 검색 결과 카드 목록과 개수 기준을 맞춘다(ResultList.PAGE_SIZE와 동일,
# 2026-08-14 사용자 요청) — [제안], DESIGN에 결과 개수 상한 명세가 없어
# 직접 정의했다.
EXCERPT_LIMIT = 5

# 말풍선 최대 폭(70%) — 목업 기준값(65%)에서 사용자 확인 후 넓혔다
# [2026-08-13]. Qt QSS는 max-width를 지원하지 않아 위젯의
# setMaximumWidth()로 직접 계산한다(ChatPanel.resizeEvent 참고).
MAX_BUBBLE_WIDTH_RATIO = 0.70

# QSS #ChatUserMessage의 padding(8px 12px)에 여유를 더한 근사값 — 아래
# _natural_single_line_width()에서 쓴다.
_LABEL_HORIZONTAL_PADDING = 30


class _AnswerBubble(QFrame):
    """AI 말풍선 1턴 — 즉시 발췌(1단계) + AI 요약 자리(2단계, 선택).

    `results`는 1단계 검색 결과를 그대로 들고 있다가 "AI 요약 보기"를 누르는
    순간 그대로 재사용된다(top-1 컨텍스트 기준) — 이게 이번 설계가 검색을
    두 번 안 하는 이유다.
    """

    summarize_requested = Signal()
    open_failed = Signal(str)  # 카드들의 open_failed를 한 자리로 모아 ChatPanel.open_failed로 릴레이

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatAnswerBubble")
        self.results: list[HybridResult] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # --- 1단계: 즉시 발췌 — 본문 영역은 상태에 따라 갈아 끼운다 ---
        # (검색중/결과없음/오류 안내 텍스트 하나, 또는 검색 카드 최대
        # EXCERPT_LIMIT개 + "N개 더" 안내). 한 말풍선은 검색중→발췌로 한
        # 번만 전이하므로 "지우고 다시 그리기"면 충분하다
        # (ResultList.show_results()와 같은 패턴).
        self._body_layout = QVBoxLayout()
        self._body_layout.setSpacing(6)
        layout.addLayout(self._body_layout)

        button_row = QHBoxLayout()
        button_row.addStretch()
        # 기존 카드 버튼과 같은 스타일(#ResultCardCopyButton)을 재사용한다 —
        # 새 QSS 없이 시각적 일관성을 맞춘다.
        self._summarize_button = QPushButton(SUMMARIZE_BUTTON_LABEL)
        self._summarize_button.setObjectName("ResultCardCopyButton")
        self._summarize_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._summarize_button.setEnabled(False)
        self._summarize_button.clicked.connect(self.summarize_requested.emit)
        button_row.addWidget(self._summarize_button)
        layout.addLayout(button_row)

        self._render_text_body(SEARCHING_TEXT)

        # --- 2단계: AI 요약(선택, 클릭 전까지 숨김) ---
        # `SummaryCard`를 그대로 끼워 넣는다 — 상태 분기(정상/기권/근거없음/
        # 실패 + "확인 필요" 배지)를 다시 만들 이유가 없다. Phase 7에서
        # 검증된 그 위젯을 턴마다 하나씩 갖는 구조로 재사용한다.
        self._summary_card = SummaryCard()
        self._summary_card.setVisible(False)
        layout.addWidget(self._summary_card)

    # --- 1단계: 즉시 발췌 --------------------------------------------

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._excerpt_label: QLabel | None = None

    def _render_text_body(self, text: str) -> None:
        """검색중/결과없음/오류 안내 전용 — 실제 결과는 `_render_result_cards()`가 그린다."""
        self._clear_body()
        label = QLabel(text)
        label.setObjectName("ChatExcerptBody")
        label.setWordWrap(True)
        # PlainText 고정 — 문서 내용에 `<`가 섞이면 RichText에서 글자가
        # 사라진다(AiSummaryBody와 같은 이유, summary_card.py 참고).
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body_layout.addWidget(label)
        self._excerpt_label = label

    def _render_result_cards(self, results: list[HybridResult]) -> None:
        """검색 카드(`ResultCard`/`TableCard`/`ImageCard`)를 그대로 재사용한다.

        표는 그리드+복사 버튼, 이미지는 썸네일+확대 버튼을 카드가 알아서
        그린다 — T10.12가 챗봇 전용으로 손으로 짰던 렌더링(표/이미지 갈아
        끼우기, 높이 재계산 등)을 전부 대체한다. 하이라이트용 질의어는
        넘기지 않는다(빈 문자열) — 이번 변경의 핵심은 "카드 재사용으로
        개수·버튼을 검색과 맞추는 것"이지 하이라이트가 아니다.
        """
        self._clear_body()
        shown = results[:EXCERPT_LIMIT]
        for result in shown:
            card = make_result_card(result, "", False, False)
            card.open_failed.connect(self.open_failed)
            self._body_layout.addWidget(card)

        remaining = len(results) - len(shown)
        if remaining > 0:
            notice = QLabel(f"{remaining}개 결과가 더 있습니다")
            notice.setObjectName("ChatMoreResultsNotice")
            self._body_layout.addWidget(notice)

    def show_searching(self) -> None:
        self._render_text_body(SEARCHING_TEXT)

    def show_excerpt(self, results: list[HybridResult]) -> None:
        self.results = results
        if not results:
            self._render_text_body(NO_RESULTS_TEXT)
            return

        self._render_result_cards(results)
        self._summarize_button.setEnabled(True)

    def show_search_error(self, message: str) -> None:
        self.results = []
        self._render_text_body(f"검색 중 오류가 발생했습니다: {message}")

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
        """검색중/결과없음/오류 안내 문구용. 결과가 카드로 렌더링된 턴에는
        의미가 없다 — 그럴 땐 `findChild`로 카드 위젯을 직접 조회한다."""
        return self._excerpt_label.text() if self._excerpt_label is not None else ""

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

    입력창을 갖지 않는다 — `MainWindow`가 소유한 공용 `InputBar`가
    `send_message()`를 호출해 메시지를 넣는다(Phase 7.7).
    """

    message_sent = Signal(int, str)  # (request_id, question)
    summarize_requested = Signal(int, list)  # (request_id, 그 턴의 검색 결과)
    open_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatPanel")
        self._next_id = 0
        self._bubbles: dict[int, _AnswerBubble] = {}
        # 좌/우 정렬 행에 들어간 위젯들(사용자 라벨 + AI 말풍선) — 창 크기가
        # 바뀔 때마다 최대 폭(65%)을 다시 계산해야 해서 전부 기억해 둔다.
        self._bubble_widgets: list[QWidget] = []

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

    # --- 공개 API -------------------------------------------------------

    def send_message(self, text: str) -> None:
        """메시지를 보낸다 — 이 패널의 유일한 입력 경로다.

        `MainWindow`의 공용 입력창(`InputBar`)이 챗봇 모드일 때 이 메서드를
        호출한다. 빈 문자열(공백만 포함)은 조용히 무시한다."""
        text = text.strip()
        if not text:
            return
        self._send(text)

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

    def _send(self, text: str) -> None:
        self._next_id += 1
        request_id = self._next_id

        user_label = QLabel(text)
        user_label.setObjectName("ChatUserMessage")
        user_label.setWordWrap(True)
        self._add_row(user_label, align_right=True)

        bubble = _AnswerBubble()
        bubble.show_searching()
        bubble.summarize_requested.connect(
            lambda rid=request_id, b=bubble: self.summarize_requested.emit(rid, b.results)
        )
        bubble.open_failed.connect(self.open_failed)
        self._bubbles[request_id] = bubble
        self._add_row(bubble, align_right=False)

        # 방금 넣은 위젯은 아직 레이아웃이 안 돌아 `bar.maximum()`이 직전
        # 값이다 — 지금 부르면 한 턴 늦게 스크롤된다. 다음 이벤트 루프로
        # 미룬다.
        QTimer.singleShot(0, self._scroll_to_bottom)
        self.message_sent.emit(request_id, text)

    def _add_row(self, widget: QWidget, *, align_right: bool) -> None:
        """`widget`을 좌/우 정렬 행으로 감싸 대화창 맨 끝(stretch 앞)에 넣는다.

        QSS는 margin-left:auto나 max-width를 지원하지 않으므로, 정렬은
        QHBoxLayout의 addStretch() 위치로, 최대 폭은 setMaximumWidth()로
        각각 직접 계산한다. 래퍼를 QWidget으로 만드는 이유는 `insertWidget`
        관행(`_transcript_layout.count() - 1`)을 유지하면서, 레이아웃만
        넣으면 정리(clear) 시 `widget()`이 None이라 놓치는 함정을 피하기
        위해서다.
        """
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        if align_right:
            row_layout.addStretch()
            row_layout.addWidget(widget)
        else:
            row_layout.addWidget(widget)
            row_layout.addStretch()

        self._transcript_layout.insertWidget(self._transcript_layout.count() - 1, row)
        self._bubble_widgets.append(widget)
        self._apply_max_width(widget)

    def _apply_max_width(self, widget: QWidget) -> None:
        # 창에 아직 부착되지 않은 시점(생성 직후)엔 `_transcript.width()`가
        # 0이다(Phase 4·5·7에서 반복된 "부착 전/후" 함정과 같은 종류). 그대로
        # 두면 상한이 안 걸려 말풍선이 첫 프레임에 폭 전체로 그려졌다가 다음
        # 이벤트 루프에 확 줄어드는 게 보인다 — 자신의 폭, 그마저 없으면
        # 고정된 임시값으로 상한을 미리 걸어 두고 resizeEvent가 실제 크기로
        # 바로잡게 한다.
        width = self._transcript.width() or self.width() or 640
        cap = int(width * MAX_BUBBLE_WIDTH_RATIO)

        if isinstance(widget, QLabel):
            # QLabel.wordWrap()의 기본 sizeHint는 텍스트를 정사각형에
            # 가깝게 배치하려 해, 한 줄로 충분히 들어가는 짧은 문장도
            # 불필요하게 두 줄로 접는다(실사용 중 발견) — 한 줄 폭을 직접
            # 계산해 최소 폭으로 줘서, 상한(cap) 안에서는 줄바꿈이 강제되지
            # 않도록 한다. 텍스트가 상한보다 길 때만 실제로 줄바꿈된다.
            #
            # 🔴 `ensurePolished()`가 반드시 먼저 와야 한다 — 이 시점
            # (insertWidget 직후, 아직 화면에 안 보임)엔 QSS의
            # `font-size: 14px`가 아직 위젯에 반영되지 않아 `widget.font()`가
            # 앱 기본 폰트(10pt)를 돌려준다. 그 폰트로 폭을 계산해 두면
            # 나중에 실제 14px 폰트로 그려질 때 계산이 작아서 여전히
            # 줄바꿈된다(실측으로 확인: 201px vs 실제 217px).
            widget.ensurePolished()
            metrics = QFontMetrics(widget.font())
            natural = metrics.horizontalAdvance(widget.text()) + _LABEL_HORIZONTAL_PADDING
            widget.setMinimumWidth(min(natural, cap))

        widget.setMaximumWidth(cap)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        for widget in self._bubble_widgets:
            self._apply_max_width(widget)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

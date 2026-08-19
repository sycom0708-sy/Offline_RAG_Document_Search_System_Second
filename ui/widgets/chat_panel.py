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

**검색(1단계)은 메시지마다 독립 처리(stateless)다** — 매번 검색어 전체
범위로 새로 검색한다. "두 번째는요?" 같은 대명사 참조로 검색어 자체가
빈약해지는 경우까지는 보정하지 않는다.

**생성(2단계, AI 요약)은 T10.17부터 이전 대화를 참고한다** — `history_before()`
가 이전 턴 중 실제로 답이 나온(`SummaryStatus.OK`) 것만 모아 프롬프트에
맥락으로 얹는다(근거로는 안 쓴다, `slm/prompt.py`). 검색을 다시 하지 않는
2단 응답 구조 자체는 그대로다 — LLM 호출 지점(사용자가 "AI 요약 보기"를
누른 순간)에서만 문맥을 추가했다. 이렇게 해야 latency가 생명인 1단계
(7~14ms)를 건드리지 않는다(Phase 7.6이 이미 두 번 반려한 "검색에 LLM을
끼워 넣는" 설계를 반복하지 않기 위해서다).

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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from search.hybrid_search import HybridResult
from slm.prompt import HistoryTurn
from slm.summarize import Summary, SummaryStatus
from ui.widgets.card_dispatch import make_result_card
from ui.widgets.summary_card import SummaryCard

SUMMARIZE_BUTTON_LABEL = "AI 요약 보기"
SEARCHING_TEXT = "검색하는 중…"
NO_RESULTS_TEXT = "관련 문서를 찾을 수 없습니다."

# 검색 결과 카드 목록과 개수 기준을 맞춘다(ResultList.PAGE_SIZE와 동일,
# 2026-08-14 사용자 요청) — [제안], DESIGN에 결과 개수 상한 명세가 없어
# 직접 정의했다.
EXCERPT_LIMIT = 5

# 말풍선 최대 폭(80%) — 목업 기준값 65% → 70%[2026-08-13] → 80%[2026-08-18].
# 답변 카드(표·AI 요약)가 길어지면서 오른쪽 회색 여백이 과하게 남는다는
# 사용자 지적으로 다시 넓혔다. Qt QSS는 max-width를 지원하지 않아 위젯의
# setMaximumWidth()로 직접 계산한다(ChatPanel.resizeEvent 참고).
MAX_BUBBLE_WIDTH_RATIO = 0.80

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
    nearby_requested = Signal(object, str)  # T10.21, open_failed와 같은 방식으로 릴레이

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatAnswerBubble")
        self.results: list[HybridResult] = []
        # T10.17: 이 턴의 질문·완성된 답변 — ChatPanel.history_before()가
        # 다음 턴 프롬프트의 대화 이력을 만들 때 읽어간다.
        self.question: str = ""
        self.summary: Summary | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # --- 1단계: 즉시 발췌 — 본문 영역은 상태에 따라 갈아 끼운다 ---
        # (검색중/결과없음/오류 안내 텍스트 하나, 또는 검색 카드 최대
        # EXCERPT_LIMIT개 + "더보기" 버튼 — ResultList와 같은 컴포넌트/동작).
        # 한 말풍선은 검색중→발췌로 한 번만 전이하므로 "지우고 다시 그리기"면
        # 충분하다(ResultList.show_results()와 같은 패턴).
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
        self._more_button: QPushButton | None = None
        self._remaining_results: list[HybridResult] = []

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
            self._add_result_card(result)

        self._remaining_results = results[len(shown):]
        if self._remaining_results:
            self._add_more_button(len(self._remaining_results))

    def _add_result_card(self, result: HybridResult) -> None:
        card = make_result_card(result, "", False, False)
        card.open_failed.connect(self.open_failed)
        card.nearby_requested.connect(self.nearby_requested)
        self._body_layout.addWidget(card)

    def _add_more_button(self, remaining: int) -> None:
        button = QPushButton(f"더보기 ({remaining}개 더)")
        button.setObjectName("ResultListMoreButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self._show_remaining_results)
        self._more_button = button
        self._body_layout.addWidget(button)

    def _show_remaining_results(self) -> None:
        if self._more_button is not None:
            self._more_button.setParent(None)
            self._more_button.deleteLater()
            self._more_button = None
        for result in self._remaining_results:
            self._add_result_card(result)
        self._remaining_results = []

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
        self.summary = summary
        self._summary_card.setVisible(True)
        self._summarize_button.setEnabled(True)
        self._summary_card.show_summary(summary)

    def show_summary_error(self, message: str) -> None:
        self._summary_card.setVisible(True)
        self._summarize_button.setEnabled(True)
        self._summary_card.show_error(message)

    def show_summary_cancelled(self) -> None:
        """다음 질문이 들어와 이 턴의 답변 생성을 접었다 (T10.23).

        실패가 아니므로 오류 카드로 보여주지 않는다 — 요약 자리를 도로 숨기고
        버튼을 다시 열어, 원하면 이 턴만 따로 다시 생성할 수 있게 둔다.
        ①(즉시 발췌)은 이미 화면에 있고 그대로 남는다.
        """
        self._summary_card.setVisible(False)
        self._summarize_button.setEnabled(bool(self.results))

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
    nearby_requested = Signal(object, str)  # T10.21, open_failed와 같은 방식으로 릴레이

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatPanel")
        self._next_id = 0
        self._bubbles: dict[int, _AnswerBubble] = {}
        # 좌/우 정렬 행에 들어간 위젯들(사용자 라벨 + AI 말풍선) — 창 크기가
        # 바뀔 때마다 최대 폭을 다시 계산해야 해서 전부 기억해 둔다.
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
            self._scroll_to_bottom_deferred()

    def show_search_error(self, request_id: int, message: str) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_search_error(message)
            self._scroll_to_bottom_deferred()

    def show_summary_starting(self, request_id: int) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary_starting()
            self._scroll_to_bottom_deferred()

    def show_summary_generating(self, request_id: int) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary_generating()
            self._scroll_to_bottom_deferred()

    def show_summary(self, request_id: int, summary: Summary) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary(summary)
            self._scroll_to_bottom_deferred()

    def show_summary_error(self, request_id: int, message: str) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary_error(message)
            self._scroll_to_bottom_deferred()

    def show_summary_cancelled(self, request_id: int) -> None:
        bubble = self._bubbles.get(request_id)
        if bubble is not None:
            bubble.show_summary_cancelled()

    def turn_count(self) -> int:
        """테스트·검증용 — 지금까지 오간 턴 수."""
        return len(self._bubbles)

    def bubble_for(self, request_id: int) -> _AnswerBubble | None:
        """테스트·검증용."""
        return self._bubbles.get(request_id)

    def history_before(self, request_id: int) -> list[HistoryTurn]:
        """`request_id`보다 앞선 턴들의 (질문, 답변)을 대화 이력으로 반환한다
        (T10.17). 성공적으로 답이 나온(`SummaryStatus.OK`) 턴만 담는다 —
        기권·근거없음·실패 턴은 다음 답변에 참고할 내용이 없다. `_bubbles`는
        `request_id`가 메시지 순서 그대로 증가하므로 정렬만 하면 된다."""
        turns = []
        for rid in sorted(self._bubbles):
            if rid >= request_id:
                break
            bubble = self._bubbles[rid]
            if bubble.summary is not None and bubble.summary.status is SummaryStatus.OK:
                turns.append(HistoryTurn(question=bubble.question, answer=bubble.summary.text))
        return turns

    # --- 내부 -----------------------------------------------------------

    def _send(self, text: str) -> None:
        self._next_id += 1
        request_id = self._next_id

        user_label = QLabel(text)
        user_label.setObjectName("ChatUserMessage")
        user_label.setWordWrap(True)
        self._add_row(user_label, align_right=True)

        bubble = _AnswerBubble()
        bubble.question = text
        bubble.show_searching()
        bubble.summarize_requested.connect(
            lambda rid=request_id, b=bubble: self.summarize_requested.emit(rid, b.results)
        )
        bubble.open_failed.connect(self.open_failed)
        bubble.nearby_requested.connect(self.nearby_requested)
        self._bubbles[request_id] = bubble
        self._add_row(bubble, align_right=False, expand=True)

        self._scroll_to_bottom_deferred()
        self.message_sent.emit(request_id, text)

    def _add_row(self, widget: QWidget, *, align_right: bool, expand: bool = False) -> None:
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
        elif expand:
            # 답변 말풍선은 **상한까지 실제로 넓힌다** (2026-08-18, 사용자 요청).
            #
            # `setMaximumWidth()`만으로는 안 넓어진다 — 상한은 "이 이상 크지
            # 말라"일 뿐이고, 위젯은 자기 `sizeHint`만큼만 차지한 뒤 남는
            # 공간은 전부 `addStretch()`가 먹는다(실측: 회색 영역 877px에
            # 표 카드가 475px만 씀). 늘어나게 하려면 늘일 권한을 줘야 한다.
            #
            # stretch 인자를 위젯 1 / 여백 0으로 주는 게 핵심이다 — 둘 다
            # 1이면 남는 공간을 반씩 나눠 가져 상한(80%)에 못 미친 채 멈춘다.
            # 위젯이 먼저 상한까지 자라고, 그러고도 남는 20%를 여백이 받는다.
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, widget.sizePolicy().verticalPolicy())
            row_layout.addWidget(widget, 1)
            row_layout.addStretch(0)
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

    def _scroll_to_bottom_deferred(self) -> None:
        """지금 막 늘어난(또는 늘어날) 위젯 높이를 반영해 맨 아래로 스크롤한다.

        `_send()`뿐 아니라 검색 결과 도착(`show_excerpt`)·AI 요약 진행
        단계 전환(`show_summary_starting`/`_generating`/실제 답변/오류)
        에서도 불러야 한다(T10.19, 사용자 보고) — 이 호출들이 전부
        말풍선 높이를 바꾸는데, 그때마다 다시 스크롤하지 않으면 그 사이에
        늘어난 높이만큼 화면이 이전 위치에 멈춰 있는 것처럼 보인다("AI 요약
        보기를 누르면 이전 메시지 위치로 올라가 버림", "다음 검색을 하면
        직전 검색 끝부분에 멈춰 방금 보낸 질문이 안 보임").

        🔴 `QTimer.singleShot(0, ...)`만으로는 부족하다 — 방금 넣거나 갈아
        끼운 위젯은 이 시점에 레이아웃이 아직 안 돌아 `bar.maximum()`이
        갱신 전 값(예: 0)이다. 0ms 뒤 타이머가 실행되는 시점에도 레이아웃이
        아직 안 끝나 있는 경우가 실측으로 확인됐다(10번 연속
        `processEvents()`를 돌려도 `maximum()`만 갱신되고 `value()`는 그대로
        멈춰 있었다) — 그래서 "몇 턴 뒤"가 아니라 **범위가 실제로 바뀌는
        순간**(`rangeChanged`)에 맞춰 스크롤한다.

        🔴 레이아웃이 **한 번에 안 끝나고 여러 번에 걸쳐 다시 계산되는
        경우도 실측으로 확인됐다**(예: 첫 재계산에서 348→418, 곧이어
        418→482로 한 번 더) — `rangeChanged`를 한 번만 받고 바로 연결을
        끊으면 그 사이의 값(예: 418)에서 멈춘다. 그래서 매번 값을
        맞춰준 뒤 짧은 유휴 타이머를 다시 시작하고, **그 타이머가 방해
        없이 끝까지 도달했을 때만** 연결을 끊는다 — 레이아웃이 잠잠해질
        때까지 계속 따라간다.
        """
        bar = self._scroll.verticalScrollBar()

        idle_timer = QTimer(self)
        idle_timer.setSingleShot(True)

        def _on_range_changed(_minimum: int, maximum: int) -> None:
            bar.setValue(maximum)
            idle_timer.start(120)

        def _detach() -> None:
            try:
                bar.rangeChanged.disconnect(_on_range_changed)
            except RuntimeError:
                pass  # 패널이 이미 파괴됐다 — 끊을 대상이 없다

        idle_timer.timeout.connect(_detach)
        bar.rangeChanged.connect(_on_range_changed)
        idle_timer.start(120)
        # 레이아웃이 이미 끝나 range가 안 바뀌는 경우(예: 내용이 안 자라
        # 스크롤이 애초에 없는 상태)에는 rangeChanged가 안 와서 위 리스너가
        # 영영 안 불릴 수 있다 — 지금 값 기준으로 한 번 더 시도해 대비한다.
        # 🔴 컨텍스트 객체(self)를 같이 넘긴다 — 이 오버로드는 self가 파괴되면
        # 타이머를 자동으로 취소한다. 안 넘기면 패널이 사라진 뒤에도 발사돼
        # 이미 삭제된 QScrollArea를 건드린다(실측: 자동 요약으로 스크롤
        # 재조정이 잦아지자 테스트에서 재현됐다).
        QTimer.singleShot(0, self, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        try:
            bar = self._scroll.verticalScrollBar()
        except RuntimeError:
            return  # 지연 스크롤이 패널 파괴 뒤에 도착한 경우
        bar.setValue(bar.maximum())

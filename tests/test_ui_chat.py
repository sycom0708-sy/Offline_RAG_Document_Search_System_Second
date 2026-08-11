"""AI 챗봇 패널 테스트 (Phase 7.6, 옛 `test_ui_summary.py`를 이름·대상 모두 옮김).

Phase 4·5의 교훈대로 "신호가 emit되는가"가 아니라 **화면에 도달하는가**를
본다 — T10.3에서 `open_failed`가 emit만 되고 받는 곳이 없어 아무 반응도
없던 버그가 카드 단위 테스트를 전부 통과한 채 남아 있었다.

`SummaryCard`는 챗봇 말풍선(`_AnswerBubble`) 안에 그대로 재사용되므로
`TestSummaryCard`는 옛 테스트를 그대로 옮겼다 — 검증 대상이 안 바뀌었다.
`ResultList`의 옛 `summary_card()`/`clear_summary()`/`has_summary()` API는
Phase 7.6에서 챗봇 패널이 자동 요약 카드를 완전히 대체하며 함께 제거됐다 —
`show_chat_mode()`로 교체됐다.
"""

from __future__ import annotations

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType
from search.hybrid_search import HybridResult
from slm.summarize import Summary, SummaryStatus
from slm.verify import VerificationResult
from ui.widgets.chat_panel import ChatPanel
from ui.widgets.result_list import ResultList
from ui.widgets.summary_card import (
    ABSTAINED_HINT,
    GENERATING_MESSAGE,
    STARTING_MESSAGE,
    SummaryCard,
)


def _hybrid(content: str = "내용") -> HybridResult:
    result = SearchResult(
        chunk_id="c1", doc_id="d1", file_path="x", file_name="사규.docx",
        type=ChunkType.TEXT, page_or_slide=3, content=content, caption="", score=-1.0,
    )
    return HybridResult(result, 0.8, False, matched_terms=1, total_terms=1)


class TestSummaryCard:
    def test_starting_state(self, qtbot):
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_starting()
        assert card.body_text() == STARTING_MESSAGE
        assert card.property("state") == "pending"

    def test_generating_state(self, qtbot):
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_generating()
        assert card.body_text() == GENERATING_MESSAGE

    def test_ok_summary_shows_text_without_badge(self, qtbot):
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_summary(Summary(
            status=SummaryStatus.OK,
            text="DNS는 트리 구조입니다. [사규.docx, 3페이지]",
            verification=VerificationResult(needs_review=False),
        ))
        assert "트리 구조" in card.body_text()
        assert card.is_review_visible() is False
        assert card.property("state") == "ok"

    def test_needs_review_shows_badge_and_reason(self, qtbot):
        """4단계가 걸렸을 때 답을 **숨기지 않고** 배지만 붙인다."""
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_summary(Summary(
            status=SummaryStatus.OK,
            text="정답은 IaaS입니다.",
            verification=VerificationResult(
                needs_review=True, weak_sentences=["정답은 IaaS입니다."]
            ),
        ))
        assert "IaaS" in card.body_text()  # 답은 그대로 보인다
        assert card.is_review_visible() is True
        assert card.is_hint_visible() is True

    def test_abstained_shows_next_step(self, qtbot):
        """기권은 실패가 아니다 — 다음 행동을 안내해야 고장으로 안 읽힌다."""
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_summary(Summary(
            status=SummaryStatus.ABSTAINED, text="문서에서 찾을 수 없습니다."
        ))
        assert card._hint.text() == ABSTAINED_HINT
        assert card.property("state") == "empty"

    def test_no_evidence_state(self, qtbot):
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_summary(Summary(
            status=SummaryStatus.NO_EVIDENCE, text="관련 문서를 찾을 수 없습니다"
        ))
        assert card.property("state") == "empty"
        assert card.is_review_visible() is False

    def test_failed_shows_reason(self, qtbot):
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_summary(Summary(
            status=SummaryStatus.FAILED, error="모델이 없습니다."
        ))
        assert card.body_text() == "모델이 없습니다."
        assert card.property("state") == "failed"

    def test_badge_clears_between_summaries(self, qtbot):
        """이전 요약의 "확인 필요"가 남으면 멀쩡한 답에 경고가 붙는다."""
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_summary(Summary(
            status=SummaryStatus.OK, text="a",
            verification=VerificationResult(needs_review=True, weak_sentences=["a"]),
        ))
        card.show_summary(Summary(
            status=SummaryStatus.OK, text="b",
            verification=VerificationResult(needs_review=False),
        ))
        assert card.is_review_visible() is False

    def test_body_is_plain_text(self, qtbot):
        """문서 내용에 `<`가 섞여도 태그로 먹히면 안 된다."""
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_summary(Summary(status=SummaryStatus.OK, text="a < b 이면 <b>참</b>"))
        assert card.body_text() == "a < b 이면 <b>참</b>"


class TestResultListChatMode:
    """`ResultList.show_chat_mode()` — 옛 `summary_card()` 계열 API를 대체."""

    def test_show_chat_mode_inserts_the_panel(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        panel = ChatPanel()

        widget.show_chat_mode(panel)

        assert widget._layout.indexOf(panel) == 0

    def test_show_chat_mode_replaces_previous_cards(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid(), _hybrid()], "질의")
        assert widget.card_count() == 2

        widget.show_chat_mode(ChatPanel())

        assert widget.card_count() == 0

    def test_panel_does_not_count_as_a_result_card(self, qtbot):
        """"검색 결과 N건"이 챗봇 패널 때문에 늘어나면 안 된다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_chat_mode(ChatPanel())
        assert widget.card_count() == 0

    def test_show_results_after_chat_mode_removes_the_panel(self, qtbot):
        """토글 OFF → `show_results()` 복귀 시 챗봇 패널이 걷혀야 한다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        panel = ChatPanel()
        widget.show_chat_mode(panel)

        widget.show_results([_hybrid()], "질의")

        assert widget.card_count() == 1
        assert widget._layout.indexOf(panel) == -1


class TestChatPanel:
    """`ChatPanel`/`_AnswerBubble` 단독 테스트 — 워커는 MainWindow가 기동하므로
    여기서는 신호(`message_sent`/`summarize_requested`)와 렌더링 위임만 본다."""

    def test_send_message_creates_a_turn(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)

        panel.send_message("계약서 검토 기준")

        assert panel.turn_count() == 1
        assert panel.bubble_for(1) is not None

    def test_send_message_emits_message_sent_with_request_id_and_text(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        received = []
        panel.message_sent.connect(lambda rid, text: received.append((rid, text)))

        panel.send_message("계약서 검토 기준")

        assert received == [(1, "계약서 검토 기준")]

    def test_show_excerpt_renders_top_result_and_enables_buttons(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)
        assert bubble._summarize_button.isEnabled() is False  # 발췌 도착 전
        assert bubble._open_button.isEnabled() is False

        panel.show_excerpt(1, [_hybrid("계약서 검토 시 기준 조항")])

        assert "계약서 검토 시 기준 조항" in bubble.excerpt_text()
        assert "사규.docx" in bubble._source_label.text()
        assert bubble._summarize_button.isEnabled() is True
        assert bubble._open_button.isEnabled() is True

    def test_show_excerpt_with_no_results_shows_guidance_text(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("전혀관련없는외계어")
        bubble = panel.bubble_for(1)

        panel.show_excerpt(1, [])

        assert "찾을 수 없습니다" in bubble.excerpt_text()
        assert bubble._summarize_button.isEnabled() is False

    def test_summarize_requested_carries_the_turns_results(self, qtbot):
        """② AI 요약이 ①의 결과를 그대로 재사용한다 — 검색을 다시 하지 않는다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)
        results = [_hybrid("계약서 검토 시 기준 조항")]
        panel.show_excerpt(1, results)

        received = []
        panel.summarize_requested.connect(lambda rid, r: received.append((rid, r)))
        bubble._summarize_button.click()

        assert received == [(1, results)]

    def test_show_summary_delegates_to_embedded_summary_card(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)
        panel.show_excerpt(1, [_hybrid()])

        panel.show_summary_generating(1)
        assert bubble.is_summary_visible() is True

        panel.show_summary(1, Summary(status=SummaryStatus.OK, text="답변입니다."))
        assert "답변입니다" in bubble.summary_text()

    def test_show_summary_error_reaches_the_bubble(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)
        panel.show_excerpt(1, [_hybrid()])

        panel.show_summary_error(1, "서버 기동 실패")

        assert "서버 기동 실패" in bubble.summary_text()

    def test_open_button_click_with_missing_file_emits_open_failed(self, qtbot):
        """존재하지 않는 경로라 열기는 실패하지만, 신호로 안전하게 처리돼야 한다
        (T10.3 — emit만 되고 받는 곳이 없던 것과 같은 함정을 챗봇에서도 막는다)."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)
        panel.show_excerpt(1, [_hybrid()])  # file_path="x" — 실제로 존재하지 않는다

        failures = []
        panel.open_failed.connect(failures.append)
        bubble._open_button.click()

        assert len(failures) == 1
        assert "찾을 수 없습니다" in failures[0]

    def test_multiple_turns_keep_independent_bubbles(self, qtbot):
        """메시지마다 독립 처리(stateless) — 이전 턴 결과가 다음 턴에 안 새어든다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        panel.send_message("리눅스")

        panel.show_excerpt(1, [_hybrid("계약서 내용")])
        panel.show_excerpt(2, [_hybrid("리눅스 내용")])

        assert panel.turn_count() == 2
        assert "계약서" in panel.bubble_for(1).excerpt_text()
        assert "리눅스" in panel.bubble_for(2).excerpt_text()

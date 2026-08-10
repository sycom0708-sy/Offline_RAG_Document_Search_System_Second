"""AI 요약 카드·리스트 연동 테스트 (T7.5).

Phase 4·5의 교훈대로 "신호가 emit되는가"가 아니라 **화면에 도달하는가**를
본다 — T10.3에서 `open_failed`가 emit만 되고 받는 곳이 없어 아무 반응도
없던 버그가 카드 단위 테스트를 전부 통과한 채 남아 있었다.
"""

from __future__ import annotations

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType
from search.hybrid_search import HybridResult
from slm.prompt import Excerpt
from slm.summarize import Summary, SummaryStatus
from slm.verify import VerificationResult
from ui.widgets.result_list import ResultList
from ui.widgets.summary_card import (
    ABSTAINED_HINT,
    GENERATING_MESSAGE,
    STARTING_MESSAGE,
    SummaryCard,
)


def _hybrid() -> HybridResult:
    result = SearchResult(
        chunk_id="c1", doc_id="d1", file_path="x", file_name="사규.docx",
        type=ChunkType.TEXT, page_or_slide=3, content="내용", caption="", score=-1.0,
    )
    return HybridResult(result, 0.8, False)


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


class TestResultListSummaryIntegration:
    def test_summary_card_is_created_on_demand(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        assert widget.has_summary() is False
        widget.summary_card()
        assert widget.has_summary() is True

    def test_summary_card_is_reused(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        assert widget.summary_card() is widget.summary_card()

    def test_summary_does_not_count_as_a_result_card(self, qtbot):
        """요약 때문에 "검색 결과 N건"이 1 늘어나면 안 된다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid(), _hybrid()], "질의")
        widget.summary_card()
        assert widget.card_count() == 2

    def test_summary_sits_above_result_cards(self, qtbot):
        """요약과 근거를 한 화면에서 위아래로 대조할 수 있어야 한다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid()], "질의")
        card = widget.summary_card()
        assert widget._layout.indexOf(card) == 0

    def test_clear_summary_keeps_result_cards(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid(), _hybrid()], "질의")
        widget.summary_card()
        widget.clear_summary()

        assert widget.has_summary() is False
        assert widget.card_count() == 2

    def test_clear_summary_is_safe_without_one(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.clear_summary()  # 예외가 나지 않아야 한다
        assert widget.has_summary() is False

    def test_new_search_drops_the_stale_summary_reference(self, qtbot):
        """`_clear()`가 요약 카드를 지웠는데 참조가 남으면 삭제된 위젯을 만진다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid()], "질의")
        widget.summary_card()

        widget.show_results([_hybrid()], "다른 질의")
        assert widget.has_summary() is False

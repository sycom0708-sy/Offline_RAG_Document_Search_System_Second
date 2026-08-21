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

import json
from dataclasses import asdict

from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTableWidget

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType, ImageData, TableData
from search.hybrid_search import HybridResult
from slm.summarize import Summary, SummaryStatus
from slm.verify import VerificationResult
from ui.widgets.chat_panel import MAX_BUBBLE_WIDTH_RATIO, ChatPanel
from ui.widgets.result_list import ResultList
from ui.widgets.table_card import TableCard
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


def _table_hybrid(table: TableData | None) -> HybridResult:
    result = SearchResult(
        chunk_id="t1", doc_id="d1", file_path="x", file_name="체크리스트.xlsx",
        type=ChunkType.TABLE, page_or_slide=None, content="", caption="", score=-1.0,
        table_json=json.dumps(asdict(table)) if table else None,
    )
    return HybridResult(result, 0.8, False, matched_terms=1, total_terms=1)


def _image_hybrid(image: ImageData | None) -> HybridResult:
    result = SearchResult(
        chunk_id="i1", doc_id="d1", file_path="x", file_name="흐름도.pptx",
        type=ChunkType.IMAGE, page_or_slide=5, content="", caption="", score=-1.0,
        image_json=json.dumps(asdict(image)) if image else None,
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

    # --- 진행 중 깜박임 (T10.22) -----------------------------------------

    def test_pulses_while_preparing_the_model(self, qtbot):
        """수십 초 걸리는 작업이라 "지금 동작 중"이 눈에 보여야 한다."""
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_starting()
        assert card.is_pulsing() is True

    def test_pulses_while_generating(self, qtbot):
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_generating()
        assert card.is_pulsing() is True

    def test_stops_pulsing_once_the_summary_arrives(self, qtbot):
        """결과가 나온 뒤에도 깜박이면 아직 진행 중인 것처럼 읽힌다."""
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_generating()

        card.show_summary(Summary(status=SummaryStatus.OK, text="답변입니다."))

        assert card.is_pulsing() is False
        # 멈춘 자리의 불투명도가 남아 결과 문구가 흐리게 굳으면 안 된다.
        assert card._pulse_effect.opacity() == 1.0

    def test_stops_pulsing_on_error(self, qtbot):
        card = SummaryCard()
        qtbot.addWidget(card)
        card.show_generating()

        card.show_error("서버 기동 실패")

        assert card.is_pulsing() is False

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

    def test_show_results_after_chat_mode_does_not_destroy_the_panel(self, qtbot):
        """T10.16: `MainWindow`가 패널 인스턴스를 재사용하려면 `_clear()`가
        떼어내기만 하고 파괴하면 안 된다 — 파괴됐다면 `deleteLater()` 예약
        이후 이 패널을 다시 붙이려는 시도가 죽은 위젯을 건드리게 된다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        panel = ChatPanel()
        widget.show_chat_mode(panel)

        widget.show_results([_hybrid()], "질의")

        widget.show_chat_mode(panel)  # 파괴됐다면 여기서 RuntimeError가 난다
        assert widget._layout.indexOf(panel) == 0

    def test_panel_gets_stretch_to_fill_the_area_down_to_the_input_bar(self, qtbot):
        """stretch=1 없이 넣으면 패널이 sizeHint 높이만 차지하고, `__init__`의
        트레일링 `addStretch()`가 나머지 여백을 먹어 대화 영역이 화면 중간에서
        끊겨 보인다(실사용 중 실제로 발견된 버그) — 인덱스 0의 stretch factor로
        확인한다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        panel = ChatPanel()

        widget.show_chat_mode(panel)

        assert widget._layout.stretch(0) == 1


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

    def test_show_excerpt_renders_top_result_as_a_search_card(self, qtbot):
        """2026-08-14: 즉시 발췌는 이제 검색 카드(ResultCard)를 그대로 재사용한다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)
        assert bubble._summarize_button.isEnabled() is False  # 발췌 도착 전

        panel.show_excerpt(1, [_hybrid("계약서 검토 시 기준 조항")])

        body = bubble.findChild(QLabel, "ResultCardBody")
        assert body is not None
        # 2026-08-21부터 챗봇 카드도 검색어를 강조해 원문이 <span>으로
        # 감싸인다(아래 test_show_excerpt_highlights_the_matched_query_term
        # 참고) — 여기서는 발췌 텍스트가 그대로 담겨 있는지만 본다.
        assert "검토 시 기준 조항" in body.text()
        assert "계약서" in body.text()
        name_label = bubble.findChild(QLabel, "ResultCardFileName")
        assert name_label is not None
        assert name_label.text() == "사규.docx"
        assert bubble._summarize_button.isEnabled() is True

    def test_show_excerpt_highlights_the_matched_query_term(self, qtbot):
        """2026-08-21, 사용자 요청: 챗봇 카드도 검색 카드처럼 검색어를
        강조해야 한다 — 이전에는 하이라이트용 질의어를 빈 문자열로 넘겨
        일부러 꺼져 있었다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")

        panel.show_excerpt(1, [_hybrid("계약서 검토 시 기준 조항")])

        body = panel.bubble_for(1).findChild(QLabel, "ResultCardBody")
        assert "background-color:#FEEEAD" in body.text()
        assert "계약서" in body.text()

    def test_send_message_passes_case_sensitive_and_exact_word_to_cards(self, qtbot):
        """대/소문자 구분·일치되는 단어 옵션이 검색 화면과 어긋나면 안 된다
        (DESIGN §5.3) — 챗봇도 같은 옵션으로 하이라이트해야 한다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("Case", case_sensitive=True, exact_word=True)

        panel.show_excerpt(1, [_hybrid("여기 Case 있고 case도 있고 CASE도 있다")])

        body = panel.bubble_for(1).findChild(QLabel, "ResultCardBody")
        # case_sensitive=True라 대소문자가 다른 "case"/"CASE"는 강조되지 않고
        # 정확히 일치하는 "Case"만 강조된다.
        assert body.text().count("background-color:#FEEEAD") == 1

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

    # --- 대화 이력 (T10.17) ---------------------------------------------

    def test_history_before_is_empty_for_the_first_turn(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")

        assert panel.history_before(1) == []

    def test_history_before_includes_earlier_completed_turns(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("첫 질문")
        panel.show_excerpt(1, [_hybrid()])
        panel.show_summary(1, Summary(status=SummaryStatus.OK, text="첫 답변 [1]"))

        panel.send_message("둘째 질문")

        history = panel.history_before(2)
        assert len(history) == 1
        assert history[0].question == "첫 질문"
        assert history[0].answer == "첫 답변 [1]"

    def test_history_before_excludes_turns_without_a_completed_answer(self, qtbot):
        """AI 요약을 아직 안 눌렀거나(대기 중), 기권/근거없음/실패로 끝난
        턴은 다음 프롬프트에 실을 만한 내용이 없다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)

        panel.send_message("발췌만 있는 질문")
        panel.show_excerpt(1, [_hybrid()])  # 요약 버튼을 안 눌렀다

        panel.send_message("기권한 질문")
        panel.show_excerpt(2, [_hybrid()])
        panel.show_summary(2, Summary(status=SummaryStatus.ABSTAINED, text="문서에서 찾을 수 없습니다."))

        panel.send_message("셋째 질문")

        assert panel.history_before(3) == []

    def test_history_before_excludes_turns_at_or_after_the_given_id(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("첫 질문")
        panel.show_excerpt(1, [_hybrid()])
        panel.show_summary(1, Summary(status=SummaryStatus.OK, text="첫 답변"))

        # 자기 자신(1번 턴)을 기준으로 물으면 자기 자신은 포함되면 안 된다.
        assert panel.history_before(1) == []

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
        panel.show_excerpt(1, [_hybrid()])  # file_path="x" — 실제로 존재하지 않는다

        failures = []
        panel.open_failed.connect(failures.append)
        open_button = panel.bubble_for(1).findChild(QPushButton, "ResultCardOpenButton")
        open_button.click()

        assert len(failures) == 1
        assert "찾을 수 없습니다" in failures[0]

    def test_open_button_click_with_existing_file_spawns_worker(self, qtbot, tmp_path, monkeypatch):
        """T10.1: 파일이 있으면 비동기 워커로 열어야 한다(카드와 같은 패턴)."""
        import ui.widgets.card_common as card_common
        from tests.conftest import FakeOpenFileWorker

        FakeOpenFileWorker.instances = []
        monkeypatch.setattr(card_common, "OpenFileWorker", FakeOpenFileWorker)

        real_file = tmp_path / "실제사규.docx"
        real_file.write_text("dummy", encoding="utf-8")
        result = SearchResult(
            chunk_id="c1", doc_id="d1", file_path=str(real_file), file_name="실제사규.docx",
            type=ChunkType.TEXT, page_or_slide=3, content="계약서 검토 기준", caption="", score=-1.0,
        )
        hybrid = HybridResult(result, 0.8, False, matched_terms=1, total_terms=1)

        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        panel.show_excerpt(1, [hybrid])
        open_button = panel.bubble_for(1).findChild(QPushButton, "ResultCardOpenButton")

        open_button.click()

        assert len(FakeOpenFileWorker.instances) == 1
        worker = FakeOpenFileWorker.instances[0]
        assert worker.file_path == str(real_file)
        assert worker.started is True
        assert open_button.isEnabled() is False

    def test_multiple_turns_keep_independent_bubbles(self, qtbot):
        """메시지마다 독립 처리(stateless) — 이전 턴 결과가 다음 턴에 안 새어든다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        panel.send_message("리눅스")

        panel.show_excerpt(1, [_hybrid("계약서 내용")])
        panel.show_excerpt(2, [_hybrid("리눅스 내용")])

        assert panel.turn_count() == 2
        body1 = panel.bubble_for(1).findChild(QLabel, "ResultCardBody")
        body2 = panel.bubble_for(2).findChild(QLabel, "ResultCardBody")
        assert "계약서" in body1.text()
        assert "리눅스" in body2.text()


class TestChatRetentionLimit:
    """2026-08-21, 사용자 요청 — 실측(1000턴 시뮬레이션, 턴당 약 1.1MB)으로
    무제한 누적이 GB 단위까지 갈 수 있음을 확인해, 오래된 턴을 화면·메모리
    에서 지우는 슬라이딩 윈도우를 뒀다."""

    def test_default_retention_is_100_turns(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)

        for i in range(1, 106):
            panel.send_message(f"질문 {i}")

        assert panel.turn_count() == 100
        assert panel.bubble_for(1) is None  # 가장 오래된 5턴이 지워졌다
        assert panel.bubble_for(5) is None
        assert panel.bubble_for(6) is not None  # 그 다음부터는 남아 있다
        assert panel.bubble_for(105) is not None

    def test_set_max_retained_turns_evicts_immediately_when_lowered(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        for i in range(1, 11):
            panel.send_message(f"질문 {i}")
        assert panel.turn_count() == 10

        panel.set_max_retained_turns(3)

        assert panel.turn_count() == 3
        assert panel.bubble_for(7) is None
        assert panel.bubble_for(8) is not None
        assert panel.bubble_for(10) is not None

    def test_evicted_turn_widgets_are_removed_from_transcript_layout(self, qtbot):
        """위젯이 딕셔너리에서만 빠지는 게 아니라 화면(레이아웃)에서도
        실제로 없어져야 메모리가 돌아온다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.set_max_retained_turns(2)

        panel.send_message("첫 질문")
        panel.send_message("둘째 질문")
        panel.send_message("셋째 질문")  # 첫 질문 턴이 지워진다

        # addStretch()까지 포함해 (사용자 행 + 답변 행) × 2턴 + 1 이어야 한다.
        assert panel._transcript_layout.count() == 5
        assert panel.turn_count() == 2

    def test_settings_page_shows_memory_estimate_for_each_choice(self, qtbot):
        from ui.widgets.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)

        page.chat_retention_combo.setCurrentIndex(0)  # 100턴(기본)
        assert "100턴" in page._chat_retention_description.text()
        assert "110MB" in page._chat_retention_description.text()

        page.chat_retention_combo.setCurrentIndex(2)  # 500턴
        assert "500턴" in page._chat_retention_description.text()
        assert "550MB" in page._chat_retention_description.text()

    def test_settings_page_default_is_100_turns(self, qtbot):
        from ui.widgets.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)
        page.set_chat_retain_turns(100)

        assert page.current_chat_retain_turns() == 100
        assert "110MB" in page._chat_retention_description.text()

    def test_changing_settings_combo_emits_signal_without_double_firing_on_restore(self, qtbot):
        from ui.widgets.settings_page import SettingsPage

        page = SettingsPage()
        qtbot.addWidget(page)
        received = []
        page.chat_retain_turns_changed.connect(received.append)

        page.set_chat_retain_turns(500)  # 복원 — 신호가 나가면 안 된다
        assert received == []

        page.chat_retention_combo.setCurrentIndex(0)  # 사용자가 실제로 바꿈
        assert received == [100]


class TestChatScrollBehavior:
    """T10.19(2026-08-15, 사용자 보고): 검색 결과 도착·AI 요약 진행 단계
    전환이 말풍선 높이를 바꾸는데, 그때마다 다시 스크롤하지 않으면 그
    사이에 늘어난 높이만큼 화면이 이전 위치에 멈춰 있는 것처럼 보인다.
    작은 뷰포트로 실제 스크롤 가능한 상태를 만들어 확인한다."""

    @staticmethod
    def _shrink_viewport(panel, qtbot):
        panel.show()
        panel.resize(400, 150)  # 카드 몇 개만 넣어도 스크롤이 생기는 작은 높이
        qtbot.wait(50)

    @staticmethod
    def _is_scrolled_to_bottom(panel) -> bool:
        bar = panel._scroll.verticalScrollBar()
        return bar.maximum() > 0 and bar.value() == bar.maximum()

    def _wait_for_overflow(self, panel, qtbot) -> None:
        """스크롤이 실제로 생길 만큼 레이아웃이 자리 잡을 때까지 기다린다
        (Phase 4·5·7의 "화면 부착 전/후" 함정과 같은 종류 — 리사이즈 직후엔
        `verticalScrollBar().maximum()`이 아직 0이다)."""
        bar = panel._scroll.verticalScrollBar()
        qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=2000)

    def test_show_excerpt_scrolls_to_bottom(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        self._shrink_viewport(panel, qtbot)

        panel.send_message("계약서")
        panel.show_excerpt(1, [_hybrid() for _ in range(5)])  # 카드 여러 개 → 말풍선이 커진다
        self._wait_for_overflow(panel, qtbot)

        # 검색 중 상태에서 스크롤을 위로 올려 "사용자가 다른 곳을 보고 있던" 상태를 흉내낸다.
        panel._scroll.verticalScrollBar().setValue(0)
        panel.show_excerpt(1, [_hybrid() for _ in range(6)])  # 카드가 하나 더 늘어 다시 커진다
        qtbot.waitUntil(lambda: self._is_scrolled_to_bottom(panel), timeout=2000)

    def test_summary_generating_scrolls_to_bottom(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        self._shrink_viewport(panel, qtbot)

        panel.send_message("계약서")
        panel.show_excerpt(1, [_hybrid() for _ in range(5)])
        self._wait_for_overflow(panel, qtbot)
        panel._scroll.verticalScrollBar().setValue(0)

        panel.show_summary_generating(1)
        qtbot.waitUntil(lambda: self._is_scrolled_to_bottom(panel), timeout=2000)

    def test_completed_summary_scrolls_to_bottom(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        self._shrink_viewport(panel, qtbot)

        panel.send_message("계약서")
        panel.show_excerpt(1, [_hybrid() for _ in range(5)])
        self._wait_for_overflow(panel, qtbot)
        panel._scroll.verticalScrollBar().setValue(0)

        panel.show_summary(1, Summary(status=SummaryStatus.OK, text="답변 " * 40))
        qtbot.waitUntil(lambda: self._is_scrolled_to_bottom(panel), timeout=2000)

    def test_second_message_scrolls_to_bottom_past_first_turn(self, qtbot):
        """"다음 검색을 하면 직전 검색 끝부분에 멈춰 방금 보낸 질문이 안
        보임" 보고 — 두 번째 메시지를 보내면 그 메시지까지 스크롤돼야 한다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        self._shrink_viewport(panel, qtbot)

        panel.send_message("계약서")
        panel.show_excerpt(1, [_hybrid() for _ in range(5)])
        self._wait_for_overflow(panel, qtbot)
        panel._scroll.verticalScrollBar().setValue(0)

        panel.send_message("두 번째 질문")
        qtbot.waitUntil(lambda: self._is_scrolled_to_bottom(panel), timeout=2000)


class TestChatExcerptTableAndImage:
    """2026-08-14, 사용자 요청: 챗봇 즉시 발췌도 검색 화면(TableCard/ImageCard)과
    같은 수준으로 표·이미지를 렌더링해야 한다 — Phase 7.6 완료 시점엔 원시
    텍스트로만 나왔었다. 같은 날 후속 요청으로, 이제 검색 카드를 그대로
    재사용해 렌더링한다(표는 자기 복사 버튼, 이미지는 자기 확대 버튼을
    스스로 갖는다)."""

    def test_table_top1_renders_as_grid_with_copy_button(self, qtbot):
        table = TableData(rows=[["손해배상", "10%"]], header_row=["항목", "비율"], caption="Sheet1")
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("체크리스트")
        bubble = panel.bubble_for(1)

        panel.show_excerpt(1, [_table_hybrid(table)])

        grid = bubble.findChild(QTableWidget, "TableCardGrid")
        assert grid is not None
        assert grid.rowCount() == 1
        assert grid.item(0, 0).text() == "손해배상"
        # objectName("ResultCardCopyButton")은 말풍선의 "AI 요약 보기" 버튼과
        # 공유하는 스타일이라, 카드 안으로 범위를 좁혀 찾는다.
        card = bubble.findChild(TableCard)
        assert card is not None
        assert card.findChild(QPushButton, "ResultCardCopyButton") is not None

    def test_table_copy_button_copies_tsv_to_clipboard(self, qtbot):
        from PySide6.QtGui import QGuiApplication

        table = TableData(rows=[["a", "b"]], header_row=["h1", "h2"])
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("체크리스트")
        panel.show_excerpt(1, [_table_hybrid(table)])
        bubble = panel.bubble_for(1)

        card = bubble.findChild(TableCard)
        copy_button = card.findChild(QPushButton, "ResultCardCopyButton")
        copy_button.click()

        assert QGuiApplication.clipboard().text() == "h1\th2\na\tb"

    def test_missing_table_data_falls_back_to_placeholder(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("체크리스트")
        bubble = panel.bubble_for(1)

        panel.show_excerpt(1, [_table_hybrid(None)])

        assert bubble.findChild(QTableWidget, "TableCardGrid") is None

    def test_image_top1_renders_thumbnail_and_zoom_button(self, qtbot, tmp_path, monkeypatch):
        from PySide6.QtGui import QImage
        from ui import thumbnail_cache

        monkeypatch.setattr(thumbnail_cache, "THUMBNAIL_DIR", tmp_path / "thumbs")
        source = tmp_path / "source.png"
        QImage(40, 40, QImage.Format.Format_RGB32).save(str(source))

        image = ImageData(image_path=str(source), origin="extracted")
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("흐름도")
        bubble = panel.bubble_for(1)

        panel.show_excerpt(1, [_image_hybrid(image)])

        thumb = bubble.findChild(QLabel, "ImageCardThumbnail")
        assert thumb is not None
        assert thumb.pixmap() is not None and not thumb.pixmap().isNull()

    def test_multiple_results_render_as_stacked_cards_with_more_button(self, qtbot):
        """2026-08-14 후속 요청: 상위 5개까지 카드로, 넘으면 검색과 같은 "더보기" 버튼."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)

        results = [_hybrid(f"내용{i}") for i in range(7)]
        panel.show_excerpt(1, results)

        bodies = bubble.findChildren(QLabel, "ResultCardBody")
        assert len(bodies) == 5
        more_button = bubble.findChild(QPushButton, "ResultListMoreButton")
        assert more_button is not None
        assert "2개" in more_button.text()

    def test_more_button_reveals_remaining_results(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)
        panel.show_excerpt(1, [_hybrid(f"내용{i}") for i in range(7)])
        more_button = bubble.findChild(QPushButton, "ResultListMoreButton")

        more_button.click()

        assert len(bubble.findChildren(QLabel, "ResultCardBody")) == 7
        assert bubble.findChild(QPushButton, "ResultListMoreButton") is None

    def test_five_or_fewer_results_show_no_more_button(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)

        panel.show_excerpt(1, [_hybrid(f"내용{i}") for i in range(3)])

        assert bubble.findChild(QPushButton, "ResultListMoreButton") is None

    def test_each_card_has_its_own_open_button_relaying_open_failed(self, qtbot):
        """2~5순위 결과도 각자 원문 열기 버튼을 갖는다(사용자 확정)."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")
        bubble = panel.bubble_for(1)

        results = [_hybrid(f"내용{i}") for i in range(3)]  # 전부 file_path="x" — 실제로 없음
        panel.show_excerpt(1, results)

        open_buttons = bubble.findChildren(QPushButton, "ResultCardOpenButton")
        assert len(open_buttons) == 3

        failures = []
        panel.open_failed.connect(failures.append)
        open_buttons[1].click()  # 두 번째 카드

        assert len(failures) == 1
        assert "찾을 수 없습니다" in failures[0]

    def test_switching_from_table_to_text_turn_removes_grid(self, qtbot):
        """턴마다 독립이라 이전 턴의 표 그리드가 다음 턴에 안 남아야 한다."""
        table = TableData(rows=[["a", "b"]], header_row=["h1", "h2"])
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("체크리스트")
        panel.send_message("계약서")

        panel.show_excerpt(1, [_table_hybrid(table)])
        panel.show_excerpt(2, [_hybrid("계약서 내용")])

        assert panel.bubble_for(1).findChild(QTableWidget, "TableCardGrid") is not None
        assert panel.bubble_for(2).findChild(QTableWidget, "TableCardGrid") is None
        body2 = panel.bubble_for(2).findChild(QLabel, "ResultCardBody")
        # "계약서"가 검색어라 하이라이트 <span>으로 감싸인다(2026-08-21).
        assert "내용" in body2.text()
        assert "계약서" in body2.text()

    def test_searching_state_always_shows_text_even_after_table_turn(self, qtbot):
        """검색 중 상태는 항상 텍스트 본문이어야 한다(표 그리드가 남아있으면 안 됨)."""
        table = TableData(rows=[["a", "b"]], header_row=["h1", "h2"])
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("체크리스트")
        bubble = panel.bubble_for(1)
        panel.show_excerpt(1, [_table_hybrid(table)])
        assert bubble.findChild(QTableWidget, "TableCardGrid") is not None

        bubble.show_searching()

        assert bubble.findChild(QTableWidget, "TableCardGrid") is None
        assert bubble.excerpt_text() == "검색하는 중…"


class TestBubbleAlignment:
    """말풍선 좌/우 정렬 + 최대 폭 70% (Phase 7.7, 목업 기준값 65%에서 사용자 확인 후 조정).

    Qt QSS는 margin-left:auto·max-width를 지원하지 않아 정렬은
    QHBoxLayout의 addStretch() 위치로, 최대 폭은 setMaximumWidth()로 직접
    계산한다 — 둘 다 objectName만으로는 검증할 수 없는 기하 속성이라
    레이아웃 아이템을 직접 들여다본다.
    """

    @staticmethod
    def _last_row(panel):
        """`_transcript_layout`의 마지막 stretch 바로 앞 항목(가장 최근 행)."""
        layout = panel._transcript_layout
        item = layout.itemAt(layout.count() - 2)
        return item.widget()

    def test_user_message_row_is_right_aligned(self, qtbot):
        """사용자 메시지 행은 stretch가 먼저(왼쪽) 와야 위젯이 오른쪽에 붙는다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")

        rows = [panel._transcript_layout.itemAt(i).widget() for i in range(panel._transcript_layout.count() - 1)]
        user_row = rows[0]  # 사용자 메시지가 AI 말풍선보다 먼저 들어간다
        row_layout = user_row.layout()

        assert row_layout.itemAt(0).spacerItem() is not None  # 왼쪽 stretch
        assert row_layout.itemAt(1).widget().objectName() == "ChatUserMessage"

    def test_answer_bubble_row_is_left_aligned(self, qtbot):
        """AI 말풍선 행은 위젯이 먼저(왼쪽) 오고 stretch가 뒤(오른쪽)여야 한다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.send_message("계약서")

        bubble_row = self._last_row(panel)
        row_layout = bubble_row.layout()

        assert row_layout.itemAt(0).widget().objectName() == "ChatAnswerBubble"
        assert row_layout.itemAt(1).spacerItem() is not None  # 오른쪽 stretch

    def test_bubbles_get_a_max_width_even_before_being_shown(self, qtbot):
        """창에 부착되기 전에도 임시 상한이 걸려야 한다 — 안 그러면 첫 프레임에
        말풍선이 폭 전체로 그려졌다가 다음 이벤트 루프에 확 줄어드는 게 보인다
        (Phase 4·5·7에서 반복된 "부착 전/후" 함정과 같은 종류)."""
        panel = ChatPanel()  # qtbot.addWidget()도, show()도 하지 않은 상태
        panel.send_message("계약서")

        bubble = panel.bubble_for(1)
        assert 0 < bubble.maximumWidth() < 16777215  # Qt 기본 무제한 값보다 작아야 한다

    def test_resize_updates_bubble_max_width_to_the_configured_ratio(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.show()
        panel.resize(1000, 600)
        qtbot.wait(50)  # 레이아웃이 실제로 다시 계산될 시간을 준다
        panel.send_message("계약서")
        qtbot.wait(50)

        bubble = panel.bubble_for(1)
        # 비율을 바꿀 때 이 테스트도 같이 고쳐야 하는 상태였다(0.70 하드코딩)
        # — 상수를 그대로 쓰면 값 변경이 테스트를 깨뜨리지 않는다.
        expected = int(panel._transcript.width() * MAX_BUBBLE_WIDTH_RATIO)
        # eliding·레이아웃 반올림 오차를 감안해 근사 비교한다.
        assert abs(bubble.maximumWidth() - expected) <= 2

    def test_short_user_message_does_not_wrap_despite_qss_font_override(self, qtbot):
        """실사용 중 발견 — `#ChatUserMessage`의 QSS `font-size` 오버라이드가
        위젯 생성 직후(첫 폴리시 전)엔 아직 `font()`에 반영되지 않는다. 그
        폰트로 한 줄 폭을 계산해 두면, 실제로 그려질 때 쓰는(더 큰) 폰트
        기준으로는 폭이 모자라 한 줄로 충분한 문장도 두 줄로 잘렸다
        (`ensurePolished()` 없이 재현됨, 수정 후 통과 확인)."""
        app = QApplication.instance()
        original_style = app.styleSheet()
        # 기본보다 눈에 띄게 큰 폰트로 QSS 재폴리시 필요성을 과장해 재현한다.
        app.setStyleSheet("#ChatUserMessage { font-size: 22px; }")
        try:
            panel = ChatPanel()
            qtbot.addWidget(panel)
            panel.resize(1000, 600)

            text = "코치 인증 자격시험 응시 방법 찾아줘"
            panel.send_message(text)

            user_label = panel._transcript.findChild(QLabel, "ChatUserMessage")
            user_label.ensurePolished()
            metrics = QFontMetrics(user_label.font())
            expected_min = metrics.horizontalAdvance(text) + 30
            # 폴리시 전 폰트로 계산됐다면 이보다 훨씬 작게 잡혔을 것이다.
            assert user_label.minimumWidth() >= expected_min - 2
        finally:
            app.setStyleSheet(original_style)


class TestAnswerBubbleFillsAvailableWidth:
    """답변 말풍선은 상한(80%)까지 **실제로 넓어져야** 한다 (2026-08-18, 사용자 요청).

    🔴 `setMaximumWidth()`만으로는 안 넓어진다 — 상한은 "이 이상 크지 말라"일
    뿐이라 위젯은 자기 `sizeHint`만큼만 차지하고 남는 공간은 `addStretch()`가
    다 먹는다(실측: 회색 영역 877px에 표 카드가 475px만 썼다). 비율 상수만
    올리고 끝냈다면 화면은 하나도 안 바뀌었을 것이다.
    """

    def test_answer_bubble_grows_to_the_cap(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.show()
        panel.resize(1000, 600)
        qtbot.wait(50)
        panel.send_message("계약서")
        qtbot.wait(50)

        bubble = panel.bubble_for(1)
        cap = int(panel._transcript.width() * MAX_BUBBLE_WIDTH_RATIO)

        assert abs(bubble.width() - cap) <= 2, (
            f"상한 {cap}px인데 실제 폭이 {bubble.width()}px — 늘어나지 않았다"
        )

    def test_user_message_still_hugs_its_text(self, qtbot):
        """질문 말풍선까지 넓어지면 안 된다 — 짧은 질문이 폭 전체를 차지한다."""
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.show()
        panel.resize(1000, 600)
        qtbot.wait(50)
        panel.send_message("계약서")
        qtbot.wait(50)

        cap = int(panel._transcript.width() * MAX_BUBBLE_WIDTH_RATIO)
        user_label = panel._bubble_widgets[0]

        assert user_label.width() < cap

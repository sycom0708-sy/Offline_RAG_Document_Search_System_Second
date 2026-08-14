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
from ui.widgets.chat_panel import ChatPanel
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
        assert "계약서 검토 시 기준 조항" in body.text()
        name_label = bubble.findChild(QLabel, "ResultCardFileName")
        assert name_label is not None
        assert name_label.text() == "사규.docx"
        assert bubble._summarize_button.isEnabled() is True

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
        assert "계약서 내용" in body2.text()

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

    def test_resize_updates_bubble_max_width_to_70_percent(self, qtbot):
        panel = ChatPanel()
        qtbot.addWidget(panel)
        panel.show()
        panel.resize(1000, 600)
        qtbot.wait(50)  # 레이아웃이 실제로 다시 계산될 시간을 준다
        panel.send_message("계약서")
        qtbot.wait(50)

        bubble = panel.bubble_for(1)
        expected = int(panel._transcript.width() * 0.70)
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

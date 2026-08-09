"""결과 카드·결과 리스트 테스트 (T4.12~T4.15)."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QLabel, QPushButton

from parser.schema import ChunkType, TableData
from indexer.fts5.search import SearchResult
from search.hybrid_search import HybridResult
from ui.widgets.card_common import format_location
from ui.widgets.result_card import ResultCard
from ui.widgets.result_list import ResultList


def _search_result(
    file_name: str = "2024_영업계약서_표준.docx",
    page_or_slide: int | None = 3,
    content: str = "계약서 검토 시 기준이 되는 조항은 손해배상, 계약 해지 조건이다.",
    file_path: str = r"D:\문서\2024_영업계약서_표준.docx",
) -> SearchResult:
    return SearchResult(
        chunk_id="c1",
        doc_id="d1",
        file_path=file_path,
        file_name=file_name,
        type=ChunkType.TEXT,
        page_or_slide=page_or_slide,
        content=content,
        caption="",
        score=-1.5,
    )


def _hybrid(result: SearchResult | None = None, similarity: float | None = 0.7, low: bool = False) -> HybridResult:
    return HybridResult(result or _search_result(), similarity, low)


class TestFormatLocation:
    def test_docx_shows_page_number(self):
        assert format_location(_search_result(file_name="x.docx", page_or_slide=3)) == "3페이지"

    def test_pdf_shows_page_number(self):
        assert format_location(_search_result(file_name="x.pdf", page_or_slide=12)) == "12페이지"

    def test_pptx_shows_slide_number(self):
        assert format_location(_search_result(file_name="x.pptx", page_or_slide=5)) == "5번 슬라이드"

    def test_missing_location_shows_dash(self):
        assert format_location(_search_result(file_name="x.txt", page_or_slide=None)) == "-"

    def test_xlsx_table_shows_sheet_name_not_page_number(self):
        """DESIGN §5.2: xlsx는 page_or_slide(시트 인덱스)가 아니라
        TableData.caption(시트 이름)을 써야 한다."""
        table = TableData(rows=[["1", "2"]], caption="Sheet2")
        result = SearchResult(
            chunk_id="c1",
            doc_id="d1",
            file_path="x.xlsx",
            file_name="x.xlsx",
            type=ChunkType.TABLE,
            page_or_slide=1,  # 시트 인덱스 — 이 값이 노출되면 버그
            content="",
            caption="",
            score=-1.0,
            table_json=json.dumps(table.__dict__),
        )
        assert format_location(result) == "Sheet2"

    def test_table_without_caption_falls_back_to_page_number(self):
        table = TableData(rows=[["1", "2"]], caption="")
        result = SearchResult(
            chunk_id="c1",
            doc_id="d1",
            file_path="x.xlsx",
            file_name="x.xlsx",
            type=ChunkType.TABLE,
            page_or_slide=2,
            content="",
            caption="",
            score=-1.0,
            table_json=json.dumps(table.__dict__),
        )
        assert format_location(result) == "2페이지"


class TestResultCard:
    def test_shows_file_name_and_location(self, qtbot):
        card = ResultCard(_hybrid(), "계약서")
        qtbot.addWidget(card)
        name_label = card.findChild(QLabel, "ResultCardFileName")
        location_label = card.findChild(QLabel, "ResultCardLocation")
        assert name_label.text() == "2024_영업계약서_표준.docx"
        assert location_label.text() == "3페이지"

    def test_body_contains_highlighted_query_term(self, qtbot):
        card = ResultCard(_hybrid(), "계약서")
        qtbot.addWidget(card)
        body = card.findChild(QLabel, "ResultCardBody")
        assert "background-color:#FDE68A" in body.text()

    def test_open_button_always_present(self, qtbot):
        """DESIGN §8 확정 3·4: 원문 열기는 카드 유형·관련성과 무관하게 항상 있어야 한다."""
        normal_card = ResultCard(_hybrid(low=False), "계약서")
        low_card = ResultCard(_hybrid(low=True), "계약서")
        qtbot.addWidget(normal_card)
        qtbot.addWidget(low_card)

        assert normal_card.findChild(QPushButton, "ResultCardOpenButton") is not None
        assert low_card.findChild(QPushButton, "ResultCardOpenButton") is not None

    def test_low_relevance_shows_label_and_dims_card(self, qtbot):
        card = ResultCard(_hybrid(low=True), "계약서")
        qtbot.addWidget(card)

        label = card.findChild(QLabel, "ResultCardRelevanceLabel")
        assert label is not None
        assert label.text() == "관련성 낮음"
        assert card.graphicsEffect() is not None
        assert card.graphicsEffect().opacity() == 0.5

    def test_normal_relevance_has_no_label_or_dimming(self, qtbot):
        card = ResultCard(_hybrid(low=False), "계약서")
        qtbot.addWidget(card)
        assert card.findChild(QLabel, "ResultCardRelevanceLabel") is None
        assert card.graphicsEffect() is None

    def test_opening_missing_file_emits_open_failed(self, qtbot):
        result = _search_result(file_path=r"D:\없는\경로\파일.docx")
        card = ResultCard(_hybrid(result), "계약서")
        qtbot.addWidget(card)

        failures = []
        card.open_failed.connect(failures.append)
        card._open_source()

        assert len(failures) == 1
        assert "파일을 찾을 수 없습니다" in failures[0]

    def test_case_sensitive_and_exact_word_passed_through_to_highlighting(self, qtbot):
        """DESIGN §5.3: 사이드바 옵션이 하이라이트에도 동일하게 적용돼야 한다."""
        result = _search_result(content="API 문서를 확인하세요")
        card_case_sensitive = ResultCard(_hybrid(result), "api", case_sensitive=True)
        qtbot.addWidget(card_case_sensitive)
        body = card_case_sensitive.findChild(QLabel, "ResultCardBody")
        assert "background-color" not in body.text()  # "api" != "API" 대소문자 구분 시


class TestResultList:
    def test_initial_state_shows_hint_message(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        message = widget.findChild(QLabel, "ResultListMessage")
        assert message is not None
        assert widget.card_count() == 0

    def test_show_results_renders_one_card_per_result(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid(), _hybrid()], "계약서")
        assert widget.card_count() == 2

    def test_show_results_clears_previous_message(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid()], "계약서")
        assert widget.findChild(QLabel, "ResultListMessage") is None

    def test_relays_open_failed_from_child_card(self, qtbot):
        """카드가 emit한 open_failed를 ResultList가 바깥으로 전달해야 한다.

        `MainWindow`가 카드 하나하나에 개별 연결할 필요 없이 `ResultList`
        하나만 구독하면 되게 하는 릴레이다. 이게 없어서 지금까지 "원문 열기"
        실패가 화면 어디에도 안 나타났다.
        """
        widget = ResultList()
        qtbot.addWidget(widget)
        result = _search_result(file_path=r"D:\없는\경로\파일.docx")
        widget.show_results([_hybrid(result)], "계약서")

        failures = []
        widget.open_failed.connect(failures.append)

        card = widget.findChild(ResultCard)
        card._open_source()

        assert len(failures) == 1
        assert "파일을 찾을 수 없습니다" in failures[0]

    def test_show_empty_includes_hint_when_given(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_empty(hint="docx만 검색 중입니다. 전체로 넓혀보세요.")
        message = widget.findChild(QLabel, "ResultListMessage")
        assert "전체로 넓혀보세요" in message.text()

    def test_switching_states_clears_old_cards(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_results([_hybrid(), _hybrid()], "계약서")
        assert widget.card_count() == 2

        widget.show_searching()
        assert widget.card_count() == 0

    def test_show_error_includes_message(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)
        widget.show_error("DB 연결 실패")
        message = widget.findChild(QLabel, "ResultListMessage")
        assert "DB 연결 실패" in message.text()

    def test_repeated_show_results_does_not_leak_widgets(self, qtbot):
        """이전 카드가 deleteLater로만 예약되고 레이아웃엔 남지 않아야 한다."""
        widget = ResultList()
        qtbot.addWidget(widget)
        for _ in range(5):
            widget.show_results([_hybrid()], "계약서")
        assert widget.card_count() == 1

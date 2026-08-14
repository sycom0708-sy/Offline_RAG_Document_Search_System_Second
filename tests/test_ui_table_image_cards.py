"""표 카드·이미지 카드·타입 라우팅 테스트 (T5.1~T5.6)."""

from __future__ import annotations

import json
from dataclasses import asdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QLabel, QPushButton, QTableWidget

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType, ImageData, TableData
from search.hybrid_search import HybridResult
from ui import thumbnail_cache
from ui.widgets.card_common import parse_image_data, parse_table_data
from ui.widgets.image_card import (
    IMAGE_TEXT_NOT_RECOGNIZED_NOTICE,
    NO_PREVIEW_TEXT,
    ImageCard,
)
from ui.widgets.result_card import ResultCard
from ui.widgets.result_list import ResultList
from ui.widgets.table_card import TableCard


def _table_result(
    table: TableData | None,
    file_name: str = "체크리스트.xlsx",
    file_path: str = r"D:\문서\체크리스트.xlsx",
) -> SearchResult:
    return SearchResult(
        chunk_id="t1",
        doc_id="d1",
        file_path=file_path,
        file_name=file_name,
        type=ChunkType.TABLE,
        page_or_slide=1,
        content=table.to_text() if table else "",
        caption="",
        score=-1.0,
        table_json=json.dumps(asdict(table)) if table else None,
    )


def _image_result(
    image: ImageData | None,
    file_name: str = "흐름도.pptx",
    file_path: str = r"D:\문서\흐름도.pptx",
) -> SearchResult:
    return SearchResult(
        chunk_id="i1",
        doc_id="d1",
        file_path=file_path,
        file_name=file_name,
        type=ChunkType.IMAGE,
        page_or_slide=5,
        content="",
        caption="",
        score=-1.0,
        image_json=json.dumps(asdict(image)) if image else None,
    )


def _hybrid(
    result: SearchResult,
    similarity: float | None = 0.7,
    low: bool = False,
    matched_terms: int = 0,
    total_terms: int = 0,
) -> HybridResult:
    return HybridResult(result, similarity, low, matched_terms, total_terms)


class TestParseHelpers:
    def test_parse_table_data_roundtrip(self):
        table = TableData(rows=[["a", "b"]], header_row=["h1", "h2"], caption="Sheet1")
        result = _table_result(table)
        parsed = parse_table_data(result)
        assert parsed == table

    def test_parse_table_data_none_when_missing(self):
        result = _table_result(None)
        assert parse_table_data(result) is None

    def test_parse_image_data_roundtrip(self):
        image = ImageData(image_path="x.png", caption="c", width=10, height=20)
        result = _image_result(image)
        parsed = parse_image_data(result)
        assert parsed == image

    def test_parse_image_data_none_when_missing(self):
        result = _image_result(None)
        assert parse_image_data(result) is None


class TestTableCard:
    def test_renders_header_and_rows(self, qtbot):
        table = TableData(rows=[["손해배상", "계약금액의 10% 이내"]], header_row=["항목", "기준"], caption="Sheet2")
        card = TableCard(_hybrid(_table_result(table)))
        qtbot.addWidget(card)

        grid = card.findChild(QTableWidget, "TableCardGrid")
        assert grid is not None
        assert grid.rowCount() == 1
        assert grid.columnCount() == 2
        assert grid.horizontalHeaderItem(0).text() == "항목"
        assert grid.item(0, 0).text() == "손해배상"

        location = card.findChild(QLabel, "ResultCardLocation")
        assert location.text() == "Sheet2"  # xlsx는 시트명

    def test_handles_missing_header_row(self, qtbot):
        """T5.2: TableData.from_rows()는 1행짜리 표에서 header_row를 비워 둔다."""
        table = TableData(rows=[["단일행", "값"]], header_row=[])
        card = TableCard(_hybrid(_table_result(table)))
        qtbot.addWidget(card)

        grid = card.findChild(QTableWidget, "TableCardGrid")
        assert grid.rowCount() == 1
        assert grid.horizontalHeader().isVisible() is False
        assert grid.item(0, 0).text() == "단일행"

    def test_copy_button_copies_tsv_to_clipboard(self, qtbot):
        table = TableData(rows=[["a", "b"], ["c", "d"]], header_row=["h1", "h2"])
        card = TableCard(_hybrid(_table_result(table)))
        qtbot.addWidget(card)

        copy_button = card.findChild(QPushButton, "ResultCardCopyButton")
        qtbot.mouseClick(copy_button, Qt.MouseButton.LeftButton)

        assert QGuiApplication.clipboard().text() == "h1\th2\na\tb\nc\td"

    def test_missing_table_data_shows_fallback_message(self, qtbot):
        card = TableCard(_hybrid(_table_result(None)))
        qtbot.addWidget(card)
        assert card.findChild(QTableWidget, "TableCardGrid") is None
        copy_button = card.findChild(QPushButton, "ResultCardCopyButton")
        assert copy_button.isEnabled() is False

    def test_open_button_present_and_reports_missing_file(self, qtbot):
        table = TableData(rows=[["a"]], header_row=[])
        card = TableCard(_hybrid(_table_result(table, file_path=r"D:\없는\파일.xlsx")))
        qtbot.addWidget(card)

        assert card.findChild(QPushButton, "ResultCardOpenButton") is not None
        failures = []
        card.open_failed.connect(failures.append)
        card._open_source()
        assert "파일을 찾을 수 없습니다" in failures[0]

    def test_opening_existing_file_spawns_worker_with_sheet_plan(self, qtbot, tmp_path, monkeypatch):
        """T10.1: xlsx 표 카드는 시트명·가장 긴 셀 값을 딥링크 계획으로 넘겨야 한다."""
        import ui.widgets.card_common as card_common
        from tests.conftest import FakeOpenFileWorker

        FakeOpenFileWorker.instances = []
        monkeypatch.setattr(card_common, "OpenFileWorker", FakeOpenFileWorker)

        real_file = tmp_path / "실제표.xlsx"
        real_file.write_text("dummy", encoding="utf-8")
        table = TableData(rows=[["짧음", "가장 긴 셀 값"]], header_row=["h1", "h2"], caption="Sheet3")
        card = TableCard(_hybrid(_table_result(table, file_path=str(real_file))))
        qtbot.addWidget(card)

        card._open_source()

        worker = FakeOpenFileWorker.instances[0]
        assert worker.started is True
        assert worker.plan.sheet_name == "Sheet3"
        assert worker.plan.needles == ["가장 긴 셀 값"]
        assert card.findChild(QPushButton, "ResultCardOpenButton").isEnabled() is False

    def test_low_relevance_shows_label_and_dims_card(self, qtbot):
        """🔴 원래 라벨만 붙고 흐림은 안 됐다 — `apply_low_relevance_style()` 공유 전.

        `ResultCard`(텍스트 카드)에만 있던 흐림 효과가 표 카드에는 빠져 있어서,
        "관련성 낮음" 라벨은 보이는데 카드가 흐려지지는 않았다(실사용에서 발견,
        2026-08-11). `card_common.build_card_header()`가 라벨은 세 카드 공통으로
        붙여주지만 흐림 효과는 별도라 놓치기 쉬웠다.
        """
        table = TableData(rows=[["a", "b"]], header_row=["h1", "h2"])
        card = TableCard(_hybrid(_table_result(table), low=True))
        qtbot.addWidget(card)

        label = card.findChild(QLabel, "ResultCardRelevanceLabel")
        assert label is not None and label.text() == "관련성 낮음"
        assert card.graphicsEffect() is not None
        assert card.graphicsEffect().opacity() == 0.5

    def test_normal_relevance_has_no_dimming(self, qtbot):
        table = TableData(rows=[["a", "b"]], header_row=["h1", "h2"])
        card = TableCard(_hybrid(_table_result(table), low=False))
        qtbot.addWidget(card)

        assert card.findChild(QLabel, "ResultCardRelevanceLabel") is None
        assert card.graphicsEffect() is None

    def test_filename_only_match_shows_badge(self, qtbot):
        """T10.6: build_card_header() 공유 함수라도 카드별 검증이 없으면
        다음에 또 빠뜨릴 수 있다(T10.10과 같은 이유)."""
        table = TableData(rows=[["a", "b"]], header_row=["h1", "h2"])
        card = TableCard(_hybrid(_table_result(table), matched_terms=0, total_terms=1))
        qtbot.addWidget(card)

        label = card.findChild(QLabel, "ResultCardFileNameMatchLabel")
        assert label is not None and label.text() == "파일명 매치"


class TestImageCard:
    def test_valid_image_shows_thumbnail_and_notice(self, qtbot, tmp_path, monkeypatch):
        monkeypatch.setattr(thumbnail_cache, "THUMBNAIL_DIR", tmp_path / "thumbs")

        source = tmp_path / "source.png"
        QImage(40, 40, QImage.Format.Format_RGB32).save(str(source))

        image = ImageData(image_path=str(source), origin="extracted")
        card = ImageCard(_hybrid(_image_result(image)))
        qtbot.addWidget(card)

        thumb = card.findChild(QLabel, "ImageCardThumbnail")
        assert thumb.pixmap() is not None and not thumb.pixmap().isNull()

        notice = card.findChild(QLabel, "ImageCardNotice")
        assert notice.text() == IMAGE_TEXT_NOT_RECOGNIZED_NOTICE

    def test_missing_source_shows_no_preview_fallback(self, qtbot, tmp_path, monkeypatch):
        monkeypatch.setattr(thumbnail_cache, "THUMBNAIL_DIR", tmp_path / "thumbs")

        image = ImageData(image_path=str(tmp_path / "없음.png"), origin="extracted")
        card = ImageCard(_hybrid(_image_result(image)))
        qtbot.addWidget(card)

        thumb = card.findChild(QLabel, "ImageCardThumbnail")
        assert thumb.text() == NO_PREVIEW_TEXT
        assert card._zoom_button.isEnabled() is False

    def test_missing_image_json_does_not_crash(self, qtbot):
        card = ImageCard(_hybrid(_image_result(None)))
        qtbot.addWidget(card)
        thumb = card.findChild(QLabel, "ImageCardThumbnail")
        assert thumb.text() == NO_PREVIEW_TEXT

    def test_open_button_present_and_reports_missing_file(self, qtbot):
        image = ImageData(image_path="아무경로.png")
        card = ImageCard(_hybrid(_image_result(image, file_path=r"D:\없는\파일.pptx")))
        qtbot.addWidget(card)

        assert card.findChild(QPushButton, "ResultCardOpenButton") is not None
        failures = []
        card.open_failed.connect(failures.append)
        card._open_source()
        assert "파일을 찾을 수 없습니다" in failures[0]

    def test_opening_existing_file_spawns_worker_with_slide_plan(self, qtbot, tmp_path, monkeypatch):
        """T10.1: pptx는 이미지 청크도 정확한 slide_index로 딥링크 계획을 만든다."""
        import ui.widgets.card_common as card_common
        from tests.conftest import FakeOpenFileWorker

        FakeOpenFileWorker.instances = []
        monkeypatch.setattr(card_common, "OpenFileWorker", FakeOpenFileWorker)

        real_file = tmp_path / "실제흐름도.pptx"
        real_file.write_text("dummy", encoding="utf-8")
        image = ImageData(image_path="아무경로.png")
        card = ImageCard(_hybrid(_image_result(image, file_path=str(real_file))))
        qtbot.addWidget(card)

        card._open_source()

        worker = FakeOpenFileWorker.instances[0]
        assert worker.started is True
        assert worker.plan.page_or_slide == 5
        assert card.findChild(QPushButton, "ResultCardOpenButton").isEnabled() is False

    def test_zoom_on_missing_file_emits_open_failed_without_opening_dialog(self, qtbot):
        image = ImageData(image_path=r"D:\없는\이미지.png")
        card = ImageCard(_hybrid(_image_result(image)))
        qtbot.addWidget(card)

        failures = []
        card.open_failed.connect(failures.append)
        card._zoom()  # 다이얼로그 대신 방어 분기(파일 없음)만 실행됨
        assert "이미지를 찾을 수 없습니다" in failures[0]

    def test_low_relevance_shows_label_and_dims_card(self, qtbot):
        """🔴 표 카드와 같은 이유로 이미지 카드도 라벨만 있고 흐림이 빠져 있었다."""
        image = ImageData(image_path="아무경로.png")
        card = ImageCard(_hybrid(_image_result(image), low=True))
        qtbot.addWidget(card)

        label = card.findChild(QLabel, "ResultCardRelevanceLabel")
        assert label is not None and label.text() == "관련성 낮음"
        assert card.graphicsEffect() is not None
        assert card.graphicsEffect().opacity() == 0.5

    def test_normal_relevance_has_no_dimming(self, qtbot):
        image = ImageData(image_path="아무경로.png")
        card = ImageCard(_hybrid(_image_result(image), low=False))
        qtbot.addWidget(card)

        assert card.findChild(QLabel, "ResultCardRelevanceLabel") is None
        assert card.graphicsEffect() is None

    def test_filename_only_match_shows_badge(self, qtbot):
        """T10.6: 표 카드와 같은 이유로 회귀 테스트를 따로 둔다."""
        image = ImageData(image_path="아무경로.png")
        card = ImageCard(_hybrid(_image_result(image), matched_terms=0, total_terms=1))
        qtbot.addWidget(card)

        label = card.findChild(QLabel, "ResultCardFileNameMatchLabel")
        assert label is not None and label.text() == "파일명 매치"


class TestTypeBasedRouting:
    def test_mixed_results_render_matching_card_types(self, qtbot):
        widget = ResultList()
        qtbot.addWidget(widget)

        text_result = _hybrid(
            SearchResult(
                chunk_id="x1",
                doc_id="d1",
                file_path="a.docx",
                file_name="a.docx",
                type=ChunkType.TEXT,
                page_or_slide=1,
                content="본문",
                caption="",
                score=-1.0,
            )
        )
        table_result = _hybrid(_table_result(TableData(rows=[["a"]], header_row=[])))
        image_result = _hybrid(_image_result(ImageData(image_path="없음.png")))

        widget.show_results([text_result, table_result, image_result], "질의")

        assert widget.card_count() == 3
        assert widget.findChild(ResultCard) is not None
        assert widget.findChild(TableCard) is not None
        assert widget.findChild(ImageCard) is not None

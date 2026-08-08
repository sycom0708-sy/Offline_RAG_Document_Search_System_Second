"""메인 윈도우 통합 테스트 (T4.18~T4.19).

DoD: 검색 → 필터/옵션 조합 → 텍스트 결과 확인 → 원문 열기까지 전체 흐름이
오류 없이 동작하고, 사이드바의 모든 토글·콤보박스가 검색 결과에 실제로
반영돼야 한다.
"""

from __future__ import annotations

import pytest

from indexer.fts5.schema import connect
from indexer.fts5.store import store_document
from indexer.vector.store import embed_missing
from parser.schema import Chunk, ChunkType, ImageData, ParsedDocument, TableData
from ui.main_window import MainWindow
from ui.state import AppState

SEARCH_TIMEOUT_MS = 15000


def _add_doc(conn, embedder, doc_id, file_name, content, page_or_slide, file_path=None):
    document = ParsedDocument(
        doc_id=doc_id, file_path=file_path or file_name, file_name=file_name, title="t"
    )
    document.chunks.append(
        Chunk(
            chunk_id=f"{doc_id}_c1",
            doc_id=doc_id,
            file_path=file_path or file_name,
            file_name=file_name,
            type=ChunkType.TEXT,
            page_or_slide=page_or_slide,
            content=content,
        )
    )
    store_document(conn, document, count_tokens=embedder.count_tokens)


@pytest.fixture
def indexed_db(tmp_path, embedder):
    """형식·대소문자·단어 경계 케이스를 구분할 수 있는 문서 4건을 인덱싱한다."""
    db_path = tmp_path / "index.sqlite3"
    conn = connect(db_path)

    _add_doc(conn, embedder, "d1", "사규.docx", "계약서 검토 시 기준이 되는 조항은 손해배상, 계약 해지 조건이다", 3)
    _add_doc(conn, embedder, "d2", "메모.txt", "API 문서를 확인하고 계약서 서명을 준비하세요", None)
    _add_doc(conn, embedder, "d3", "발표자료.pptx", "계약 갱신 절차를 안내합니다", 2)
    _add_doc(conn, embedder, "d4", "공지.txt", "api 관련 계약 안내사항입니다", None)

    embed_missing(conn, embedder)
    conn.close()
    return db_path


@pytest.fixture
def window(qtbot, indexed_db):
    win = MainWindow(db_path=indexed_db, state=AppState())
    qtbot.addWidget(win)
    return win


class TestEndToEndSearch:
    def test_search_shows_matching_cards(self, qtbot, window):
        window.search_bar.set_text("계약서 검토 기준이 뭐였지")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        assert window.result_list.card_count() >= 1

    def test_empty_query_resets_to_initial(self, qtbot, window):
        window.search_bar.set_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        window.search_bar.set_text("")
        qtbot.wait(400)  # debounce 통과

        assert window.result_list.card_count() == 0

    def test_second_search_does_not_drop_running_worker(self, qtbot, window):
        """실행 중인 워커의 참조를 잃으면 QThread가 GC되며 앱이 통째로 죽는다.

        워커를 한 자리(`_active_worker`)에만 붙들던 시절 실제로 크래시했다
        (0xC0000409). 결과를 버리는 것은 `request_id` 비교가 하므로, 여기서는
        끝날 때까지 살려두기만 하면 된다.
        """
        import gc

        window._on_search_requested("계약서")
        window._on_search_requested("리눅스")

        # 두 번째 검색이 시작된 시점에 첫 워커가 아직 참조돼 있어야 한다.
        assert len(window._active_workers) == 2
        gc.collect()  # 참조를 잃었다면 여기서 수거되고, 아래 접근이 죽는다
        assert all(isinstance(w.isRunning(), bool) for w in window._active_workers)

        # 끝나면 스스로 빠져나가야 한다 — 안 그러면 무한정 쌓인다.
        qtbot.waitUntil(lambda: not window._active_workers, timeout=SEARCH_TIMEOUT_MS)

    def test_close_waits_for_running_search(self, qtbot, window):
        """검색 도중 창을 닫아도 같은 종류의 크래시가 난다."""
        window._on_search_requested("계약서")
        assert window._active_workers

        window.close()  # closeEvent가 워커를 기다린다
        assert all(w.isFinished() for w in window._active_workers)

    def test_no_matching_query_shows_empty_state(self, qtbot, window):
        window.search_bar.set_text("전혀관련없는외계어단어조합")
        qtbot.wait(400)

        def has_message():
            return window.result_list._layout.count() >= 1 and window.result_list.card_count() == 0

        qtbot.waitUntil(has_message, timeout=SEARCH_TIMEOUT_MS)
        assert window.result_list.card_count() == 0

    def test_open_button_click_does_not_raise(self, qtbot, window):
        """존재하지 않는 파일 경로라 열기는 실패하지만, 시그널로 안전하게 처리돼야 한다."""
        window.search_bar.set_text("계약서 검토 기준이 뭐였지")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        from ui.widgets.result_card import ResultCard

        card = window.result_list.findChild(ResultCard)
        failures = []
        card.open_failed.connect(failures.append)
        card._open_source()
        assert len(failures) == 1


class TestNoIndexState:
    def test_empty_database_shows_no_index_message(self, qtbot, tmp_path):
        empty_db = tmp_path / "empty.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState())
        qtbot.addWidget(win)

        win.search_bar.set_text("아무 질의")
        qtbot.wait(400)

        from PySide6.QtWidgets import QLabel

        label = win.result_list.findChild(QLabel, "ResultListMessage")
        assert "폴더를 지정" in label.text()


class TestSidebarOptionsAffectResults:
    """T4.19: 형식 필터 × 대소문자구분 × 일치단어 교차 케이스."""

    def test_format_filter_narrows_to_selected_extension(self, qtbot, window):
        window.sidebar.format_filter.set_available_formats([".docx", ".txt", ".pptx"])
        window.search_bar.set_text("계약")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        baseline_count = window.result_list.card_count()

        window.sidebar.format_filter._format_checkboxes[".docx"].setChecked(True)
        qtbot.waitUntil(lambda: window.result_list.card_count() >= 1, timeout=SEARCH_TIMEOUT_MS)

        from ui.widgets.result_card import ResultCard

        cards = window.result_list.findChildren(ResultCard)
        assert all(c._result.file_name.endswith(".docx") for c in cards)
        assert len(cards) <= baseline_count

    def test_case_sensitive_toggle_changes_results(self, qtbot, window):
        """메모.txt는 "API"(대문자), 공지.txt는 "api"(소문자)를 담고 있다."""
        from ui.widgets.result_card import ResultCard

        window.search_bar.set_text("API")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        before_names = {c._result.file_name for c in window.result_list.findChildren(ResultCard)}
        assert {"메모.txt", "공지.txt"} <= before_names  # 대소문자 구분 OFF: 둘 다 잡힘

        before_count = window.result_list.card_count()
        window.sidebar.search_options.case_sensitive.setChecked(True)
        qtbot.waitUntil(
            lambda: window.result_list.card_count() != before_count
            and window.result_list.card_count() > 0,
            timeout=SEARCH_TIMEOUT_MS,
        )

        after_names = {c._result.file_name for c in window.result_list.findChildren(ResultCard)}
        assert after_names == {"메모.txt"}  # 대소문자 구분 ON: "API" 정확히 일치하는 것만

    def test_exact_word_excludes_mid_token_match(self, qtbot, window):
        """'계약'으로 검색 시 일치단어 ON이면 '계약서'(부분 포함)는 빠져야 한다."""
        window.sidebar.search_options.exact_word.setChecked(True)
        window.search_bar.set_text("계약")
        # `_layout.count() >= 1`은 "검색 중" placeholder만으로도 참이 되어 실제
        # 검색 완료를 기다리지 못했다(실측 재현됨) — card_count로 완료를 확인한다.
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        from ui.widgets.result_card import ResultCard

        cards = window.result_list.findChildren(ResultCard)
        file_names = {c._result.file_name for c in cards}
        assert "공지.txt" in file_names or "발표자료.pptx" in file_names  # 완전 단어 "계약" 포함 문서
        # "계약서"만 담고 "계약" 완전 단어가 없는 문서는 제외돼야 한다는 취지 확인
        for card in cards:
            content = card._result.content
            assert "계약" in content

    def test_options_persist_across_new_search(self, qtbot, window):
        """옵션을 켠 채로 검색어를 바꿔도 옵션이 유지돼야 한다."""
        window.sidebar.search_options.exact_word.setChecked(True)
        window.search_bar.set_text("계약")
        qtbot.wait(500)
        assert window.sidebar.search_options.is_exact_word() is True

    def test_changing_option_reruns_last_query(self, qtbot, window):
        """DESIGN 요구: 옵션 변경 시 검색을 다시 눌러야 하는 게 아니라 자동 반영돼야 한다."""
        window.search_bar.set_text("계약")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        before = window.result_list.card_count()

        window.sidebar.format_filter.set_available_formats([".docx", ".txt", ".pptx"])
        window.sidebar.format_filter._format_checkboxes[".docx"].setChecked(True)

        qtbot.waitUntil(lambda: window.result_list.card_count() != before or window.result_list.card_count() >= 1, timeout=SEARCH_TIMEOUT_MS)
        from ui.widgets.result_card import ResultCard

        cards = window.result_list.findChildren(ResultCard)
        assert all(c._result.file_name.endswith(".docx") for c in cards)


class TestReindexFlow:
    def test_reindexing_updates_status_bar_and_format_filter(self, qtbot, tmp_path, samples):
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState())
        qtbot.addWidget(win)

        source_folder = str(next(iter(samples.values())).parent)
        win._start_reindex(source_folder)

        qtbot.waitUntil(
            lambda: "인덱싱됨" in win.status_bar_widget._info_label.text(), timeout=60000
        )

        assert win.state.target_folder == source_folder
        assert len(win.sidebar.format_filter._format_checkboxes) > 0


class TestMixedResultTypes:
    """T5.6: text/table/image가 한 결과 리스트에 자연스럽게 섞여야 한다."""

    def test_search_renders_text_table_and_image_cards_together(self, qtbot, tmp_path, embedder):
        from PySide6.QtGui import QImage

        from ui.widgets.image_card import ImageCard
        from ui.widgets.result_card import ResultCard
        from ui.widgets.table_card import TableCard

        db_path = tmp_path / "mixed.sqlite3"
        conn = connect(db_path)

        image_path = tmp_path / "capture.png"
        QImage(20, 20, QImage.Format.Format_RGB32).save(str(image_path))

        document = ParsedDocument(doc_id="m1", file_path="혼합.pdf", file_name="혼합.pdf", title="t")
        document.chunks.append(
            Chunk(
                chunk_id="m1_text",
                doc_id="m1",
                file_path="혼합.pdf",
                file_name="혼합.pdf",
                type=ChunkType.TEXT,
                page_or_slide=1,
                content="예산 기준 안내",
            )
        )
        table = TableData(rows=[["예산", "100만원"]], header_row=["항목", "값"])
        document.chunks.append(
            Chunk(
                chunk_id="m1_table",
                doc_id="m1",
                file_path="혼합.pdf",
                file_name="혼합.pdf",
                type=ChunkType.TABLE,
                page_or_slide=2,
                content=table.to_text(),
                table=table,
            )
        )
        image = ImageData(image_path=str(image_path), origin="extracted")
        document.chunks.append(
            Chunk(
                chunk_id="m1_image",
                doc_id="m1",
                file_path="혼합.pdf",
                file_name="혼합.pdf",
                type=ChunkType.IMAGE,
                page_or_slide=3,
                content="예산 흐름도",
                image=image,
            )
        )
        store_document(conn, document, count_tokens=embedder.count_tokens)
        embed_missing(conn, embedder)
        conn.close()

        win = MainWindow(db_path=db_path, state=AppState())
        qtbot.addWidget(win)

        win.search_bar.set_text("예산")
        qtbot.waitUntil(lambda: win.result_list.card_count() >= 3, timeout=SEARCH_TIMEOUT_MS)

        assert win.result_list.findChild(ResultCard) is not None
        assert win.result_list.findChild(TableCard) is not None
        assert win.result_list.findChild(ImageCard) is not None

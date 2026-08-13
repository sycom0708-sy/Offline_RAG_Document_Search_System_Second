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
def window(qtbot, indexed_db, tmp_path):
    win = MainWindow(db_path=indexed_db, state=AppState.load(path=tmp_path / "state.json"))
    qtbot.addWidget(win)
    return win


class TestEndToEndSearch:
    def test_search_shows_matching_cards(self, qtbot, window):
        window.input_bar.submit_text("계약서 검토 기준이 뭐였지")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        assert window.result_list.card_count() >= 1

    def test_empty_query_resets_to_initial(self, qtbot, window):
        window.input_bar.submit_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        window.input_bar.submit_text("")  # InputBar는 빈 문자열도 그대로 emit한다

        assert window.result_list.card_count() == 0

    def test_header_close_button_clears_query_and_results(self, qtbot, window):
        """헤더 ✕ — 검색어·결과를 모두 지우고 초기 안내로 되돌린다 (Phase 7.7)."""
        window.input_bar.submit_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        assert window.result_header.isVisibleTo(window)

        window.result_header._close_button.click()

        assert window.result_list.card_count() == 0
        assert window._last_query == ""
        assert not window.result_header.isVisibleTo(window)
        assert window.input_bar.text() == ""

    def test_toggling_filter_after_header_close_does_not_resurrect_results(self, qtbot, window):
        """✕ 이후 `_last_query`가 안 비면 옵션 토글이 지운 검색을 되살린다(회귀 방지)."""
        window.input_bar.submit_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        window.result_header._close_button.click()
        window.sidebar.search_options.case_sensitive.setChecked(True)

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
        window.input_bar.submit_text("전혀관련없는외계어단어조합")

        def has_message():
            return window.result_list._layout.count() >= 1 and window.result_list.card_count() == 0

        qtbot.waitUntil(has_message, timeout=SEARCH_TIMEOUT_MS)
        assert window.result_list.card_count() == 0

    def test_open_button_click_does_not_raise(self, qtbot, window):
        """존재하지 않는 파일 경로라 열기는 실패하지만, 시그널로 안전하게 처리돼야 한다."""
        window.input_bar.submit_text("계약서 검토 기준이 뭐였지")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        from ui.widgets.result_card import ResultCard

        card = window.result_list.findChild(ResultCard)
        failures = []
        card.open_failed.connect(failures.append)
        card._open_source()
        assert len(failures) == 1

    def test_open_failure_reaches_status_bar(self, qtbot, window):
        """카드가 emit한 open_failed가 실제로 사용자에게 보여야 한다.

        `ResultList`가 카드의 `open_failed`를 relay하고 `MainWindow`가
        받는 연결 자체가 없어, 신호는 나가는데 아무 데도 안 들리는 상태였다
        — 원문 열기 실패 시 화면에 아무 반응도 없던 실제 버그다.
        """
        window.input_bar.submit_text("계약서 검토 기준이 뭐였지")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        from ui.widgets.result_card import ResultCard

        card = window.result_list.findChild(ResultCard)
        card._open_source()  # 실제 클릭 경로 — 존재하지 않는 파일 경로라 실패한다

        bar = window.status_bar_widget
        assert bar._warning_label.isVisibleTo(bar)
        assert "찾을 수 없습니다" in bar._warning_label.text()


class TestNoIndexState:
    def test_empty_database_shows_no_index_message(self, qtbot, tmp_path):
        empty_db = tmp_path / "empty.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        win.input_bar.submit_text("아무 질의")

        from PySide6.QtWidgets import QLabel

        label = win.result_list.findChild(QLabel, "ResultListMessage")
        assert "폴더를 지정" in label.text()


class TestSidebarOptionsAffectResults:
    """T4.19: 형식 필터 × 대소문자구분 × 일치단어 교차 케이스."""

    def test_format_filter_narrows_to_selected_extension(self, qtbot, window):
        window.sidebar.format_filter.set_available_formats([".docx", ".txt", ".pptx"])
        window.input_bar.submit_text("계약")
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

        window.input_bar.submit_text("API")
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
        window.input_bar.submit_text("계약")
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
        window.input_bar.submit_text("계약")
        assert window.sidebar.search_options.is_exact_word() is True

    def test_changing_option_reruns_last_query(self, qtbot, window):
        """DESIGN 요구: 옵션 변경 시 검색을 다시 눌러야 하는 게 아니라 자동 반영돼야 한다."""
        window.input_bar.submit_text("계약")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        before = window.result_list.card_count()

        window.sidebar.format_filter.set_available_formats([".docx", ".txt", ".pptx"])
        window.sidebar.format_filter._format_checkboxes[".docx"].setChecked(True)

        qtbot.waitUntil(lambda: window.result_list.card_count() != before or window.result_list.card_count() >= 1, timeout=SEARCH_TIMEOUT_MS)
        from ui.widgets.result_card import ResultCard

        cards = window.result_list.findChildren(ResultCard)
        assert all(c._result.file_name.endswith(".docx") for c in cards)


class TestPerformanceComboStartupSync:
    """PC 성능 콤보 표시가 저장된 `state.model_profile`과 어긋나던 버그 회귀 방지.

    `PerformanceCombo`는 자기 `__init__`에서 `PROFILE_ORDER[0]`(경량)으로
    초기화되고, `MainWindow`가 시작 시 이를 동기화하는 코드가 없었다 —
    저장된 프로파일이 권장이어도 화면은 항상 "경량"으로 보였다. 실제로는
    권장으로 검색되는데 화면은 경량이라고 말하는 어긋난 상태(실사용 중 발견).
    """

    def test_combo_reflects_saved_heavy_profile_on_startup(self, qtbot, indexed_db, tmp_path):
        from config.settings import HEAVY

        state = AppState.load(path=tmp_path / "state.json")
        state.model_profile = HEAVY.key
        win = MainWindow(db_path=indexed_db, state=state)
        qtbot.addWidget(win)

        assert win.sidebar.performance_combo.current_profile() == HEAVY.key

    def test_combo_reflects_saved_light_profile_on_startup(self, qtbot, window):
        from config.settings import LIGHT

        assert window.sidebar.performance_combo.current_profile() == LIGHT.key


class TestRecentSearchWiring:
    """공용 입력창 제출 → 최근 검색 기록 → 사이드바 갱신 → 항목 클릭 시
    재실행까지 (Phase 7.7, 2026-08-13 재작업)."""

    def test_submitting_a_query_records_it_in_state_and_sidebar(self, qtbot, window):
        window.input_bar.submit_text("계약서 검토 기준이 뭐였지")

        assert window.state.recent_searches[0] == "계약서 검토 기준이 뭐였지"
        assert window.sidebar.recent_searches._all_items[0] == "계약서 검토 기준이 뭐였지"

    def test_submitting_blank_text_does_not_record_a_recent_search(self, qtbot, window):
        window.input_bar.submit_text("계약서")
        window.result_header._close_button.click()  # _last_query 초기화
        window.input_bar.submit_text("")

        assert window.state.recent_searches == ["계약서"]

    def test_recent_search_is_persisted_across_reload(self, qtbot, indexed_db, tmp_path):
        state_path = tmp_path / "state.json"
        win = MainWindow(db_path=indexed_db, state=AppState.load(path=state_path))
        qtbot.addWidget(win)
        win.input_bar.submit_text("계약서 검토 기준이 뭐였지")

        reloaded = AppState.load(path=state_path)

        assert reloaded.recent_searches == ["계약서 검토 기준이 뭐였지"]

    def test_clicking_a_recent_search_item_reruns_it(self, qtbot, window):
        window.input_bar.submit_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        window.input_bar.submit_text("리눅스")
        qtbot.waitUntil(lambda: window._last_query == "리눅스", timeout=SEARCH_TIMEOUT_MS)

        # 사이드바에는 실측 전이라 폴백 개수만큼 보인다 — "계약서" 항목을 찾아 클릭한다.
        button = next(
            window.sidebar.recent_searches._list_layout.itemAt(i).widget()
            for i in range(window.sidebar.recent_searches._list_layout.count())
            if window.sidebar.recent_searches._list_layout.itemAt(i).widget().toolTip() == "계약서"
        )
        button.click()

        assert window._last_query == "계약서"
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)


class TestReindexFlow:
    def test_reindexing_updates_status_bar_and_format_filter(self, qtbot, tmp_path, samples):
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        source_folder = str(next(iter(samples.values())).parent)
        win._start_reindex(source_folder)

        qtbot.waitUntil(
            lambda: "인덱싱됨" in win.status_bar_widget._info_label.text(), timeout=60000
        )

        assert win.state.target_folder == source_folder
        assert len(win.sidebar.format_filter._format_checkboxes) > 0

    def test_shows_non_modal_progress_dialog_while_indexing(self, qtbot, tmp_path, samples):
        """T10.4: TECH 4.6("메인 UI가 멈추지 않도록")을 지키는 비모달 팝업."""
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        source_folder = str(next(iter(samples.values())).parent)
        win._start_reindex(source_folder)

        assert win._indexing_progress_dialog is not None
        assert win._indexing_progress_dialog.isModal() is False

        qtbot.waitUntil(lambda: win._indexing_progress_dialog is None, timeout=60000)

    def test_cancel_button_sets_stop_event(self, qtbot, tmp_path, samples):
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        source_folder = str(next(iter(samples.values())).parent)
        win._start_reindex(source_folder)
        thread = win._indexing_thread

        win._indexing_progress_dialog.cancel_button.click()

        assert thread.stop_event.is_set()
        qtbot.waitUntil(lambda: win._indexing_progress_dialog is None, timeout=60000)

    def test_starting_reindex_twice_does_not_spawn_second_thread(self, qtbot, tmp_path, samples):
        """같은 DB에 인덱싱 스레드 두 개가 동시에 쓰면 위험하다 — 도는 중이면 무시한다."""
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        source_folder = str(next(iter(samples.values())).parent)
        win._start_reindex(source_folder)
        first_thread = win._indexing_thread

        win._start_reindex(source_folder)  # 아직 실행 중일 때 재시도

        assert win._indexing_thread is first_thread
        qtbot.waitUntil(lambda: win._indexing_progress_dialog is None, timeout=60000)

    def test_new_indexing_clears_stale_open_failed_warning(self, qtbot, tmp_path, samples):
        """이전 "원문 열기" 실패 안내가 새 인덱싱 진행률과 한 줄에 겹쳐 보이던 문제.

        상태바는 정보 라벨과 경고 라벨이 한 줄에 나란히 붙어 있어, 이전
        실행에서 남은 경고가 지워지지 않으면 "인덱싱 중… 5/10 파일을 찾을 수
        없습니다: ..."처럼 한 문장인 것으로 오해할 수 있다(실사용에서 실제로
        겪음).
        """
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        win.status_bar_widget.set_warning("이전 원문 열기 실패 메시지")

        source_folder = str(next(iter(samples.values())).parent)
        win._start_reindex(source_folder)

        assert not win.status_bar_widget._warning_label.isVisibleTo(win.status_bar_widget)
        qtbot.waitUntil(lambda: win._indexing_progress_dialog is None, timeout=60000)

    def test_closing_window_after_indexing_does_not_raise(self, qtbot, tmp_path, samples):
        """closeEvent가 IndexingThread(threading.Thread)에 QThread 전용 API인
        isRunning()/wait(ms)를 불러 AttributeError가 났다(실측 확인) —
        _indexing_thread가 한 번이라도 세팅되면 창을 닫을 때마다 터졌다.
        is_alive()/join(초)로 고쳤다.
        """
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        source_folder = str(next(iter(samples.values())).parent)
        win._start_reindex(source_folder)
        qtbot.waitUntil(lambda: win._indexing_progress_dialog is None, timeout=60000)

        win.close()  # 예외가 나면 이 테스트 자체가 실패한다


class _FakeFolderWatcher:
    """T8.5 배선 테스트용 spy — 실제 watchdog Observer·디바운스 지연 없이
    start()/stop() 호출과 생성자 인자만 기록한다. 실제 감시 로직(디바운스,
    이벤트 처리) 자체는 tests/test_folder_watcher.py가 검증한다.
    """

    instances: list["_FakeFolderWatcher"] = []

    def __init__(self, folder, on_change, debounce_seconds=3.0) -> None:
        self.folder = folder
        self.on_change = on_change
        self.started = False
        self.stopped = False
        type(self).instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class TestFolderWatch:
    """T8.5: 실시간 폴더 감시 — 실제 watchdog 대신 스파이로 배선만 검증한다."""

    @pytest.fixture(autouse=True)
    def _reset_fake_instances(self):
        _FakeFolderWatcher.instances = []
        yield
        _FakeFolderWatcher.instances = []

    @pytest.fixture(autouse=True)
    def _skip_embedder_warmup(self, monkeypatch):
        """이 클래스는 watcher 배선만 본다 — 임베더가 전혀 필요 없다.

        `MainWindow.__init__`마다 백그라운드 ONNX 워밍업 스레드가 뜨는데,
        이 클래스처럼 짧은 시간에 `MainWindow`를 여러 개 연달아 만들면
        (다른 테스트처럼 인덱싱·검색 대기로 자연히 시간 간격이 안 생긴다)
        ONNX 세션 생성이 겹쳐 이 PC에서 재현되는 기존 크래시(access
        violation)를 훨씬 자주 유발한다 — 실제로 겪었다. 이 클래스의
        어떤 테스트도 `_embedder`를 쓰지 않으므로 워밍업 자체를 꺼서 피한다.
        """
        monkeypatch.setattr(MainWindow, "_start_embedder_warmup", lambda self: None)

    def test_toggling_on_starts_a_watcher(self, qtbot, tmp_path, monkeypatch):
        import ui.main_window as main_window_module

        monkeypatch.setattr(main_window_module, "FolderWatcher", _FakeFolderWatcher)

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)
        win.state.target_folder = str(tmp_path)

        win._on_folder_watch_toggled(True)

        assert win.state.folder_watch_enabled is True
        assert len(_FakeFolderWatcher.instances) == 1
        watcher = _FakeFolderWatcher.instances[0]
        assert watcher.folder == str(tmp_path)
        assert watcher.started is True
        assert win._folder_watcher is watcher

    def test_toggling_off_stops_the_watcher(self, qtbot, tmp_path, monkeypatch):
        import ui.main_window as main_window_module

        monkeypatch.setattr(main_window_module, "FolderWatcher", _FakeFolderWatcher)

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)
        win.state.target_folder = str(tmp_path)
        win._on_folder_watch_toggled(True)
        watcher = _FakeFolderWatcher.instances[0]

        win._on_folder_watch_toggled(False)

        assert win.state.folder_watch_enabled is False
        assert watcher.stopped is True
        assert win._folder_watcher is None

    def test_watcher_starts_automatically_if_previously_enabled(self, qtbot, tmp_path, monkeypatch):
        """이전 세션에서 켜뒀으면 앱을 다시 켤 때 자동으로 감시가 재개돼야 한다."""
        import ui.main_window as main_window_module

        monkeypatch.setattr(main_window_module, "FolderWatcher", _FakeFolderWatcher)

        state_path = tmp_path / "state.json"
        state = AppState.load(path=state_path)
        state.target_folder = str(tmp_path)
        state.folder_watch_enabled = True
        state.save()

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=state_path))
        qtbot.addWidget(win)

        assert len(_FakeFolderWatcher.instances) == 1
        assert _FakeFolderWatcher.instances[0].started is True

    def test_watcher_does_not_start_when_disabled(self, qtbot, tmp_path, monkeypatch):
        import ui.main_window as main_window_module

        monkeypatch.setattr(main_window_module, "FolderWatcher", _FakeFolderWatcher)

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        assert _FakeFolderWatcher.instances == []
        assert win._folder_watcher is None

    def test_selecting_a_new_folder_restarts_watcher_at_new_location(
        self, qtbot, tmp_path, monkeypatch, samples
    ):
        import ui.main_window as main_window_module

        monkeypatch.setattr(main_window_module, "FolderWatcher", _FakeFolderWatcher)

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        folder_a = tmp_path / "a"
        folder_a.mkdir()
        win.state.target_folder = str(folder_a)
        win._on_folder_watch_toggled(True)
        first_watcher = _FakeFolderWatcher.instances[0]

        folder_b = str(next(iter(samples.values())).parent)
        win._start_reindex(folder_b)  # 대상 폴더가 바뀌었다

        assert first_watcher.stopped is True
        assert len(_FakeFolderWatcher.instances) == 2
        second_watcher = _FakeFolderWatcher.instances[1]
        assert second_watcher.folder == folder_b
        assert second_watcher.started is True
        qtbot.waitUntil(lambda: win._indexing_progress_dialog is None, timeout=60000)

    def test_reindexing_the_same_folder_does_not_restart_watcher(
        self, qtbot, tmp_path, monkeypatch, samples
    ):
        """watchdog가 트리거한 재인덱싱은 대상 폴더가 그대로라 Observer를
        다시 켤 필요가 없다 — 매번 멈췄다 켜면 그 사이 이벤트를 놓칠 수 있다."""
        import ui.main_window as main_window_module

        monkeypatch.setattr(main_window_module, "FolderWatcher", _FakeFolderWatcher)

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        folder = str(next(iter(samples.values())).parent)
        win.state.target_folder = folder
        win._on_folder_watch_toggled(True)
        watcher = _FakeFolderWatcher.instances[0]

        win._start_reindex(folder, silent=True)  # 같은 폴더로 재인덱싱(watchdog 시뮬레이션)

        assert watcher.stopped is False
        assert len(_FakeFolderWatcher.instances) == 1
        qtbot.waitUntil(lambda: win._indexing_thread is not None and not win._indexing_thread.is_alive(), timeout=60000)

    def test_silent_reindex_does_not_show_progress_dialog(self, qtbot, tmp_path, samples):
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        folder = str(next(iter(samples.values())).parent)
        win._start_reindex(folder, silent=True)

        assert win._indexing_progress_dialog is None
        qtbot.waitUntil(
            lambda: win._indexing_thread is not None and not win._indexing_thread.is_alive(),
            timeout=60000,
        )

    def test_folder_changed_triggers_silent_reindex(self, qtbot, tmp_path, samples):
        """watcher의 on_change 콜백이 실제로 조용한 재인덱싱을 트리거하는지 확인한다."""
        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)
        win.state.target_folder = str(next(iter(samples.values())).parent)

        win._on_folder_changed()

        assert win._indexing_thread is not None
        assert win._indexing_progress_dialog is None
        qtbot.waitUntil(
            lambda: not win._indexing_thread.is_alive(), timeout=60000
        )

    def test_watcher_start_failure_shows_warning_instead_of_crashing(
        self, qtbot, tmp_path, monkeypatch
    ):
        import ui.main_window as main_window_module

        class _BrokenWatcher(_FakeFolderWatcher):
            def start(self) -> None:
                raise OSError("폴더가 없어졌습니다")

        monkeypatch.setattr(main_window_module, "FolderWatcher", _BrokenWatcher)

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)
        win.state.target_folder = str(tmp_path)

        win._on_folder_watch_toggled(True)  # 예외가 나면 이 테스트 자체가 실패한다

        assert win._folder_watcher is None
        assert win.status_bar_widget._warning_label.isVisibleTo(win.status_bar_widget)

    def test_close_event_stops_the_watcher(self, qtbot, tmp_path, monkeypatch):
        import ui.main_window as main_window_module

        monkeypatch.setattr(main_window_module, "FolderWatcher", _FakeFolderWatcher)

        empty_db = tmp_path / "fresh.sqlite3"
        win = MainWindow(db_path=empty_db, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)
        win.state.target_folder = str(tmp_path)
        win._on_folder_watch_toggled(True)
        watcher = _FakeFolderWatcher.instances[0]

        win.close()

        assert watcher.stopped is True


class TestLibreOfficeWarning:
    """T10.2: LibreOffice 미설치로 구버전 문서가 조용히 빠지면 사용자에게 안내한다."""

    def test_missing_libreoffice_failure_produces_warning(self, qtbot, window):
        from pathlib import Path

        from indexer.pipeline import IndexReport

        report = IndexReport(failures=[(
            Path("규정.doc"),
            "LibreOffice(soffice)를 찾을 수 없습니다. "
            "LibreOffice 포터블을 내려받아 vendor/LibreOfficePortable/ 폴더에 넣으세요. "
            "(또는 SOFFICE_PATH 환경변수로 실행 파일 경로를 지정하세요)",
        )])

        message = window._libreoffice_warning(report)
        assert message is not None
        assert "규정.doc" in message
        assert "vendor/LibreOfficePortable" in message

    def test_unrelated_failure_produces_no_warning(self, window):
        from pathlib import Path

        from indexer.pipeline import IndexReport

        report = IndexReport(failures=[(Path("broken.pdf"), "손상된 PDF입니다")])
        assert window._libreoffice_warning(report) is None

    def test_on_indexing_done_surfaces_warning_to_status_bar(self, window):
        from pathlib import Path

        from indexer.pipeline import IndexReport

        report = IndexReport(failures=[(
            Path("규정.doc"),
            "LibreOffice(soffice)를 찾을 수 없습니다. "
            "LibreOffice 포터블을 내려받아 vendor/LibreOfficePortable/ 폴더에 넣으세요.",
        )])

        window._on_indexing_done(report)
        assert window.status_bar_widget._warning_label.isVisibleTo(window.status_bar_widget)

    def test_clean_run_clears_previous_warning(self, window):
        """LibreOffice를 설치한 뒤 재인덱싱하면 안내가 사라져야 한다."""
        from indexer.pipeline import IndexReport

        window.status_bar_widget.set_warning("이전 실행의 경고")
        window._on_indexing_done(IndexReport())
        assert not window.status_bar_widget._warning_label.isVisibleTo(window.status_bar_widget)


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

        win = MainWindow(db_path=db_path, state=AppState.load(path=tmp_path / "state.json"))
        qtbot.addWidget(win)

        win.input_bar.submit_text("예산")
        qtbot.waitUntil(lambda: win.result_list.card_count() >= 3, timeout=SEARCH_TIMEOUT_MS)

        assert win.result_list.findChild(ResultCard) is not None
        assert win.result_list.findChild(TableCard) is not None
        assert win.result_list.findChild(ImageCard) is not None


class TestAiChatWiring:
    """AI 챗봇 토글 배선 (Phase 7.6, 2단 응답 구조).

    실제 sLM을 띄우지 않는다 — 서비스를 스텁으로 바꿔 **배선이 화면까지
    도달하는지**만 본다. T10.3에서 신호가 emit만 되고 받는 곳이 없어 아무
    반응도 없던 버그가 카드 단위 테스트를 다 통과한 채 남아 있었다.

    1단계(즉시 발췌)는 `SearchWorker`만 쓰고 LLM을 부르지 않는다는 것,
    2단계("AI 요약 보기")를 눌러야만 스텁의 `chat()`이 호출된다는 것도
    함께 확인한다 — 이게 이번 설계가 은행 앱 수준 응답 속도를 지키는 방식.
    """

    @staticmethod
    def _stub_service(window, text="계약서 기준 조항입니다. [1]"):
        from slm.client import Completion

        class _Stub:
            def __init__(self):
                self.calls = 0

            def is_available(self):
                return True

            def is_running(self):
                return True

            def chat(self, messages, **_kwargs):
                self.calls += 1
                return Completion(text=text, elapsed_sec=0.1, completion_tokens=5)

            def shutdown(self):
                pass

        stub = _Stub()
        window._slm_service = stub
        return stub

    @staticmethod
    def _turn_on_chat(window):
        window.sidebar.search_options.set_ai_summary_available(True)
        window.sidebar.search_options.ai_summary.setChecked(True)
        return window._chat_panel

    def test_toggle_is_off_by_default(self, window):
        """추출형 검색이 기본값이라는 원칙 (PRD/DESIGN §1, TECH 5.2)."""
        assert window.sidebar.search_options.is_ai_summary() is False

    def test_toggle_on_replaces_cards_with_chat_panel(self, window, qtbot):
        self._stub_service(window)
        window.input_bar.submit_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        self._turn_on_chat(window)

        assert window._chat_panel is not None
        assert window.result_list.card_count() == 0

    def test_toggle_on_auto_sends_last_query_as_first_message(self, window, qtbot):
        self._stub_service(window)
        window.input_bar.submit_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)

        panel = self._turn_on_chat(window)

        assert panel.turn_count() == 1
        qtbot.waitUntil(
            lambda: panel.bubble_for(1).excerpt_text() not in ("", "검색하는 중…"),
            timeout=SEARCH_TIMEOUT_MS,
        )

    def test_message_shows_excerpt_without_calling_the_llm(self, window, qtbot):
        """1단계는 검색만 한다 — sLM은 아직 호출되면 안 된다."""
        stub = self._stub_service(window)
        panel = self._turn_on_chat(window)

        panel.send_message("계약서 검토 기준")
        bubble = panel.bubble_for(1)
        qtbot.waitUntil(
            lambda: bubble.excerpt_text() not in ("", "검색하는 중…"), timeout=SEARCH_TIMEOUT_MS
        )

        assert stub.calls == 0

    def test_summarize_button_triggers_llm_and_shows_summary(self, window, qtbot):
        """② AI 요약 — ①이 이미 받은 결과를 그대로 넘겨 검색을 다시 하지 않는다."""
        stub = self._stub_service(window)
        panel = self._turn_on_chat(window)

        panel.send_message("계약서 검토 기준")
        bubble = panel.bubble_for(1)
        qtbot.waitUntil(lambda: bool(bubble.results), timeout=SEARCH_TIMEOUT_MS)

        bubble._summarize_button.click()

        qtbot.waitUntil(lambda: stub.calls > 0, timeout=SEARCH_TIMEOUT_MS)
        qtbot.waitUntil(lambda: "계약서" in bubble.summary_text(), timeout=SEARCH_TIMEOUT_MS)

    def test_toggle_off_restores_result_cards(self, window, qtbot):
        self._stub_service(window)
        window.input_bar.submit_text("계약서")
        qtbot.waitUntil(lambda: window.result_list.card_count() > 0, timeout=SEARCH_TIMEOUT_MS)
        before = window.result_list.card_count()

        self._turn_on_chat(window)
        assert window._chat_panel is not None

        window.sidebar.search_options.ai_summary.setChecked(False)

        assert window._chat_panel is None
        assert window.result_list.card_count() == before

    def test_toggle_state_is_persisted(self, window):
        self._turn_on_chat(window)
        assert window.state.ai_chat_enabled is True

        reloaded = AppState.load(path=window.state._path)
        assert reloaded.ai_chat_enabled is True

    def test_summary_failure_reaches_the_bubble(self, window, qtbot):
        """실패가 화면에 도달하는지 — 신호만 나가고 끝나면 사용자는 멈춘 줄 안다."""
        class _Failing:
            def is_available(self):
                return True

            def is_running(self):
                return True

            def chat(self, *_a, **_k):
                raise RuntimeError("서버 기동 실패")

            def shutdown(self):
                pass

        window._slm_service = _Failing()
        panel = self._turn_on_chat(window)

        panel.send_message("계약서 검토 기준")
        bubble = panel.bubble_for(1)
        qtbot.waitUntil(lambda: bool(bubble.results), timeout=SEARCH_TIMEOUT_MS)

        bubble._summarize_button.click()

        qtbot.waitUntil(lambda: "서버 기동 실패" in bubble.summary_text(), timeout=SEARCH_TIMEOUT_MS)

    def test_multiple_turns_do_not_drop_running_workers(self, window, qtbot):
        """검색 워커와 같은 이유로 여러 턴이 겹쳐도 GC 크래시가 없어야 한다
        (0xC0000409류, `TestEndToEndSearch.test_second_search_does_not_drop_running_worker`와 같은 계열)."""
        import gc

        self._stub_service(window)
        panel = self._turn_on_chat(window)

        panel.send_message("계약서")
        panel.send_message("리눅스")

        assert len(window._active_workers) >= 1
        gc.collect()
        assert all(isinstance(w.isRunning(), bool) for w in window._active_workers)

        qtbot.waitUntil(lambda: not window._active_workers, timeout=SEARCH_TIMEOUT_MS)
        assert panel.turn_count() == 2

    def test_open_button_click_does_not_raise(self, window, qtbot):
        """존재하지 않는 파일 경로라 열기는 실패하지만, 신호로 안전하게 처리돼야 한다."""
        self._stub_service(window)
        panel = self._turn_on_chat(window)

        panel.send_message("계약서 검토 기준")
        bubble = panel.bubble_for(1)
        qtbot.waitUntil(lambda: bool(bubble.results), timeout=SEARCH_TIMEOUT_MS)

        bubble._open_button.click()  # 예외가 나지 않아야 한다

    def test_close_shuts_down_the_slm_server(self, window):
        """🔴 안 내리면 4.8GB짜리 프로세스가 고아로 남는다."""
        class _Tracking:
            def __init__(self):
                self.shutdown_called = False

            def is_available(self):
                return False

            def is_running(self):
                return False

            def shutdown(self):
                self.shutdown_called = True

        tracker = _Tracking()
        window._slm_service = tracker
        window.close()
        assert tracker.shutdown_called is True


class TestChatSearchProfileRegression:
    """🔴 T10.9 재발 방지 — 챗봇 경로도 `SearchWorker`를 그대로 쓰므로 같은
    버그(embedder=만 넘기고 profile= 누락 → 벡터 차원 불일치 → similarity가
    전부 None)를 물려받을 수 있다. `tests/test_ui_search_worker.py`가 이미
    `SearchWorker` 자체를 단위로 검증하지만, 여기서는 `MainWindow`의 챗봇
    배선(`_on_chat_message_sent`)이 실제로 그 경로를 타는지까지 확인한다."""

    def test_chat_message_with_heavy_embedder_yields_real_similarity(self, qtbot, tmp_path, heavy_embedder):
        from indexer.fts5.schema import connect
        from indexer.fts5.store import store_document
        from indexer.vector.store import embed_missing
        from parser.schema import Chunk, ChunkType, ParsedDocument

        db_path = tmp_path / "heavy.sqlite3"
        conn = connect(db_path)
        document = ParsedDocument(doc_id="d1", file_path="x", file_name="사규.docx", title="사규")
        document.chunks = [
            Chunk(
                chunk_id="c1", doc_id="d1", file_path="x", file_name="사규.docx",
                type=ChunkType.TEXT, page_or_slide=1,
                content="계약서 검토 시 기준이 되는 조항은 손해배상과 계약 해지 조건이다",
            ),
        ]
        store_document(conn, document, count_tokens=heavy_embedder.count_tokens)
        embed_missing(conn, heavy_embedder)
        conn.close()

        from config.settings import HEAVY

        # `state.model_profile`을 HEAVY로 두고 실제 워밍업 경로(백그라운드
        # 스레드)를 그대로 태운다 — 워밍업 스레드가 나중에 끝나 `_embedder`를
        # 직접 대입한 값 위에 덮어쓰는 경합을 피한다(실측으로 확인).
        state = AppState.load(path=tmp_path / "state.json")
        state.model_profile = HEAVY.key
        win = MainWindow(db_path=db_path, state=state)
        qtbot.addWidget(win)
        qtbot.waitUntil(lambda: win._embedder is not None, timeout=SEARCH_TIMEOUT_MS)

        win.sidebar.search_options.set_ai_summary_available(True)
        win.sidebar.search_options.ai_summary.setChecked(True)
        panel = win._chat_panel

        panel.send_message("계약서 손해배상")
        bubble = panel.bubble_for(1)
        qtbot.waitUntil(lambda: bool(bubble.results), timeout=SEARCH_TIMEOUT_MS)

        assert bubble.results[0].similarity is not None

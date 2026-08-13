"""레이아웃 셸 조립 (T4.1, Phase 7.7 전면 재구성) — 사이드바 / 결과 헤더 /
결과 리스트 / 공용 입력창 / 상태바.

목업(`rag_ui_concept_*.html`)에 맞춰 상단 고정 검색바를 없애고 하단 공용
입력창(`InputBar`) 하나로 검색·챗봇 두 모드를 함께 받는다. 이 파일이
사이드바 옵션·입력·검색 워커·모델 관리·폴더 관리를 전부 이어 붙이는 조립부다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from config.settings import get_profile
from indexer.fts5.schema import connect
from indexer.incremental.watcher import FolderWatcher
from indexer.pipeline import IndexingThread, IndexReport
from parser.utils.libreoffice import INSTALL_HINT, is_missing_libreoffice_error
from slm.service import SlmService
from ui.search_worker import SearchWorker
from ui.state import DB_PATH, AppState
from ui.summary_worker import SummaryWorker
from ui.thumbnail_cache import evict_thumbnails
from ui.widgets.chat_panel import ChatPanel
from ui.widgets.folder_dialog import FolderDialog
from ui.widgets.indexing_progress_dialog import IndexingProgressDialog
from ui.widgets.model_manager_dialog import ModelManagerDialog
from ui.widgets.result_header import ResultHeader
from ui.widgets.result_list import ResultList
from ui.widgets.search_bar import InputBar
from ui.widgets.sidebar import Sidebar
from ui.widgets.status_bar import StatusBar


# 창을 닫을 때 실행 중인 스레드를 기다리는 한계. 무한정 기다리면 창이 안 닫히고,
# 안 기다리면 실행 중인 QThread가 파괴되며 크래시한다.
_THREAD_SHUTDOWN_WAIT_MS = 5000

# 요약은 중앙 18.3초(채택 모델 실측)라 검색과 같은 5초로는 못 기다린다.
_SUMMARY_SHUTDOWN_WAIT_MS = 20000


class _IndexingBridge(QObject):
    """백그라운드 인덱싱 스레드(`threading.Thread`)의 콜백을 Qt 신호로 옮긴다.

    Qt 위젯은 자신을 만든(메인) 스레드에서만 안전하게 건드릴 수 있다. 신호를
    메인 스레드에서 만든 QObject에 실어 보내면, 실제 발신 스레드가 무엇이든
    연결된 슬롯은 수신자(main thread)의 이벤트 루프에서 실행된다 — 실측으로
    확인함(PLAN §4-B ②).
    """

    progress = Signal(int, int, str)  # done, total, 현재 처리 중인 파일 경로
    done = Signal(object)  # IndexReport


class _WatchBridge(QObject):
    """`FolderWatcher`의 콜백(watchdog 자체 스레드)을 Qt 신호로 옮긴다.

    `_IndexingBridge`와 같은 이유·같은 방식이다 — 큐드 커넥션이 발신 스레드와
    무관하게 수신자(메인 스레드)의 이벤트 루프에서 슬롯을 실행해준다.
    """

    changed = Signal()


class MainWindow(QMainWindow):
    def __init__(
        self,
        parent=None,
        db_path: Path | None = None,
        state: AppState | None = None,
    ) -> None:
        """`db_path`/`state`는 테스트에서 실제 `data/` 폴더 대신 임시 경로를
        주입하기 위한 것이다 — 생략하면 실제 배포 경로(`ui.state.DB_PATH`,
        `AppState.load()`)를 그대로 쓴다."""
        super().__init__(parent)
        self.db_path = db_path if db_path is not None else DB_PATH
        self.state = state if state is not None else AppState.load()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._request_seq = 0
        # 실행 중인 워커를 **전부** 붙들고 있어야 한다. 한 자리에만 두면 다음
        # 검색이 그 참조를 덮어쓰는 순간, 아직 돌고 있는 QThread가 파이썬 GC에
        # 수거되면서 앱이 통째로 죽는다(0xC0000409 실측). 결과를 버리는 것은
        # `request_id` 비교가 이미 해주므로, 여기서는 살려두기만 하면 된다.
        self._active_workers: set[SearchWorker] = set()
        self._embedder = None  # 백그라운드 워밍업 완료 전까지 None
        self._last_query = ""
        self._last_results: list = []
        self._indexing_thread: IndexingThread | None = None
        self._indexing_progress_dialog: IndexingProgressDialog | None = None
        self._folder_watcher: FolderWatcher | None = None  # T8.5, 기본 OFF

        # AI 챗봇 (Phase 7.6, 옛 "AI 요약" 기능을 대체). 서버는 첫 요청 때
        # 올라오고 유휴 5분이면 스스로 내려간다 — 여기서 만드는 것은 관리
        # 객체일 뿐 프로세스가 아니다.
        self._slm_service = SlmService(self.state.slm_profile)
        # 검색 워커와 같은 이유로 **실행 중인 요약 워커를 전부 붙들어야 한다** —
        # 참조를 잃으면 실행 중인 QThread가 GC되며 앱이 통째로 죽는다.
        self._active_summary_workers: set[SummaryWorker] = set()
        # 챗봇 모드가 켜져 있을 때만 값이 있다 — 결과 영역을 누가 갖고 있는지
        # (카드 목록 vs 이 패널) 판단하는 기준이다.
        self._chat_panel: ChatPanel | None = None
        # summarize_requested 신호가 (request_id, results)만 실어 보내 질문
        # 원문이 없다 — message_sent 때 미리 적어 두고 SummaryWorker에 넘길 때 쓴다.
        self._chat_questions: dict[int, str] = {}

        self._build_ui()
        self._wire_signals()
        self._refresh_format_filter_options()
        self._refresh_status_bar()
        self._refresh_ai_chat_availability()
        self.sidebar.set_recent_searches(self.state.recent_searches)
        self._start_embedder_warmup()
        self._sync_folder_watcher()  # T8.5: 이전 세션에서 켜뒀으면 자동 재개

    # --- UI 구성 --------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("CentralWidget")
        root = _hbox(central)

        # PC 성능 콤보가 저장된 프로파일로 바로 시작하도록 생성 시점에
        # 넘긴다 — 기본값으로 만든 뒤 따로 맞추면 화면은 "경량"인데 실제로는
        # 권장으로 검색되는 어긋난 상태가 됐었다(실사용 중 발견).
        self.sidebar = Sidebar(initial_profile=self.state.model_profile)
        root.addWidget(self.sidebar)

        main_area = QWidget()
        main_area.setObjectName("MainArea")
        main_layout = QVBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 검색 결과 모드에서만 보인다(DESIGN §5.8: 챗봇 모드는 헤더 없이
        # 대화만 흐른다) — 기본값은 숨김이다.
        self.result_header = ResultHeader()
        self.result_header.setVisible(False)
        main_layout.addWidget(self.result_header)

        self.result_list = ResultList()
        main_layout.addWidget(self.result_list, stretch=1)

        self.input_bar = InputBar()
        main_layout.addWidget(self.input_bar)

        self.status_bar_widget = StatusBar()
        main_layout.addWidget(self.status_bar_widget)

        root.addWidget(main_area, stretch=1)
        self.setCentralWidget(central)

    def _wire_signals(self) -> None:
        self.input_bar.submitted.connect(self._on_input_submitted)
        self.result_header.close_requested.connect(self._clear_all)

        self.sidebar.format_filter.selection_changed.connect(self._on_filters_changed)
        self.sidebar.search_options.case_sensitive_changed.connect(self._on_filters_changed)
        self.sidebar.search_options.exact_word_changed.connect(self._on_filters_changed)
        self.sidebar.search_options.ai_summary_changed.connect(self._on_ai_chat_toggled)

        self.sidebar.performance_combo.profile_activated.connect(self._on_profile_activated)
        self.sidebar.performance_combo.model_manager_requested.connect(self._open_model_manager)
        self.sidebar.model_manager_requested.connect(self._open_model_manager)
        self.sidebar.folder_requested.connect(self._open_folder_dialog)
        self.sidebar.recent_search_selected.connect(self.input_bar.submit_text)

        self.result_list.open_failed.connect(self._on_open_failed)

    # --- 입력 라우팅 (Phase 7.7) --------------------------------------------

    def _on_input_submitted(self, text: str) -> None:
        """공용 입력창(`InputBar`)의 유일한 진입점.

        현재 모드를 보고 챗봇 메시지 전송/검색 실행으로 분기한다 — 입력창
        자체는 어느 모드인지 모른다(Phase 7.7 설계, PLAN 참고)."""
        if text.strip():
            self.state.add_recent_search(text)
            self.state.save()  # 인자 없이 — T10.5가 기억해둔 경로로 저장된다
            self.sidebar.set_recent_searches(self.state.recent_searches)

        if self._chat_panel is not None:
            self._chat_panel.send_message(text)
            return

        self._on_search_requested(text)
        self._sync_result_header()

    def _clear_all(self) -> None:
        """헤더 ✕ — 검색어·결과를 모두 지우고 초기 안내로 되돌린다.

        `_last_query`까지 반드시 비워야 한다. 안 비우면 ① 옵션 토글이
        `_on_filters_changed`에서 지운 검색을 되살리고, ② 이후 챗봇 모드를
        켤 때 `_activate_chat_mode()`가 지운 질문을 유령 첫 메시지로
        자동 전송한다.
        """
        self._last_query = ""
        self._last_results = []
        self.input_bar.clear()
        self.result_list.show_initial()
        self._sync_result_header()

    def _sync_result_header(self) -> None:
        """검색 결과 모드 + 검색어가 있을 때만 헤더를 보여준다."""
        if self._chat_panel is not None or not self._last_query.strip():
            self.result_header.setVisible(False)
            return
        self.result_header.set_query(self._last_query)
        self.result_header.setVisible(True)

    # --- 검색 --------------------------------------------------

    def _on_filters_changed(self, *_args) -> None:
        """옵션이 바뀌면 마지막 질의를 그대로 다시 돌린다 — 빈 질의면 아무 것도 안 한다."""
        if self._last_query:
            self._run_search(self._last_query)

    def _on_search_requested(self, query: str) -> None:
        self._last_query = query
        if not query.strip():
            self.result_list.show_initial()
            return
        self._run_search(query)

    def _run_search(self, query: str) -> None:
        conn = connect(self.db_path)
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        conn.close()
        if doc_count == 0:
            self.result_list.show_no_index()
            return

        self.result_list.show_searching()

        self._request_seq += 1
        request_id = self._request_seq

        case_sensitive = self.sidebar.search_options.is_case_sensitive()
        exact_word = self.sidebar.search_options.is_exact_word()
        extensions = self.sidebar.format_filter.selected_extensions()

        worker = SearchWorker(
            self.db_path,
            query,
            request_id,
            embedder=self._embedder,
            case_sensitive=case_sensitive,
            exact_word=exact_word,
            extensions=extensions,
        )
        worker.succeeded.connect(self._on_search_succeeded)
        worker.failed.connect(self._on_search_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: self._active_workers.discard(w))
        self._active_workers.add(worker)
        worker.start()

    def _on_search_succeeded(self, request_id: int, results: list) -> None:
        if request_id != self._request_seq:
            return  # 더 최신 질의가 이미 나갔다 — 늦게 도착한 결과는 버린다

        self._last_results = results

        if not results:
            hint = self._empty_result_hint()
            self.result_list.show_empty(hint)
            return

        case_sensitive = self.sidebar.search_options.is_case_sensitive()
        exact_word = self.sidebar.search_options.is_exact_word()
        self.result_list.show_results(results, self._last_query, case_sensitive, exact_word)

    def _on_search_failed(self, request_id: int, message: str) -> None:
        if request_id != self._request_seq:
            return
        self.result_list.show_error(message)

    # --- AI 챗봇 (Phase 7.6) ----------------------------------------------
    #
    # 2단 응답 구조: ① 메시지를 보내면 SearchWorker(무수정)로 즉시 발췌를
    # 보여준다(LLM 미사용, 검색 지연 7~14ms). ② 그 턴의 "AI 요약 보기"를
    # 누르면 그때 받은 결과를 그대로 SummaryWorker(무수정)에 넘긴다 —
    # 검색을 다시 하지 않는다. 카드 목록 경로(_run_search 등)는 무수정이다.

    def _refresh_ai_chat_availability(self) -> None:
        """모델·실행 바이너리가 모두 있어야 토글을 연다."""
        available = self._slm_service.is_available()
        self.sidebar.search_options.set_ai_summary_available(available)
        if available:
            self.sidebar.search_options.set_ai_summary(self.state.ai_chat_enabled)

    def _on_ai_chat_toggled(self, enabled: bool) -> None:
        self.state.ai_chat_enabled = enabled
        self.state.save()

        if enabled:
            self._activate_chat_mode()
        else:
            self._deactivate_chat_mode()

    def _activate_chat_mode(self) -> None:
        panel = ChatPanel()
        panel.message_sent.connect(self._on_chat_message_sent)
        panel.summarize_requested.connect(self._on_chat_summarize_requested)
        panel.open_failed.connect(self._on_open_failed)
        self._chat_panel = panel
        self.result_list.show_chat_mode(panel)
        self._sync_result_header()  # 챗봇 모드는 헤더가 없다(DESIGN §5.8)

        # 검색어가 이미 있으면(직전에 검색 결과 모드에서 입력해 둔 상태)
        # 그대로 첫 메시지로 자동 전송한다 — 패널이 빈 채로 뜨면 토글이
        # 안 먹는 것처럼 보인다.
        if self._last_query:
            panel.send_message(self._last_query)

    def _deactivate_chat_mode(self) -> None:
        self._chat_panel = None
        self._render_current_results()
        self._sync_result_header()

    def _render_current_results(self) -> None:
        """챗봇 모드를 벗어날 때 결과 영역을 카드 목록으로 되돌린다."""
        if not self._last_query:
            self.result_list.show_initial()
            return
        if not self._last_results:
            self.result_list.show_empty(self._empty_result_hint())
            return
        case_sensitive = self.sidebar.search_options.is_case_sensitive()
        exact_word = self.sidebar.search_options.is_exact_word()
        self.result_list.show_results(self._last_results, self._last_query, case_sensitive, exact_word)

    def _on_chat_message_sent(self, request_id: int, question: str) -> None:
        """① 즉시 발췌 — 기존 `SearchWorker`를 무수정으로 그대로 쓴다.

        `SearchWorker._search()`가 이미 `embedder.profile`을 함께 넘긴다
        (T10.9 재발 방지) — 여기서 따로 챙길 게 없다.
        """
        self._chat_questions[request_id] = question

        case_sensitive = self.sidebar.search_options.is_case_sensitive()
        exact_word = self.sidebar.search_options.is_exact_word()
        extensions = self.sidebar.format_filter.selected_extensions()

        worker = SearchWorker(
            self.db_path,
            question,
            request_id,
            embedder=self._embedder,
            case_sensitive=case_sensitive,
            exact_word=exact_word,
            extensions=extensions,
        )
        worker.succeeded.connect(self._on_chat_search_succeeded)
        worker.failed.connect(self._on_chat_search_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: self._active_workers.discard(w))
        self._active_workers.add(worker)
        worker.start()

    def _on_chat_search_succeeded(self, request_id: int, results: list) -> None:
        # 턴마다 독립이라 "더 최신 질의가 왔으니 버린다" 판단이 필요 없다 —
        # ChatPanel이 request_id로 그 턴의 말풍선을 찾아 그리기만 한다.
        if self._chat_panel is not None:
            self._chat_panel.show_excerpt(request_id, results)

    def _on_chat_search_failed(self, request_id: int, message: str) -> None:
        if self._chat_panel is not None:
            self._chat_panel.show_search_error(request_id, message)

    def _on_chat_summarize_requested(self, request_id: int, results: list) -> None:
        """② AI 요약 — 그 턴이 ①에서 이미 받은 `results`를 그대로 넘긴다
        (검색을 다시 하지 않는다). `SummaryWorker`도 무수정."""
        if self._chat_panel is not None:
            self._chat_panel.show_summary_generating(request_id)

        question = self._chat_questions.get(request_id, "")
        worker = SummaryWorker(question, results, self._slm_service, request_id)
        worker.started_loading.connect(self._on_chat_summary_loading)
        worker.succeeded.connect(self._on_chat_summary_succeeded)
        worker.failed.connect(self._on_chat_summary_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda w=worker: self._active_summary_workers.discard(w))
        self._active_summary_workers.add(worker)
        worker.start()

    def _on_chat_summary_loading(self, request_id: int) -> None:
        if self._chat_panel is not None:
            self._chat_panel.show_summary_starting(request_id)

    def _on_chat_summary_succeeded(self, request_id: int, summary) -> None:
        if self._chat_panel is not None:
            self._chat_panel.show_summary(request_id, summary)

    def _on_chat_summary_failed(self, request_id: int, message: str) -> None:
        if self._chat_panel is not None:
            self._chat_panel.show_summary_error(request_id, message)

    def _on_open_failed(self, message: str) -> None:
        """"원문 열기" 실패를 사용자에게 보여준다.

        카드는 실패 시 `open_failed`를 emit하지만 지금까지 받는 곳이 없어
        아무 반응도 없는 것처럼 보였다(신호는 나가는데 아무도 안 듣는 상태) —
        실사용에서 실제로 겪은 버그다. 상태바의 인덱싱 안내와 같은 자리를
        재사용한다: 다음 인덱싱이 끝나면 자연히 새 상태로 교체된다.
        """
        self.status_bar_widget.set_warning(message)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        """실행 중인 스레드를 정리하고 닫는다.

        검색 도중 창을 닫으면 실행 중인 QThread가 파괴되면서 앱이 죽는다 —
        `_active_workers`가 막아주는 것과 같은 종류의 크래시다. 인덱싱은 길 수
        있어 `stop_event`로 중단을 먼저 요청한다.
        """
        if self._folder_watcher is not None:
            self._folder_watcher.stop()  # T8.5: watchdog Observer도 자체 스레드다

        if self._indexing_thread is not None and self._indexing_thread.is_alive():
            # IndexingThread는 QThread가 아니라 threading.Thread다 —
            # isRunning()/wait(ms)는 QThread API라 여기선 존재하지 않는다
            # (AttributeError, 실측 확인). is_alive()/join(초)를 쓴다.
            self._indexing_thread.stop_event.set()
            self._indexing_thread.join(_THREAD_SHUTDOWN_WAIT_MS / 1000)

        for worker in list(self._active_workers):
            worker.wait(_THREAD_SHUTDOWN_WAIT_MS)

        # 요약 워커는 추론이 끝날 때까지 최대 18초를 더 기다릴 수 있어 검색보다
        # 여유가 필요하다. 다만 무한정 기다리면 창이 안 닫히므로 상한을 둔다.
        for worker in list(self._active_summary_workers):
            worker.wait(_SUMMARY_SHUTDOWN_WAIT_MS)

        # 🔴 반드시 마지막에. 안 부르면 llama-server(채택 모델 기준 4.8GB)가
        # 고아 프로세스로 남아 앱을 닫아도 메모리가 돌아오지 않는다.
        self._slm_service.shutdown()

        super().closeEvent(event)

    def _empty_result_hint(self) -> str | None:
        """DESIGN §7: 형식 필터·옵션이 원인일 수 있으니 완화를 제안한다."""
        extensions = self.sidebar.format_filter.selected_extensions()
        if extensions:
            names = ", ".join(ext.lstrip(".") for ext in sorted(extensions))
            return f"{names}만 검색 중입니다. 전체로 넓혀보세요."
        if self.sidebar.search_options.is_exact_word():
            return "'일치되는 단어' 옵션을 꺼보세요."
        return None

    # --- 임베더 --------------------------------------------------

    def _start_embedder_warmup(self) -> None:
        """ONNX 세션 최초 로딩(약 651ms, Phase 3 실측)을 백그라운드에서 미리 끝낸다.

        완료 전에 검색이 들어오면 `SearchWorker`가 `embedder=None`으로 받아
        내부에서 자체적으로 만든다(느리지만 정상 동작) — 워밍업 실패가
        검색 자체를 막지는 않는다.
        """

        def warmup() -> None:
            try:
                from indexer.vector.embedder import Embedder

                embedder = Embedder(get_profile(self.state.model_profile))
                embedder.count_tokens("")  # 세션 생성을 강제로 트리거
                self._embedder = embedder
            except Exception:
                pass  # 모델 미설치 등 — 조용히 포기, 검색은 키워드 결과로 대체됨

        threading.Thread(target=warmup, daemon=True).start()

    def _on_profile_activated(self, key: str) -> None:
        if key == self.state.model_profile:
            return
        self.state.model_profile = key
        self.state.save()
        self._embedder = None
        self._start_embedder_warmup()

    # --- 모델 관리 --------------------------------------------------

    def _open_model_manager(self, focus_profile: str) -> None:
        dialog = ModelManagerDialog(focus_profile=focus_profile, parent=self)
        dialog.exec()
        self.sidebar.performance_combo.refresh()
        # sLM을 새로 넣었거나 지웠을 수 있다 — AI 챗봇 토글을 다시 판정한다.
        self._refresh_ai_chat_availability()

    # --- 폴더 관리 / 인덱싱 --------------------------------------------------

    def _open_folder_dialog(self) -> None:
        dialog = FolderDialog(
            current_folder=self.state.target_folder,
            current_watch_enabled=self.state.folder_watch_enabled,
            parent=self,
        )
        dialog.reindex_requested.connect(self._start_reindex)
        dialog.watch_toggled.connect(self._on_folder_watch_toggled)
        dialog.exec()

    def _start_reindex(self, folder: str, silent: bool = False) -> None:
        """`silent=True`면 진행률 팝업 없이 상태바로만 조용히 진행한다 (T8.5,
        watchdog가 트리거한 백그라운드 재인덱싱용 — 사용자가 다른 작업 중에
        팝업이 튀어나오면 안 된다)."""
        # 두 인덱싱 스레드가 같은 DB에 동시에 쓰면 위험하다 — 이미 도는 중이면
        # 새로 시작하지 않고 기존 팝업만 앞으로 가져온다(무음 모드면 팝업이
        # 없을 수 있으니 그때는 아무것도 안 한다).
        if self._indexing_thread is not None and self._indexing_thread.is_alive():
            if self._indexing_progress_dialog is not None:
                self._indexing_progress_dialog.raise_()
                self._indexing_progress_dialog.activateWindow()
            return

        folder_changed = self.state.target_folder != folder
        self.state.target_folder = folder
        self.state.save()
        if folder_changed:
            # watchdog가 감지한 재인덱싱은 대상 폴더가 그대로이므로 매번
            # Observer를 멈췄다 다시 켤 필요가 없다 — 폴더 선택 다이얼로그를
            # 거쳐 실제로 대상이 바뀐 경우에만 감시를 다시 건다.
            self._sync_folder_watcher()

        # 이전 "원문 열기" 실패 안내가 남아 있으면 새 인덱싱 진행률과 한 줄에
        # 겹쳐 보인다(실측 확인) — 새 인덱싱을 시작하는 시점에 지운다.
        self.status_bar_widget.set_warning(None)

        bridge = _IndexingBridge(self)
        bridge.progress.connect(self._on_indexing_progress)
        bridge.done.connect(self._on_indexing_done)
        self._indexing_bridge = bridge  # GC 방지를 위해 보관

        self._indexing_thread = IndexingThread(
            self.db_path,
            folder,
            on_progress=lambda done, total, path: bridge.progress.emit(done, total, str(path)),
            on_done=lambda report: bridge.done.emit(report),
        )

        if not silent:
            dialog = IndexingProgressDialog(parent=self)
            dialog.cancel_requested.connect(self._cancel_indexing)
            dialog.show()
            self._indexing_progress_dialog = dialog

        self._indexing_thread.start()

    # --- 실시간 폴더 감시 (T8.5, 기본 OFF) --------------------------------

    def _on_folder_watch_toggled(self, enabled: bool) -> None:
        self.state.folder_watch_enabled = enabled
        self.state.save()
        self._sync_folder_watcher()

    def _sync_folder_watcher(self) -> None:
        """설정·대상 폴더에 맞춰 감시를 다시 켠다. 폴더가 바뀌었을 수도,
        토글이 바뀌었을 수도 있어 일단 멈추고 다시 판단한다."""
        if self._folder_watcher is not None:
            self._folder_watcher.stop()
            self._folder_watcher = None

        if not self.state.folder_watch_enabled or not self.state.target_folder:
            return

        bridge = _WatchBridge(self)
        bridge.changed.connect(self._on_folder_changed)
        self._watch_bridge = bridge  # GC 방지를 위해 보관

        watcher = FolderWatcher(self.state.target_folder, on_change=bridge.changed.emit)
        try:
            watcher.start()
        except Exception as exc:
            # 폴더가 없어졌다 등 — 감시 실패가 앱을 죽이면 안 된다(T10.2와 같은 결).
            self.status_bar_widget.set_warning(f"실시간 감시를 시작하지 못했습니다: {exc}")
            return

        self._folder_watcher = watcher

    def _on_folder_changed(self) -> None:
        if self.state.target_folder:
            self._start_reindex(self.state.target_folder, silent=True)

    def _cancel_indexing(self) -> None:
        if self._indexing_thread is not None:
            self._indexing_thread.stop_event.set()

    def _on_indexing_progress(self, done: int, total: int, current_path: str) -> None:
        self.status_bar_widget.set_indexing_progress(done, total)
        if self._indexing_progress_dialog is not None:
            self._indexing_progress_dialog.set_progress(done, total, current_path)

    def _on_indexing_done(self, report: IndexReport) -> None:
        self._refresh_format_filter_options()
        self._refresh_status_bar()
        self.status_bar_widget.set_warning(self._libreoffice_warning(report))
        if report.stale_image_chunk_ids:
            # 재파싱·정리로 사라진 이미지 청크의 옛 썸네일을 지운다 (Phase 8, T8.4).
            evict_thumbnails(report.stale_image_chunk_ids)
        if self._indexing_progress_dialog is not None:
            self._indexing_progress_dialog.close()
            self._indexing_progress_dialog = None
        if self._last_query:
            self._run_search(self._last_query)

    def _libreoffice_warning(self, report: IndexReport) -> str | None:
        """T10.2: LibreOffice가 없어 구버전 문서가 조용히 빠졌다면 안내한다."""
        missing = [path for path, message in report.failures
                   if is_missing_libreoffice_error(message)]
        if not missing:
            return None

        names = ", ".join(path.name for path in missing[:2])
        if len(missing) > 2:
            names += f" 외 {len(missing) - 2}건"
        return f"구버전 문서를 변환하지 못했습니다({names}). {INSTALL_HINT}"

    # --- 상태바 --------------------------------------------------

    def _refresh_status_bar(self) -> None:
        conn = connect(self.db_path)
        doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        row = conn.execute("SELECT MAX(indexed_at) AS ts FROM documents").fetchone()
        conn.close()

        # store.py는 UTC 오프셋을 포함한 isoformat()으로 저장한다. fromisoformat()이
        # 오프셋을 그대로 파싱해 tz-aware datetime을 돌려주므로, format_relative_time이
        # 같은 타임존 기준으로 "지금"을 구해 비교한다 — 로컬 타임존과 섞이지 않는다.
        last_indexed_at = None
        if row is not None and row["ts"]:
            try:
                last_indexed_at = datetime.fromisoformat(row["ts"])
            except ValueError:
                last_indexed_at = None

        self.status_bar_widget.set_idle(doc_count, last_indexed_at)

    def _refresh_format_filter_options(self) -> None:
        conn = connect(self.db_path)
        rows = conn.execute("SELECT DISTINCT file_name FROM documents").fetchall()
        conn.close()

        extensions = sorted({Path(r["file_name"]).suffix.lower() for r in rows if Path(r["file_name"]).suffix})
        self.sidebar.format_filter.set_available_formats(extensions)


def _hbox(widget: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return layout

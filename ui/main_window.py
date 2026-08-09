"""레이아웃 셸 조립 (T4.1) — 검색바 / 사이드바 / 결과 리스트 / 상태바.

DESIGN §2.1 구조를 그대로 옮긴다. 이 파일이 사이드바 옵션·검색바·검색
워커·모델 관리·폴더 관리를 전부 이어 붙이는 조립부다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from config.settings import get_profile
from indexer.fts5.schema import connect
from indexer.pipeline import IndexingThread, IndexReport
from parser.utils.libreoffice import INSTALL_HINT, is_missing_libreoffice_error
from ui.search_worker import SearchWorker
from ui.state import DB_PATH, AppState
from ui.widgets.folder_dialog import FolderDialog
from ui.widgets.model_manager_dialog import ModelManagerDialog
from ui.widgets.result_list import ResultList
from ui.widgets.search_bar import SearchBar
from ui.widgets.sidebar import Sidebar
from ui.widgets.status_bar import StatusBar


# 창을 닫을 때 실행 중인 스레드를 기다리는 한계. 무한정 기다리면 창이 안 닫히고,
# 안 기다리면 실행 중인 QThread가 파괴되며 크래시한다.
_THREAD_SHUTDOWN_WAIT_MS = 5000


class _IndexingBridge(QObject):
    """백그라운드 인덱싱 스레드(`threading.Thread`)의 콜백을 Qt 신호로 옮긴다.

    Qt 위젯은 자신을 만든(메인) 스레드에서만 안전하게 건드릴 수 있다. 신호를
    메인 스레드에서 만든 QObject에 실어 보내면, 실제 발신 스레드가 무엇이든
    연결된 슬롯은 수신자(main thread)의 이벤트 루프에서 실행된다 — 실측으로
    확인함(PLAN §4-B ②).
    """

    progress = Signal(int, int)
    done = Signal(object)  # IndexReport


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
        self._indexing_thread: IndexingThread | None = None

        self._build_ui()
        self._wire_signals()
        self._refresh_format_filter_options()
        self._refresh_status_bar()
        self._start_embedder_warmup()

    # --- UI 구성 --------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("CentralWidget")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.search_bar = SearchBar()
        root.addWidget(self.search_bar)

        body = QWidget()
        body_layout = _hbox(body)
        self.sidebar = Sidebar()
        body_layout.addWidget(self.sidebar)
        self.result_list = ResultList()
        body_layout.addWidget(self.result_list, stretch=1)
        root.addWidget(body, stretch=1)

        self.status_bar_widget = StatusBar()
        root.addWidget(self.status_bar_widget)

        self.setCentralWidget(central)

    def _wire_signals(self) -> None:
        self.search_bar.search_requested.connect(self._on_search_requested)

        self.sidebar.format_filter.selection_changed.connect(self._on_filters_changed)
        self.sidebar.search_options.case_sensitive_changed.connect(self._on_filters_changed)
        self.sidebar.search_options.exact_word_changed.connect(self._on_filters_changed)

        self.sidebar.performance_combo.profile_activated.connect(self._on_profile_activated)
        self.sidebar.performance_combo.model_manager_requested.connect(self._open_model_manager)

        self.status_bar_widget.folder_button.clicked.connect(self._open_folder_dialog)
        self.result_list.open_failed.connect(self._on_open_failed)

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
        if self._indexing_thread is not None and self._indexing_thread.isRunning():
            self._indexing_thread.stop_event.set()
            self._indexing_thread.wait(_THREAD_SHUTDOWN_WAIT_MS)

        for worker in list(self._active_workers):
            worker.wait(_THREAD_SHUTDOWN_WAIT_MS)

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

    # --- 폴더 관리 / 인덱싱 --------------------------------------------------

    def _open_folder_dialog(self) -> None:
        dialog = FolderDialog(current_folder=self.state.target_folder, parent=self)
        dialog.reindex_requested.connect(self._start_reindex)
        dialog.exec()

    def _start_reindex(self, folder: str) -> None:
        self.state.target_folder = folder
        self.state.save()

        bridge = _IndexingBridge(self)
        bridge.progress.connect(self._on_indexing_progress)
        bridge.done.connect(self._on_indexing_done)
        self._indexing_bridge = bridge  # GC 방지를 위해 보관

        self._indexing_thread = IndexingThread(
            self.db_path,
            folder,
            on_progress=lambda done, total, _path: bridge.progress.emit(done, total),
            on_done=lambda report: bridge.done.emit(report),
        )
        self._indexing_thread.start()

    def _on_indexing_progress(self, done: int, total: int) -> None:
        self.status_bar_widget.set_indexing_progress(done, total)

    def _on_indexing_done(self, report: IndexReport) -> None:
        self._refresh_format_filter_options()
        self._refresh_status_bar()
        self.status_bar_widget.set_warning(self._libreoffice_warning(report))
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

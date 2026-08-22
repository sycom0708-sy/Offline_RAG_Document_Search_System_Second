"""sLM(GGUF) 다운로드 진행 다이얼로그 — 모델 관리 팝업의 "다운로드 시작" 버튼.

지금까지는 `QMessageBox.information()`으로 링크·명령줄을 안내만 하고
사용자가 직접 브라우저나 터미널에서 받아야 했다. 이 다이얼로그는 그 안내
문구를 실제 "다운로드 시작" 버튼으로 바꾼다 — `slm/download.py`가 이미
갖고 있던 이어받기 지원 다운로더를 백그라운드 스레드에서 돌리고 진행률을
보여준다.

**여전히 자동 다운로드는 아니다** — 사용자가 버튼을 명시적으로 눌러야만
시작된다(TECH 9.1의 "완전 오프라인" 전제는 앱이 조용히 인터넷에 나가지
않는다는 것이지, 사용자가 요청한 다운로드까지 막는 것은 아니다). 인터넷이
없는 PC에서는 여전히 실패하고, 그 경우 다른 PC에서 받아 옮기라는 안내가
보인다.

상태는 idle → progress → (done | failed) 넷이다. 실패해도 팝업을 닫지
않고 그 자리에서 사유를 보여주며 "다시 시도"를 제공한다 — `.part` 파일이
남아 있어 처음부터 다시 받지 않는다.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from config.settings import SlmProfile
from slm.download import SlmDownloadCancelled, SlmDownloadError, download_slm


class _DownloadWorker(QObject):
    """`download_slm()`을 백그라운드 스레드에서 돈다.

    `_VerifyWorker`(model_manager_dialog.py)와 같은 패턴 — GB 단위 I/O를
    UI 스레드에서 하면 창이 얼어붙는다.
    """

    progress = Signal(int, object)  # 받은 바이트, 전체 바이트(모르면 None)
    succeeded = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, profile: SlmProfile, cancel_event: threading.Event) -> None:
        super().__init__()
        self._profile = profile
        self._cancel_event = cancel_event

    def run(self) -> None:
        try:
            download_slm(
                self._profile,
                on_progress=lambda done, total: self.progress.emit(done, total),
                cancel_event=self._cancel_event,
            )
        except SlmDownloadCancelled:
            self.cancelled.emit()
        except SlmDownloadError as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit()


class SlmDownloadDialog(QDialog):
    """sLM 모델 하나를 받는 진행 다이얼로그."""

    # 성공적으로 받았다 — 호출부(모델 관리 다이얼로그)가 목록을 새로고침하도록.
    download_succeeded = Signal()

    def __init__(self, profile: SlmProfile, parent=None) -> None:
        super().__init__(parent)
        self._profile = profile
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None
        self._cancel_event: threading.Event | None = None

        self.setObjectName("SlmDownloadDialog")
        self.setWindowTitle(f"{profile.label} 다운로드")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        info = QLabel(
            f"파일명\t{profile.local_path.name}\n"
            f"용량\t{profile.size_gb:.2f} GB\n"
            f"저장 위치\t{profile.local_path.parent}"
        )
        info.setObjectName("SlmDownloadInfo")
        layout.addWidget(info)

        self._hint_label = QLabel(
            "인터넷이 되는 PC에서만 받을 수 있습니다. 오프라인 PC라면 이 파일을 "
            "다른 PC에서 받아 위 폴더에 직접 옮기세요."
        )
        self._hint_label.setObjectName("SlmDownloadHint")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        self._progress_hint_label = QLabel("닫아도 받던 위치부터 이어받을 수 있습니다.")
        self._progress_hint_label.setObjectName("SlmDownloadHint")
        self._progress_hint_label.setWordWrap(True)
        layout.addWidget(self._progress_hint_label)

        self._error_label = QLabel()
        self._error_label.setObjectName("SlmDownloadError")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        self._success_label = QLabel("다운로드 완료 · 체크섬 확인됨")
        self._success_label.setObjectName("SlmDownloadSuccess")
        layout.addWidget(self._success_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("SlmDownloadProgress")
        self._progress_bar.setRange(0, 1000)
        layout.addWidget(self._progress_bar)

        self._progress_label = QLabel()
        self._progress_label.setObjectName("SlmDownloadProgressLabel")
        layout.addWidget(self._progress_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._primary_btn = QPushButton("다운로드 시작")
        self._primary_btn.setObjectName("PrimaryButton")
        self._primary_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._primary_btn.clicked.connect(self._on_primary_clicked)
        buttons.addWidget(self._primary_btn)

        self._close_btn = QPushButton("닫기")
        self._close_btn.setObjectName("SidebarFooterButton")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self.close)
        buttons.addWidget(self._close_btn)
        layout.addLayout(buttons)

        self._set_state("idle")

    # --- 상태 전환 --------------------------------------------------

    def _set_state(self, state: str) -> None:
        """idle | progress | done | failed — 위젯 표시만 갈아 끼운다."""
        self._state = state
        self._hint_label.setVisible(state == "idle")
        self._progress_bar.setVisible(state == "progress")
        self._progress_label.setVisible(state == "progress")
        self._progress_hint_label.setVisible(state == "progress")
        self._error_label.setVisible(state == "failed")
        self._success_label.setVisible(state == "done")

        if state == "idle":
            self._primary_btn.setText("다운로드 시작")
            self._primary_btn.setEnabled(True)
        elif state == "progress":
            self._primary_btn.setText("다운로드 중…")
            self._primary_btn.setEnabled(False)
        elif state == "failed":
            self._primary_btn.setText("다시 시도")
            self._primary_btn.setEnabled(True)
        elif state == "done":
            self._primary_btn.setText("다운로드 시작")
            self._primary_btn.setEnabled(False)

    # --- 버튼 동작 ---------------------------------------------------

    def _on_primary_clicked(self) -> None:
        if self._state in ("idle", "failed"):
            self._start_download()

    def _start_download(self) -> None:
        self._error_label.setText("")
        self._progress_bar.setValue(0)
        self._progress_label.setText("")

        self._cancel_event = threading.Event()
        thread = QThread(self)
        worker = _DownloadWorker(self._profile, self._cancel_event)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.succeeded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._cleanup_thread)
        # 참조를 붙들어야 한다 — 안 그러면 실행 중 GC로 죽는다(Phase 6 실측 패턴).
        self._thread = thread
        self._worker = worker
        thread.start()
        self._set_state("progress")

    def _on_progress(self, done: int, total: int | None) -> None:
        if total:
            self._progress_bar.setValue(int(done / total * 1000))
            self._progress_label.setText(
                f"{done / 1e9:.2f} / {total / 1e9:.2f} GB ({done / total * 100:.0f}%)"
            )
        else:
            self._progress_bar.setRange(0, 0)  # 전체 크기를 모르면 불확정 진행 표시
            self._progress_label.setText(f"{done / 1e9:.2f} GB")

    def _on_succeeded(self) -> None:
        self._set_state("done")
        self.download_succeeded.emit()

    def _on_failed(self, message: str) -> None:
        self._error_label.setText(message)
        self._set_state("failed")

    def _on_cancelled(self) -> None:
        # 창을 닫으면서 취소한 것이 아니라(그 경우 다이얼로그가 이미 사라진다),
        # 취소 버튼이 따로 있다면 여기로 온다 — 지금은 닫기가 곧 취소라
        # 실제로는 closeEvent 경로만 타지만, 취소 버튼이 추가될 경우를 대비해
        # idle로 되돌린다.
        self._set_state("idle")

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    # --- 생명주기 -----------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        """다운로드 중 닫으면 취소하고 닫는다.

        스레드를 계속 살려두고 다이얼로그만 닫으면(부모-자식 소유권이
        애매해져) `ModelManagerDialog`가 이미 겪은 것과 같은 크래시 위험이
        있다(Phase 6 패턴). 대신 `.part` 파일 기반 이어받기(slm/download.py)에
        기대 — 취소해도 다음에 다시 누르면 받던 위치부터 이어진다. 그래서
        "닫아도 이어받을 수 있다"는 안내가 거짓이 아니다.
        """
        if self._thread is not None and self._thread.isRunning():
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._thread.quit()
            self._thread.wait(5000)
        super().closeEvent(event)

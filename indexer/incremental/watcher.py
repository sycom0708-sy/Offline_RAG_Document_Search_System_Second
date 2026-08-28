"""실시간 폴더 감시 (T8.5, 옵션 — 기본 OFF).

Phase 8 핵심(mtime/해시 스킵)이 이미 재인덱싱 자체를 값싸게 만들어뒀다 —
그래서 여기서는 "파일 하나만 골라 재파싱"하는 새 경로를 만들지 않고,
변경을 감지하면 그냥 폴더 전체 재인덱싱을 다시 트리거하는 것으로 충분하다.
변경 없는 파일은 `needs_reindex()`가 알아서 건너뛴다.

watchdog 콜백은 자체 스레드에서 불린다 — Qt 위젯을 여기서 직접 건드리면
안 되므로, 호출부(`ui/main_window.py`)가 `on_change`를 Qt 신호로 옮겨야 한다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config.settings import DATA_DIR

DEFAULT_DEBOUNCE_SECONDS = 3.0

# 감시 대상 폴더가 앱 자신의 data/를 포함하면(예: 대상 폴더를 잘못 골랐거나
# data/를 포함하는 상위 폴더를 골랐을 때), 재인덱싱이 data/에 쓴 변경을
# 감시가 다시 감지해 무한 재인덱싱하는 루프가 실사용 중 재현됐다
# (2026-08-28) — data/ 안에서 나는 이벤트는 어떤 이유로든 무시한다.
_DATA_DIR_RESOLVED = DATA_DIR.resolve()


def _is_within_data_dir(raw_path: str) -> bool:
    try:
        resolved = Path(raw_path).resolve()
    except OSError:
        return False
    return resolved == _DATA_DIR_RESOLVED or _DATA_DIR_RESOLVED in resolved.parents


class _DebouncedHandler(FileSystemEventHandler):
    """저장 도구가 짧은 시간에 여러 이벤트를 낼 수 있어 묶어서 한 번만 반응한다."""

    def __init__(self, on_change: Callable[[], None], debounce_seconds: float) -> None:
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule(self, event) -> None:
        if event.is_directory:
            return
        if _is_within_data_dir(event.src_path):
            return
        dest_path = getattr(event, "dest_path", None)
        if dest_path and _is_within_data_dir(dest_path):
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._on_change)
            self._timer.daemon = True
            self._timer.start()

    def on_created(self, event) -> None:
        self._schedule(event)

    def on_modified(self, event) -> None:
        self._schedule(event)

    def on_deleted(self, event) -> None:
        self._schedule(event)

    def on_moved(self, event) -> None:
        self._schedule(event)


class FolderWatcher:
    """폴더를 재귀적으로 감시하다가 변경이 있으면(디바운스 후) `on_change`를 부른다."""

    def __init__(
        self,
        folder: str | Path,
        on_change: Callable[[], None],
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> None:
        self._folder = str(folder)
        self._handler = _DebouncedHandler(on_change, debounce_seconds)
        self._observer = Observer()

    def start(self) -> None:
        """감시를 시작한다. 폴더가 없으면 watchdog이 예외를 던진다 — 호출부가 처리한다."""
        self._observer.schedule(self._handler, self._folder, recursive=True)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)

"""스캔 → 파싱 → 저장 오케스트레이션, 백그라운드 스레드 + 진행 콜백 (T2.8)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

from indexer.fts5.store import store_document
from indexer.scanner import scan_folder
from parser import parse_file
from parser.base import ParserError

ProgressCallback = Callable[[int, int, Path], None]
DoneCallback = Callable[[list[tuple[Path, str]]], None]


def index_folder(
    conn: sqlite3.Connection,
    root: str | Path,
    on_progress: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
) -> list[tuple[Path, str]]:
    """폴더를 스캔해 순차적으로 파싱·저장한다.

    파일 하나가 실패해도 인덱싱 전체를 멈추지 않는다 — 실패는 모아서
    반환하고 나머지 파일은 계속 처리한다.

    반환값: (파일 경로, 에러 메시지) 실패 목록.
    """
    files = list(scan_folder(root))
    total = len(files)
    failures: list[tuple[Path, str]] = []

    for done, path in enumerate(files, start=1):
        if stop_event is not None and stop_event.is_set():
            break
        try:
            document = parse_file(path)
            store_document(conn, document)
        except ParserError as exc:
            failures.append((path, str(exc)))
        except Exception as exc:  # 예상 못한 오류도 인덱싱 전체를 죽이지 않는다
            failures.append((path, f"예상치 못한 오류: {exc}"))
        finally:
            if on_progress is not None:
                on_progress(done, total, path)

    return failures


class IndexingThread(threading.Thread):
    """백그라운드 인덱싱 스레드.

    sqlite3 커넥션은 자신을 만든 스레드에서만 안전하게 쓸 수 있어, 커넥션을
    밖에서 주입받지 않고 `run()` 내부(=이 스레드)에서 직접 연다.
    """

    def __init__(
        self,
        db_path: str | Path,
        root: str | Path,
        on_progress: ProgressCallback | None = None,
        on_done: DoneCallback | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._db_path = db_path
        self._root = root
        self._on_progress = on_progress
        self._on_done = on_done
        self.stop_event = threading.Event()

    def run(self) -> None:
        from indexer.fts5.schema import connect

        conn = connect(self._db_path)
        try:
            failures = index_folder(conn, self._root, self._on_progress, self.stop_event)
        finally:
            conn.close()
        if self._on_done is not None:
            self._on_done(failures)

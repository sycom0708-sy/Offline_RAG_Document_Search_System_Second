"""스캔 → 파싱 → 저장 오케스트레이션, 백그라운드 스레드 + 진행 콜백 (T2.8)."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from indexer.fts5.store import store_document
from indexer.scanner import scan_folder
from indexer.vector.store import embed_missing
from parser import parse_file
from parser.base import ParserError

ProgressCallback = Callable[[int, int, Path], None]


@dataclass
class IndexReport:
    """인덱싱 결과.

    파일 단위 실패(`failures`)와 기능 저하 경고(`warnings`)를 구분한다 —
    임베딩 모델이 없어 벡터를 못 만든 것은 "그 파일을 인덱싱하지 못했다"와
    전혀 다른 상황이다(키워드 검색은 정상 동작한다).
    """

    failures: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    indexed: int = 0
    embedded: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


DoneCallback = Callable[[IndexReport], None]


def index_folder(
    conn: sqlite3.Connection,
    root: str | Path,
    on_progress: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
    embed: bool = True,
) -> IndexReport:
    """폴더를 스캔해 순차적으로 파싱·저장하고, 이어서 임베딩을 만든다.

    파일 하나가 실패해도 인덱싱 전체를 멈추지 않는다 — 실패는 모아서
    반환하고 나머지 파일은 계속 처리한다.

    `embed=True`면 임베딩 모델을 준비해 청킹을 **토큰 기준**으로 수행하고
    (문자 기준으로 자르면 임베딩이 잘린다), 저장이 끝난 뒤 벡터를 계산한다.
    모델이 없으면 키워드 인덱싱만 하고 `warnings`에 남긴다 — 벡터가 없어도
    키워드 검색은 정상 동작하므로 실패로 취급하지 않는다.
    """
    files = list(scan_folder(root))
    total = len(files)
    report = IndexReport()

    embedder = None
    count_tokens = None
    if embed:
        embedder, embed_error = _prepare_embedder()
        if embedder is not None:
            count_tokens = embedder.count_tokens
        else:
            report.warnings.append(embed_error)

    for done, path in enumerate(files, start=1):
        if stop_event is not None and stop_event.is_set():
            break
        try:
            document = parse_file(path)
            store_document(conn, document, count_tokens=count_tokens)
            report.indexed += 1
        except ParserError as exc:
            report.failures.append((path, str(exc)))
        except Exception as exc:  # 예상 못한 오류도 인덱싱 전체를 죽이지 않는다
            report.failures.append((path, f"예상치 못한 오류: {exc}"))
        finally:
            if on_progress is not None:
                on_progress(done, total, path)

    if embedder is not None and not (stop_event is not None and stop_event.is_set()):
        try:
            report.embedded = embed_missing(conn, embedder)
        except Exception as exc:
            report.warnings.append(f"임베딩 계산 실패 (키워드 검색은 정상): {exc}")

    return report


def _prepare_embedder():
    """임베더를 준비한다. 실패하면 (None, 사유)를 돌려준다."""
    try:
        from indexer.vector.embedder import Embedder

        embedder = Embedder()
        embedder.count_tokens("")  # 모델 파일 존재 여부를 여기서 즉시 확인
        return embedder, ""
    except Exception as exc:
        return None, f"임베딩 생략 (키워드 검색은 정상 동작): {exc}"


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
        embed: bool = True,
    ) -> None:
        super().__init__(daemon=True)
        self._db_path = db_path
        self._root = root
        self._on_progress = on_progress
        self._on_done = on_done
        self._embed = embed
        self.stop_event = threading.Event()

    def run(self) -> None:
        from indexer.fts5.schema import connect

        conn = connect(self._db_path)
        try:
            report = index_folder(
                conn, self._root, self._on_progress, self.stop_event, embed=self._embed
            )
        finally:
            conn.close()
        if self._on_done is not None:
            self._on_done(report)

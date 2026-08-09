"""스캔 → 파싱 → 저장 오케스트레이션, 백그라운드 스레드 + 진행 콜백 (T2.8)."""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from indexer.fts5.store import store_document
from indexer.scanner import scan_folder
from indexer.vector.store import embed_missing
from parser import ParseStatus, parse_file
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
    pruned: int = 0  # 대상 폴더 밖 문서를 지운 개수 (T10.5, "새 폴더로 교체")

    @property
    def ok(self) -> bool:
        return not self.failures


DoneCallback = Callable[[IndexReport], None]


def _prune_stale_documents(conn: sqlite3.Connection, files: list[Path]) -> int:
    """이번 스캔에서 발견되지 않은 문서를 지우고 지운 개수를 반환한다.

    "사용자당 대상 폴더 하나"가 제품 전제다(PRD 4장). 그런데 대상 폴더를
    바꿔 재인덱싱해도 이전 폴더의 문서가 지워지지 않아 계속 쌓였다 — 다른
    PC·다른 세션에서 인덱싱한 흔적이 남은 채 검색 결과에 섞여 나오는 것을
    실사용 중 실제로 겪었다(2026-08-09). **이번 스캔 결과로 완전히
    교체한다**(사용자 확정).

    처음엔 "대상 폴더 바깥이면 지운다"(경로 접두사 비교)로 짰는데, **같은
    폴더 안에서 파일이 서브폴더로 옮겨진 경우**를 못 잡는다는 게 바로 다음
    실사용에서 드러났다 — 옮겨진 파일의 옛 경로도 여전히 "폴더 안"이라
    접두사 비교로는 안 지워지고, DB에 존재 안 하는 파일 경로만 유령처럼
    남았다. 그래서 접두사가 아니라 **이번에 실제로 스캔된 파일 목록 자체와
    대조**한다 — 폴더가 통째로 바뀐 경우와 폴더 안에서 파일이 옮겨지거나
    지워진 경우를 한 번에 잡는다. Phase 8의 "변경 안 된 파일은 건드리지
    않는다"(mtime/해시로 스킵)와는 다른 문제다 — 이건 전체 재파싱은 그대로
    두고 "지금 없는 문서만 지운다"는 절반만 한다.

    경로 비교는 대소문자를 접어서(`os.path.normcase`) 한다 — Windows는
    대소문자를 구분하지 않는데 문자열 그대로 비교하면 같은 파일도 대소문자가
    다르면 "사라졌다"고 오판할 수 있다.
    """
    scanned = {os.path.normcase(os.path.normpath(str(p.resolve()))) for p in files}

    stale_ids = []
    for doc_id, file_path in conn.execute("SELECT doc_id, file_path FROM documents"):
        candidate = os.path.normcase(os.path.normpath(file_path))
        if candidate not in scanned:
            stale_ids.append(doc_id)

    if stale_ids:
        conn.executemany(
            "DELETE FROM documents WHERE doc_id = ?", [(doc_id,) for doc_id in stale_ids]
        )
        conn.commit()  # ON DELETE CASCADE로 chunks·chunk_vectors도 함께 지워진다
    return len(stale_ids)


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

    스캔하자마자, 파싱을 시작하기 전에 이번 스캔에 없는 문서부터 지운다 —
    대상 폴더가 바뀌었든, 같은 폴더 안에서 파일이 옮겨지거나 지워졌든
    "지금 없는 건 지운다"(사용자 확정, "새 폴더로 완전히 교체"). 파일 하나
    파싱 전에 지우므로 이번 실행이 중간에 취소돼도(`stop_event`) 이미 지운
    항목 + 아직 처리 못 한 항목이 둘 다 "없음"으로 보이는 정도지, 지우다 만
    상태로 반쯤 섞이지는 않는다.
    """
    root_path = Path(root)
    files = list(scan_folder(root_path))
    total = len(files)

    report = IndexReport()
    report.pruned = _prune_stale_documents(conn, files)

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
            if document.status is ParseStatus.FAILED:
                # LegacyOfficeParser처럼 예외를 던지지 않고 document.errors에만
                # 담는 파서가 있다(T10.2) — 그대로 두면 0청크로 조용히 빠진다.
                report.failures.append(
                    (path, "; ".join(document.errors) or "알 수 없는 오류")
                )
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

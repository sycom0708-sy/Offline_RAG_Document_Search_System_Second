"""스캔 → 파싱 → 저장 오케스트레이션, 백그라운드 스레드 + 진행 콜백 (T2.8)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config.settings import ASSETS_DIR
from indexer.fts5.store import store_document
from indexer.incremental import FileChange, classify_file
from indexer.scanner import scan_folder
from indexer.vector.store import embed_missing
from parser import ParseStatus, parse_file
from parser.base import ParserError
from parser.utils.ids import make_doc_id

ProgressCallback = Callable[[int, int, Path], None]

# 현재 단계 (Phase 11-B, DESIGN §14.4) — 파일 진행률(`on_progress`)과 따로
# 실어 보낸다. 임베딩 구간은 파일이 아니라 **청크** 단위로 도는 데다
# (607청크 136초 실측, T10.26) 그동안 파일 진행률은 마지막 값에 멈춰 있어,
# 같은 콜백에 섞으면 "19/19에서 2분 넘게 멈춘 화면"이 된다.
STAGE_PARSING = "파싱"
STAGE_EMBEDDING = "임베딩"
STAGE_DONE = "완료"

StageCallback = Callable[[str, int, int], None]  # (단계, done, total)


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
    skipped: int = 0  # mtime·해시가 그대로라 재파싱을 건너뛴 파일 수 (Phase 8)
    scanned: int = 0  # 이번 실행의 대상 파일 수 (Phase 11-B, 문서 관리 "총")
    created: int = 0  # 인덱스에 처음 들어온 파일 수 (Phase 11-B, "신규")
    updated: int = 0  # 이미 있었는데 내용이 바뀐 파일 수 (Phase 11-B, "변경")
    stale_image_chunk_ids: list[str] = field(default_factory=list)  # 재파싱·정리로 사라진 이미지 청크 id (Phase 8, T8.4 썸네일 캐시 무효화용)

    @property
    def ok(self) -> bool:
        return not self.failures


DoneCallback = Callable[[IndexReport], None]


def failed_document_paths(conn: sqlite3.Connection) -> list[Path]:
    """인덱스에 `status=failed`로 남아 있는 문서의 경로 (Phase 11-B).

    `IndexReport.failures`는 **이번 실행**의 결과라 앱을 껐다 켜면 사라진다.
    실패는 그대로 인덱스에 남아 있으므로(0청크 문서) DB에서 다시 읽어 와야
    `재시도` 버튼과 파일 진단 목록이 재시작 후에도 의미를 갖는다.

    파싱 도중 예외를 던진 파일(`ParserError`)은 저장 자체가 안 돼 여기에
    안 잡히지만, 그런 파일은 `documents` 행이 없어 다음 인덱싱에서 신규로
    다시 시도되므로 재시도 대상으로 챙길 필요가 없다.
    """
    return [
        Path(row[0])
        for row in conn.execute("SELECT file_path FROM documents WHERE status = 'failed'")
    ]


def _prune_stale_documents(conn: sqlite3.Connection, files: list[Path]) -> tuple[int, list[str]]:
    """이번 스캔에서 발견되지 않은 문서를 지우고 (지운 개수, 이미지 청크 id 목록)을 반환한다.

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

    stale_image_chunk_ids: list[str] = []
    if stale_ids:
        placeholders = ",".join("?" * len(stale_ids))
        stale_image_chunk_ids = [
            row[0]
            for row in conn.execute(
                f"SELECT chunk_id FROM chunks WHERE doc_id IN ({placeholders}) AND type = 'image'",
                stale_ids,
            )
        ]
        conn.executemany(
            "DELETE FROM documents WHERE doc_id = ?", [(doc_id,) for doc_id in stale_ids]
        )
        conn.commit()  # ON DELETE CASCADE로 chunks·chunk_vectors도 함께 지워진다
        # 문서가 없어졌으니 그 doc_id 폴더에 모아둔 이미지도 함께 지운다
        # (Phase 11-D) — 안 지우면 중앙화된 곳이 지워진 문서의 잔재로
        # 영원히 불어난다. `stale_image_chunk_ids`(T8.4 썸네일 캐시용)와는
        # 별개다 — 그쪽은 청크 단위 id, 이쪽은 문서 단위 폴더다.
        for doc_id in stale_ids:
            shutil.rmtree(ASSETS_DIR / doc_id, ignore_errors=True)
    return len(stale_ids), stale_image_chunk_ids


def reindex_files(
    conn: sqlite3.Connection,
    paths: list[Path],
    on_progress: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
    embed: bool = True,
    on_stage: StageCallback | None = None,
) -> IndexReport:
    """지정한 파일만 **강제로** 다시 파싱한다 (Phase 11-B `재시도` [사용자 확정]).

    `index_folder()`와 두 가지가 다르다.

    1. **증분 스킵을 건너뛴다.** 실패한 문서도 mtime·해시가 저장돼 있어
       `classify_file()`은 `UNCHANGED`를 돌려준다(`indexer/incremental` 참고).
       재시도가 그 판정에 걸리면 아무 일도 일어나지 않으므로 무조건 파싱한다.
    2. 🔴 **`_prune_stale_documents()`를 부르지 않는다.** 대상이 폴더 전체가
       아니라 일부라서, 그대로 불렀다면 "이번 스캔에 없는 문서"에 나머지
       문서가 전부 걸려 인덱스가 통째로 지워진다. 폴더 정리를 `index_folder()`
       쪽에만 두고 이 함수는 아예 손대지 않는 것으로 구조적으로 막는다.
    """
    files = [path for path in paths if path.is_file()]
    return _run_index(
        conn,
        files,
        IndexReport(),
        force=True,
        on_progress=on_progress,
        stop_event=stop_event,
        embed=embed,
        on_stage=on_stage,
    )


def index_folder(
    conn: sqlite3.Connection,
    root: str | Path,
    on_progress: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
    embed: bool = True,
    on_stage: StageCallback | None = None,
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

    파일마다 `indexer.incremental.needs_reindex()`로 mtime(필요하면 해시까지)을
    확인해 변경이 없으면 파싱·저장을 건너뛴다(`report.skipped`, Phase 8) —
    기존 인덱스를 그대로 재사용한다.
    """
    root_path = Path(root)
    files = list(scan_folder(root_path))

    report = IndexReport()
    report.pruned, pruned_image_chunk_ids = _prune_stale_documents(conn, files)
    report.stale_image_chunk_ids.extend(pruned_image_chunk_ids)

    return _run_index(
        conn,
        files,
        report,
        force=False,
        on_progress=on_progress,
        stop_event=stop_event,
        embed=embed,
        on_stage=on_stage,
    )


def _run_index(
    conn: sqlite3.Connection,
    files: list[Path],
    report: IndexReport,
    *,
    force: bool,
    on_progress: ProgressCallback | None,
    stop_event: threading.Event | None,
    embed: bool,
    on_stage: StageCallback | None,
) -> IndexReport:
    """파싱 루프 + 임베딩. `index_folder()`와 `reindex_files()`의 공통 몸통이다.

    폴더 정리(`_prune_stale_documents`)는 **여기 없다** — 대상이 폴더 전체일
    때만 성립하는 동작이라 `index_folder()`에 남겨 뒀다.
    """
    total = len(files)
    report.scanned = total

    embedder = None
    count_tokens = None
    if embed:
        embedder, embed_error = _prepare_embedder()
        if embedder is not None:
            count_tokens = embedder.count_tokens
        else:
            report.warnings.append(embed_error)

    if on_stage is not None:
        on_stage(STAGE_PARSING, 0, total)

    for done, path in enumerate(files, start=1):
        if stop_event is not None and stop_event.is_set():
            break
        try:
            change = FileChange.CHANGED if force else classify_file(conn, path)
            if not change.needs_parse:
                # mtime(필요하면 해시까지)이 그대로다 — 재파싱을 건너뛴다 (Phase 8).
                report.skipped += 1
                continue
            # 문서별로 doc_id 폴더에 모은다(Phase 11-D) — 원본 문서 폴더 옆에
            # `.assets/`를 흩뿌리던 것을 중앙화. asset_dir_for()가 이 값을
            # 그대로 쓰므로 파서 쪽은 한 줄도 안 바뀐다.
            document = parse_file(path, asset_dir=ASSETS_DIR / make_doc_id(path))
            stale_image_chunk_ids = store_document(conn, document, count_tokens=count_tokens)
            report.stale_image_chunk_ids.extend(stale_image_chunk_ids)
            report.indexed += 1
            # 신규/변경은 파싱 **전**의 판정을 쓴다 — 저장하고 나면 전부
            # "이미 있는 문서"가 되어 사후에는 구분할 수 없다.
            if change is FileChange.NEW:
                report.created += 1
            else:
                report.updated += 1
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
            def _embed_progress(done: int, pending_total: int) -> None:
                if on_stage is not None:
                    on_stage(STAGE_EMBEDDING, done, pending_total)

            if on_stage is not None:
                on_stage(STAGE_EMBEDDING, 0, 0)
            report.embedded = embed_missing(conn, embedder, on_progress=_embed_progress)
        except Exception as exc:
            report.warnings.append(f"임베딩 계산 실패 (키워드 검색은 정상): {exc}")

    if on_stage is not None:
        on_stage(STAGE_DONE, total, total)
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
        on_stage: StageCallback | None = None,
        files: list[Path] | None = None,
    ) -> None:
        """`files`를 주면 폴더 전체 대신 그 파일들만 **강제로** 다시 파싱한다
        (Phase 11-B `재시도`). 이때 `root`는 쓰이지 않지만, 이후 상태 표시가
        어느 폴더에 대한 작업인지 알 수 있도록 그대로 받아 둔다."""
        super().__init__(daemon=True)
        self._db_path = db_path
        self._root = root
        self._on_progress = on_progress
        self._on_done = on_done
        self._embed = embed
        self._on_stage = on_stage
        self._files = files
        self.stop_event = threading.Event()

    def run(self) -> None:
        from indexer.fts5.schema import connect

        # 🔴 어떤 경우에도 `on_done`을 부른다. 예전에는 예외가 나면 그대로
        # 스레드가 죽어 완료 통지가 영영 안 갔는데, 그때 남는 것이 "닫히지
        # 않는 진행률 팝업" 정도였다. Phase 11-B부터는 문서 관리 페이지가
        # "인덱싱 중"에 갇히고 `인덱스 업데이트` 버튼이 계속 비활성이라
        # 앱을 껐다 켜지 않으면 회복이 안 된다 — 실제로 밟을 수 있는
        # 경로다(대상 폴더가 사라지면 `scan_folder()`가 예외를 던진다).
        report = IndexReport()
        try:
            conn = connect(self._db_path)
            try:
                if self._files is not None:
                    report = reindex_files(
                        conn,
                        self._files,
                        self._on_progress,
                        self.stop_event,
                        embed=self._embed,
                        on_stage=self._on_stage,
                    )
                else:
                    report = index_folder(
                        conn,
                        self._root,
                        self._on_progress,
                        self.stop_event,
                        embed=self._embed,
                        on_stage=self._on_stage,
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — 통지를 못 하는 편이 더 나쁘다
            # 파일 단위 실패가 아니라 실행 자체가 못 선 것이므로 `failures`가
            # 아니라 `warnings`에 담는다 — `재시도`가 붙잡을 파일이 없다.
            report.warnings.append(f"인덱싱을 시작하지 못했습니다: {exc}")
        if self._on_done is not None:
            self._on_done(report)

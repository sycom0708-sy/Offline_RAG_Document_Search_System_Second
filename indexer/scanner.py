"""폴더 재귀 스캔 (T2.1)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from config.settings import DATA_DIR
from parser.registry import is_supported


# Word·Excel·PowerPoint가 문서를 열어 둔 동안 옆에 만드는 잠금 파일 접두사.
# 확장자는 원본과 같아(`~$보고서.docx`) `is_supported()`를 통과하지만 내용은
# 수백 바이트짜리 스텁이라 **항상 파싱에 실패한다**.
_OFFICE_LOCK_PREFIX = "~$"

# 앱 자신의 인덱스 DB·설정·로그가 쌓이는 폴더 — 절대 스캔 대상에 넣지
# 않는다. 사용자가 이 폴더(또는 이를 포함하는 상위 폴더)를 검색 대상으로
# 고르면, 인덱싱이 `data/index.sqlite3`·`data/app_state.json`·
# `data/logs/*.log`에 쓰기를 하고, 폴더 감시가 그 변경을 다시 감지해
# 재인덱싱하는 무한 루프가 실사용 중 재현됐다(2026-08-28) — 진단 로그
# 파일 자체가 "문서"로 인식돼 매번 인덱싱되며 내용이 계속 불어났다.
_DATA_DIR_RESOLVED = DATA_DIR.resolve()


def scan_folder(root: str | Path) -> Iterator[Path]:
    """대상 폴더를 재귀적으로 탐색해 지원 형식 파일 경로만 순서대로 반환한다.

    숨김 폴더(`.`로 시작)는 건너뛴다 — 인덱스 캐시(`.assets` 등) 자기 자신을
    스캔 대상에 포함시키지 않기 위함이다.

    앱 자신의 `data/` 폴더(모듈 상단 설명 참고)도 어디에 있든 건너뛴다.

    Office 잠금 파일(`~$...`)도 건너뛴다 [Phase 11-B]. 지금까지는 조용히 실패
    목록에 쌓이기만 해서 눈에 띄지 않았는데, 문서 관리 페이지가 실패를
    보여주기 시작하자 **고칠 수 없는 실패 항목**으로 남는 것이 드러났다
    (실제 인덱스에서 `~$인증자격시험 세부사항_인증_1_28_v3.doc` 발견) —
    원본이 아니라 임시 파일이라 `재시도`를 눌러도 영원히 실패한다.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"대상 폴더가 아닙니다: {root}")

    for path in sorted(root.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.name.startswith(_OFFICE_LOCK_PREFIX):
            continue
        resolved = path.resolve()
        if resolved == _DATA_DIR_RESOLVED or _DATA_DIR_RESOLVED in resolved.parents:
            continue
        if path.is_file() and is_supported(path):
            yield path


def count_supported(root: str | Path) -> int:
    """진행 바 초기값(전체 파일 수) 계산용."""
    return sum(1 for _ in scan_folder(root))


_DRIVE_REMOTE = 4  # Windows GetDriveType()의 "네트워크 드라이브" 값


def is_network_path(path: str | Path) -> bool:
    """이 경로가 네트워크 위치(UNC 경로 또는 매핑된 네트워크 드라이브)인가 (T9.9).

    공유 폴더는 지원하지 않기로 확정했다[사용자 확정, 2026-09-04] — `doc_id`가
    절대경로 해시 기반이라 각 PC가 독립적으로 인덱싱해야 하고(Phase 3 "인덱스는
    이식할 수 없다"), 네트워크 경로는 Phase 8 증분 판정(mtime 우선 비교)·폴더
    감시(T8.5)가 로컬과 동일하게 동작하는지 검증된 적이 없다 — 조용히 스캔에서
    빼는 대신(`_DATA_DIR_RESOLVED`처럼) **선택 시점에 명시적으로 막는다**.

    `GetDriveTypeW()` 하나로 UNC 경로(`\\\\server\\share\\...`)와 매핑된
    네트워크 드라이브(`Z:\\...`, 사용자가 네트워크 드라이브인 줄 모르고 고를
    수 있다) 둘 다 잡는다 — UNC만 문자열로 검사하면 매핑된 드라이브를 놓친다.
    판정 못 하면(비-Windows, API 실패) False — 과탐지로 정상 로컬 폴더를
    막는 것보다는 낫다는 판단.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        drive, _ = os.path.splitdrive(str(Path(path).resolve()))
        if not drive:
            return False
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == _DRIVE_REMOTE
    except Exception:
        return False

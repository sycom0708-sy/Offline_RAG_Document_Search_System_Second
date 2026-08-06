"""폴더 재귀 스캔 (T2.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from parser.registry import is_supported


def scan_folder(root: str | Path) -> Iterator[Path]:
    """대상 폴더를 재귀적으로 탐색해 지원 형식 파일 경로만 순서대로 반환한다.

    숨김 폴더(`.`로 시작)는 건너뛴다 — 인덱스 캐시(`.assets` 등) 자기 자신을
    스캔 대상에 포함시키지 않기 위함이다.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"대상 폴더가 아닙니다: {root}")

    for path in sorted(root.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_file() and is_supported(path):
            yield path


def count_supported(root: str | Path) -> int:
    """진행 바 초기값(전체 파일 수) 계산용."""
    return sum(1 for _ in scan_folder(root))

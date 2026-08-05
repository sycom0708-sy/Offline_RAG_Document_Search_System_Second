"""파일 해시 및 mtime 조회 (Phase 8 증분 갱신에서 재사용)."""

from __future__ import annotations

import hashlib
from pathlib import Path

_BLOCK_SIZE = 1024 * 1024


def file_sha256(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as fp:
        for block in iter(lambda: fp.read(_BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def file_mtime(file_path: str | Path) -> float:
    return Path(file_path).stat().st_mtime

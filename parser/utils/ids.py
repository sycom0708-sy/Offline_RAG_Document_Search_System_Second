"""doc_id / chunk_id 생성."""

from __future__ import annotations

import uuid
from pathlib import Path

# 동일 경로의 파일은 재인덱싱 시에도 같은 doc_id를 갖도록 고정 네임스페이스를 쓴다 (Phase 8 증분 갱신 전제).
_NAMESPACE = uuid.UUID("6f1a2c3d-4e5b-4a7c-8d9e-0f1a2b3c4d5e")


def make_doc_id(file_path: str | Path) -> str:
    normalized = str(Path(file_path).resolve()).lower()
    return uuid.uuid5(_NAMESPACE, normalized).hex


def make_chunk_id(doc_id: str, chunk_type: str, ordinal: int) -> str:
    return f"{doc_id}_{chunk_type}_{ordinal:05d}"

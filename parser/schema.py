"""파서 공통 출력 스키마 (TECH 문서 4.2절)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ChunkType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"


class ParseStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class TableData:
    """표의 행·열 구조를 보존한 형태 (TECH 3.1절)."""

    rows: list[list[str]]
    caption: str = ""
    header_row: list[str] = field(default_factory=list)

    @classmethod
    def from_rows(cls, rows: list[list[str]], caption: str = "") -> "TableData | None":
        """추출된 행 목록에서 TableData를 만든다. 내용이 없으면 None.

        첫 행은 2행 이상일 때만 헤더로 승격한다. 1행짜리 표까지 헤더로 보내면
        rows가 비어 표 카드가 빈 표로 렌더링된다.
        """
        filled = [row for row in rows if any(cell.strip() for cell in row)]
        if not filled:
            return None
        if len(filled) == 1:
            return cls(rows=filled, caption=caption)
        return cls(rows=filled[1:], header_row=filled[0], caption=caption)

    def to_text(self) -> str:
        """FTS5 인덱싱용 평문. 캡션·헤더를 앞세워 키워드 매칭 가중을 준다 (TECH 4.3절)."""
        parts = []
        if self.caption:
            parts.append(self.caption)
        if self.header_row:
            parts.append(" | ".join(self.header_row))
        for row in self.rows:
            parts.append(" | ".join(row))
        return "\n".join(parts)


@dataclass
class ImageData:
    """추출·캡처된 이미지의 위치와 출처."""

    image_path: str
    caption: str = ""
    width: int = 0
    height: int = 0
    # extracted: 문서에 삽입된 실제 이미지 / rendered: 벡터 도형 페이지 렌더링 캡처
    origin: str = "extracted"


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    file_path: str
    file_name: str
    type: ChunkType
    page_or_slide: int | None
    content: str
    # 이 청크가 속한 절의 제목 (T10.31) — PDF는 페이지 최대 글꼴, docx는
    # Heading 스타일, pptx는 제목 플레이스홀더에서 가져온다. 찾지 못하면 빈
    # 문자열이고, 그때는 결과 카드에 제목 줄이 안 나올 뿐이다.
    heading: str = ""
    keywords: list[str] = field(default_factory=list)
    embedding_vector: list[float] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_mtime: float | None = None
    source_hash: str | None = None
    table: TableData | None = None
    image: ImageData | None = None

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = ChunkType(self.type)
        if self.type is ChunkType.TABLE and self.table is None:
            raise ValueError(f"table 청크에 TableData가 없습니다: {self.chunk_id}")
        if self.type is ChunkType.IMAGE and self.image is None:
            raise ValueError(f"image 청크에 ImageData가 없습니다: {self.chunk_id}")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type.value
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ParsedDocument:
    doc_id: str
    file_path: str
    file_name: str
    title: str
    chunks: list[Chunk] = field(default_factory=list)
    status: ParseStatus = ParseStatus.OK
    errors: list[str] = field(default_factory=list)
    source_mtime: float | None = None
    source_hash: str | None = None

    def chunks_of(self, chunk_type: ChunkType) -> list[Chunk]:
        return [c for c in self.chunks if c.type is chunk_type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "title": self.title,
            "chunks": [c.to_dict() for c in self.chunks],
            "status": self.status.value,
            "errors": list(self.errors),
            "source_mtime": self.source_mtime,
            "source_hash": self.source_hash,
        }

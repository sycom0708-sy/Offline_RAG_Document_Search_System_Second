"""준수율 검증용 테스트셋 스키마·로더 (T6.4).

**실문서 발췌를 저장소에 커밋하지 않는다.** 이 저장소는 다른 노트북에서
GitHub로 push되므로, 실업무 문서 기반 테스트셋은 `.gitignore` 대상인 `data/`에
두고 **문서 본문 대신 `chunk_ids`만** 적는다 — 발췌는 측정할 때 로컬 인덱스
DB에서 끌어온다. 커밋용 합성 테스트셋만 `excerpts`에 본문을 직접 담는다.

케이스 하나:

    {
      "id": "spec-ram",
      "question": "최소 사양 RAM은 얼마인가요?",
      "expect_abstain": false,      # 근거가 있는 질문
      "keywords": ["8GB"],          # 정답 판정용 (모두 포함해야 정답)
      "chunk_ids": ["..."]          # 또는 "excerpts": [{file_name, location, text}]
    }
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from slm.prompt import Excerpt


class TestsetError(ValueError):
    """테스트셋 파일이 스키마에 맞지 않는다."""

    # pytest가 `Test*` 이름을 테스트 클래스로 수집하려다 경고를 낸다.
    __test__ = False


@dataclass(frozen=True)
class TestCase:
    __test__ = False  # pytest 수집 대상이 아니다

    id: str
    question: str
    # 이 질문에 대해 모델이 기권("문서에서 찾을 수 없습니다")해야 하는가.
    expect_abstain: bool
    keywords: tuple[str, ...] = ()
    chunk_ids: tuple[str, ...] = ()
    excerpts: tuple[Excerpt, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.question:
            raise TestsetError(f"id와 question은 비울 수 없습니다: {self.id!r}")
        if not self.expect_abstain and not self.keywords:
            # 근거 있는 질문인데 정답 키워드가 없으면 응답 정확도를 잴 수 없다.
            raise TestsetError(f"[{self.id}] 근거 있는 질문에는 keywords가 필요합니다")


@dataclass
class Testset:
    __test__ = False  # pytest 수집 대상이 아니다

    cases: list[TestCase] = field(default_factory=list)
    source: str = ""

    def __len__(self) -> int:
        return len(self.cases)

    @property
    def grounded(self) -> list[TestCase]:
        return [c for c in self.cases if not c.expect_abstain]

    @property
    def ungrounded(self) -> list[TestCase]:
        return [c for c in self.cases if c.expect_abstain]


def _parse_case(raw: dict) -> TestCase:
    excerpts = tuple(
        Excerpt(
            file_name=item.get("file_name", ""),
            location=item.get("location", "-"),
            text=item.get("text", ""),
        )
        for item in raw.get("excerpts", [])
    )
    # 키워드 한 자리에 동의어 목록을 넣을 수 있다 — JSON의 리스트를 튜플로만 바꾼다.
    keywords = tuple(
        tuple(item) if isinstance(item, list) else item
        for item in raw.get("keywords", [])
    )
    return TestCase(
        id=str(raw.get("id", "")),
        question=str(raw.get("question", "")),
        expect_abstain=bool(raw.get("expect_abstain", False)),
        keywords=keywords,
        chunk_ids=tuple(raw.get("chunk_ids", [])),
        excerpts=excerpts,
        note=str(raw.get("note", "")),
    )


def load_testset(path: str | Path) -> Testset:
    path = Path(path)
    if not path.is_file():
        raise TestsetError(
            f"테스트셋 파일이 없습니다: {path}\n"
            "`python -m scripts.build_slm_testset --help`로 만드는 방법을 확인하세요."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TestsetError(f"JSON 형식 오류({path}): {exc}") from exc

    cases_raw = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(cases_raw, list) or not cases_raw:
        raise TestsetError(f"cases 배열이 비어 있습니다: {path}")

    cases = [_parse_case(item) for item in cases_raw]
    seen = set()
    for case in cases:
        if case.id in seen:
            raise TestsetError(f"id가 중복됩니다: {case.id}")
        seen.add(case.id)
    return Testset(cases=cases, source=str(path))


def resolve_excerpts(case: TestCase, conn: sqlite3.Connection | None = None) -> list[Excerpt]:
    """케이스의 발췌를 만든다. 인라인 본문이 있으면 그것을, 없으면 DB에서 끌어온다.

    `chunk_ids`가 있는데 DB가 없거나 청크가 사라졌으면 조용히 건너뛰지 않고
    올린다 — 발췌가 비면 모든 모델이 기권해 측정이 통째로 무의미해진다.
    """
    if case.excerpts:
        return list(case.excerpts)
    if not case.chunk_ids:
        return []
    if conn is None:
        raise TestsetError(f"[{case.id}] chunk_ids를 풀려면 인덱스 DB 연결이 필요합니다")

    from indexer.fts5.search import SearchResult
    from parser.schema import ChunkType

    placeholders = ",".join("?" * len(case.chunk_ids))
    rows = conn.execute(
        f"""SELECT chunk_id, doc_id, file_path, file_name, type, page_or_slide,
                   content, caption, table_json, image_json
              FROM chunks WHERE chunk_id IN ({placeholders})""",
        case.chunk_ids,
    ).fetchall()

    by_id = {}
    for row in rows:
        result = SearchResult(
            chunk_id=row[0], doc_id=row[1], file_path=row[2], file_name=row[3],
            type=ChunkType(row[4]), page_or_slide=row[5], content=row[6],
            caption=row[7], score=0.0, table_json=row[8], image_json=row[9],
        )
        by_id[row[0]] = Excerpt.from_result(result)

    missing = [cid for cid in case.chunk_ids if cid not in by_id]
    if missing:
        raise TestsetError(
            f"[{case.id}] 인덱스에 없는 chunk_id: {', '.join(missing)}\n"
            "인덱스를 다시 만들었다면 테스트셋도 다시 만들어야 합니다."
        )
    # 테스트셋에 적힌 순서를 유지한다 — 발췌 번호가 바뀌면 채점 기록과 어긋난다.
    return [by_id[cid] for cid in case.chunk_ids]

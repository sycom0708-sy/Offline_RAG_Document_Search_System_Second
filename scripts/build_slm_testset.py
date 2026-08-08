"""검증용 테스트셋 초안 생성·점검 (T6.4).

    python -m scripts.build_slm_testset draft --out data/slm_testset.draft.json
    python -m scripts.build_slm_testset validate data/slm_testset.json

**질문은 사람이 쓴다.** 자동 생성한 질문은 청크 문장을 뒤집어 놓은 수준이라
"근거 없는 질문"을 제대로 만들 수 없는데, 그게 이 Phase의 핵심 지표다. 그래서
이 스크립트는 인덱스에서 **후보 청크를 뽑아 초안 틀을 만들어 주는 데까지만**
하고, 질문·정답 키워드는 초안 파일을 열어 채운다.

초안·완성본 모두 기본 출력 경로가 `data/` 아래다 — `.gitignore` 대상이라
실업무 문서 발췌가 저장소로 새지 않는다(계획 §③). 커밋되는 합성 테스트셋은
`tests/fixtures/slm_testset_sample.json`에 따로 있다.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

from indexer.fts5.schema import connect
from slm.testset import TestsetError, load_testset, resolve_excerpts

DEFAULT_DB = "data/index.sqlite3"
DEFAULT_DRAFT = "data/slm_testset.draft.json"

# 너무 짧은 청크는 질문을 만들 거리가 없다.
MIN_CONTENT_CHARS = 120
PREVIEW_CHARS = 400


def _sample_chunks(conn: sqlite3.Connection, count: int, seed: int) -> list[sqlite3.Row]:
    """문서를 고루 훑도록 doc_id별로 돌아가며 뽑는다.

    한 문서에서 몰아 뽑으면 그 문서의 서술 방식에만 맞춘 테스트셋이 된다.
    """
    rows = conn.execute(
        """SELECT chunk_id, doc_id, file_name, page_or_slide, content
             FROM chunks
            WHERE type = 'text' AND length(content) >= ?
            ORDER BY doc_id, chunk_id""",
        (MIN_CONTENT_CHARS,),
    ).fetchall()

    by_doc: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_doc.setdefault(row["doc_id"], []).append(row)

    rng = random.Random(seed)
    for chunks in by_doc.values():
        rng.shuffle(chunks)

    picked: list[sqlite3.Row] = []
    doc_ids = sorted(by_doc)
    rng.shuffle(doc_ids)
    while len(picked) < count and any(by_doc[d] for d in doc_ids):
        for doc_id in doc_ids:
            if by_doc[doc_id] and len(picked) < count:
                picked.append(by_doc[doc_id].pop())
    return picked


def _draft_case(index: int, row: sqlite3.Row, *, expect_abstain: bool) -> dict:
    hint = (
        "이 발췌로는 답할 수 없는 질문을 쓴다 (같은 주제지만 발췌에 없는 항목)"
        if expect_abstain
        else "이 발췌만 보면 답할 수 있는 질문을 쓴다"
    )
    case = {
        "id": f"{'abstain' if expect_abstain else 'grounded'}-{index:02d}",
        "question": "",
        "expect_abstain": expect_abstain,
        "keywords": [],
        "chunk_ids": [row["chunk_id"]],
        "_hint": hint,
        "_preview": row["content"][:PREVIEW_CHARS],
    }
    if expect_abstain:
        case.pop("keywords")
    return case


def build_draft(db_path: str, out_path: str, *, grounded: int, abstain: int, seed: int) -> Path:
    conn = connect(db_path)
    try:
        rows = _sample_chunks(conn, grounded + abstain, seed)
    finally:
        conn.close()

    if len(rows) < grounded + abstain:
        print(
            f"경고: 조건을 만족하는 청크가 {len(rows)}개뿐입니다 "
            f"(요청 {grounded + abstain}개). 인덱싱된 문서를 늘리거나 개수를 줄이세요.",
            file=sys.stderr,
        )

    cases = []
    for index, row in enumerate(rows[:grounded], start=1):
        cases.append(_draft_case(index, row, expect_abstain=False))
    for index, row in enumerate(rows[grounded:], start=1):
        cases.append(_draft_case(index, row, expect_abstain=True))

    # 발췌가 아예 없는 케이스 — 모델이 자체 지식으로 답해버리는지 보는 자리다.
    cases.append({
        "id": "abstain-no-excerpt",
        "question": "",
        "expect_abstain": True,
        "chunk_ids": [],
        "_hint": "발췌 없이 던질 일반 상식 질문을 쓴다 (예: 특정 인물의 생년)",
    })

    payload = {
        "_comment": (
            "질문(question)과 정답 키워드(keywords)를 직접 채운 뒤 _hint/_preview를 지우고 "
            "data/slm_testset.json으로 저장하세요. 이 파일은 실문서 발췌를 담으므로 "
            "절대 커밋하지 않습니다."
        ),
        "cases": cases,
    }
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def validate(testset_path: str, db_path: str) -> int:
    try:
        testset = load_testset(testset_path)
    except TestsetError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    blank = [c.id for c in testset.cases if not c.question.strip()]
    if blank:
        print(f"오류: 질문이 비어 있는 케이스: {', '.join(blank)}", file=sys.stderr)
        return 1

    conn = connect(db_path) if Path(db_path).is_file() else None
    try:
        for case in testset.cases:
            try:
                excerpts = resolve_excerpts(case, conn)
            except TestsetError as exc:
                print(f"오류: {exc}", file=sys.stderr)
                return 1
            if not case.expect_abstain and not excerpts:
                print(f"오류: [{case.id}] 근거 있는 질문인데 발췌가 없습니다", file=sys.stderr)
                return 1
    finally:
        if conn is not None:
            conn.close()

    print(f"테스트셋 {len(testset)}건 — 근거 있음 {len(testset.grounded)} / "
          f"근거 없음 {len(testset.ungrounded)}")
    if not testset.grounded or not testset.ungrounded:
        print("경고: 한쪽이 비어 있으면 과잉 기권율이나 기권 정확도를 잴 수 없습니다",
              file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.build_slm_testset")
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="인덱스에서 초안 틀을 만든다")
    draft.add_argument("--db", default=DEFAULT_DB)
    draft.add_argument("--out", default=DEFAULT_DRAFT)
    draft.add_argument("--grounded", type=int, default=15, help="근거 있는 질문 수")
    draft.add_argument("--abstain", type=int, default=10, help="근거 없는 질문 수")
    draft.add_argument("--seed", type=int, default=20260807, help="샘플링 시드(재현용)")

    check = sub.add_parser("validate", help="작성된 테스트셋을 점검한다")
    check.add_argument("testset")
    check.add_argument("--db", default=DEFAULT_DB)

    args = parser.parse_args(argv)

    if args.command == "draft":
        if not Path(args.db).is_file():
            print(f"오류: 인덱스 DB가 없습니다: {args.db}", file=sys.stderr)
            return 1
        out = build_draft(args.db, args.out, grounded=args.grounded,
                          abstain=args.abstain, seed=args.seed)
        print(f"초안 생성: {out}\n질문과 정답 키워드를 채운 뒤 validate로 점검하세요.")
        return 0

    return validate(args.testset, args.db)


if __name__ == "__main__":
    raise SystemExit(main())

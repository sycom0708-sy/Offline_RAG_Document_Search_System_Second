"""AI 요약 회귀 측정 (T7.11) — 안전장치를 통과시킨 뒤에도 품질이 유지되는가.

Phase 6은 **순수 추론**을 쟀다: 프롬프트를 넣고 답을 받아 채점. Phase 7은 그
답이 사용자에게 닿기 전에 4단계 안전장치를 지난다. 그 과정에서 품질이
떨어지지 않았는지, 그리고 **Phase 6이 잡지 못했던 실패를 4단계가 잡아내는지**
를 같은 테스트셋으로 확인한다.

    python -m scripts.regress_summary --testset data/slm_testset.json

Phase 6 권장 사양 측정치(26문항, Qwen3.5-4B): 기권정확도 81.8% /
응답정확도 80.0% / 과잉기권 6.7%. 실패 2건은 객관식 발췌에 대해 발췌에 없는
정답을 지어낸 것이었다 — 4단계가 이걸 "확인 필요"로 잡아야 한다.

**gate 1(유사도 임계값)은 이 측정에서 우회한다.** 테스트셋은 검색이 아니라
`chunk_ids`로 근거를 직접 지정하므로 유사도라는 개념이 없다. gate 1은
`tests/test_slm_summarize.py`가 따로 검증한다.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import DEFAULT_SLM_PROFILE, SLM_ORDER, get_slm_profile
from indexer.fts5.schema import connect
from slm.prompt import contains_keywords, is_abstention
from slm.service import SlmService
from slm.summarize import Summary, SummaryStatus, summarize_excerpts
from slm.testset import TestCase, TestsetError, load_testset, resolve_excerpts

DEFAULT_TESTSET = "data/slm_testset.json"
DEFAULT_DB = "data/index.sqlite3"

@dataclass
class CaseOutcome:
    case_id: str
    question: str
    expect_abstain: bool
    answer: str
    status: str
    abstained: bool
    keywords_hit: bool
    needs_review: bool
    review_reason: str
    elapsed_sec: float


@dataclass
class Report:
    outcomes: list[CaseOutcome] = field(default_factory=list)

    @property
    def _grounded(self):
        return [o for o in self.outcomes if not o.expect_abstain]

    @property
    def _ungrounded(self):
        return [o for o in self.outcomes if o.expect_abstain]

    @property
    def abstain_accuracy(self) -> float | None:
        cases = self._ungrounded
        return sum(o.abstained for o in cases) / len(cases) if cases else None

    @property
    def over_abstain_rate(self) -> float | None:
        cases = self._grounded
        return sum(o.abstained for o in cases) / len(cases) if cases else None

    @property
    def answer_accuracy(self) -> float | None:
        cases = self._grounded
        return sum(o.keywords_hit for o in cases) / len(cases) if cases else None

    @property
    def review_flagged(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.needs_review]

    @property
    def caught_hallucinations(self) -> list[CaseOutcome]:
        """**이 측정의 핵심 지표.**

        "근거 없이 답해야 하는데 답해버린" 케이스 중 4단계가 잡아낸 것.
        Phase 6에서는 이것이 그대로 사용자에게 갔다.
        """
        return [o for o in self.outcomes
                if o.expect_abstain and not o.abstained and o.needs_review]

    @property
    def missed_hallucinations(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes
                if o.expect_abstain and not o.abstained and not o.needs_review]


def run_case(case: TestCase, conn: sqlite3.Connection | None, service: SlmService) -> CaseOutcome:
    excerpts = resolve_excerpts(case, conn)
    summary: Summary = summarize_excerpts(case.question, excerpts, service)
    answer = summary.text or ""
    abstained = (
        summary.status is SummaryStatus.ABSTAINED
        or summary.status is SummaryStatus.NO_EVIDENCE
        or is_abstention(answer)
    )

    return CaseOutcome(
        case_id=case.id,
        question=case.question,
        expect_abstain=case.expect_abstain,
        answer=answer,
        status=summary.status.value,
        abstained=abstained,
        keywords_hit=contains_keywords(answer, getattr(case, "keywords", None) or []),
        needs_review=summary.needs_review,
        review_reason=summary.review_reason,
        elapsed_sec=summary.elapsed_sec,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.regress_summary")
    parser.add_argument("--testset", default=DEFAULT_TESTSET)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--model", default=DEFAULT_SLM_PROFILE, choices=SLM_ORDER)
    parser.add_argument("--limit", type=int, default=None, help="문항 수 제한(빠른 확인용)")
    parser.add_argument("--show", type=int, default=0, help="표본 육안 검증용 출력 건수")
    args = parser.parse_args(argv)

    try:
        testset = load_testset(args.testset)
    except TestsetError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    profile = get_slm_profile(args.model)
    if not profile.is_installed():
        print(f"오류: {profile.label} 미설치 — `python -m slm.download {args.model}`",
              file=sys.stderr)
        return 1

    cases = list(testset.cases)[: args.limit] if args.limit else list(testset.cases)
    conn = connect(args.db) if Path(args.db).is_file() else None
    report = Report()

    print("=" * 88)
    print(f"AI 요약 회귀 측정 (4단계 안전장치 통과 후) — {profile.label}")
    print(f"테스트셋: {args.testset} — {len(cases)}문항")
    print("=" * 88)

    # 측정 중에는 서버를 내리지 않는다(유휴 타임아웃 0).
    try:
        with SlmService(args.model, idle_timeout_sec=0) as service:
            for index, case in enumerate(cases, start=1):
                outcome = run_case(case, conn, service)
                report.outcomes.append(outcome)
                mark = "기권" if outcome.abstained else "응답"
                flag = " ⚠확인필요" if outcome.needs_review else ""
                print(f"  [{index:3}/{len(cases)}] {outcome.case_id:<24} "
                      f"{mark} {outcome.elapsed_sec:5.1f}s{flag}")
    finally:
        if conn is not None:
            conn.close()

    _print_summary(report, args.show)
    return 0


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _print_summary(report: Report, show: int) -> None:
    print()
    print("=" * 88)
    print(f"  기권정확도 {_pct(report.abstain_accuracy)}   "
          f"과잉기권 {_pct(report.over_abstain_rate)}   "
          f"응답정확도 {_pct(report.answer_accuracy)}")
    print(f"  4단계 '확인 필요' 표시: {len(report.review_flagged)}건 / {len(report.outcomes)}문항")
    print("=" * 88)
    print()
    print("Phase 6 권장 사양 측정치(순수 추론): 기권정확도 81.8% / 과잉기권 6.7% / 응답정확도 80.0%")
    print()

    caught = report.caught_hallucinations
    missed = report.missed_hallucinations
    print(f"근거 없이 답한 케이스 {len(caught) + len(missed)}건 중 "
          f"**4단계가 잡은 것 {len(caught)}건**, 놓친 것 {len(missed)}건")
    for outcome in caught:
        print(f"  ✔ 잡음  [{outcome.case_id}] {outcome.answer[:60]}")
        print(f"          사유: {outcome.review_reason}")
    for outcome in missed:
        print(f"  ✘ 놓침  [{outcome.case_id}] {outcome.answer[:60]}")

    if show:
        print()
        print("### 표본")
        for outcome in report.outcomes[:show]:
            expect = "답해야 함" if not outcome.expect_abstain else "기권해야 함"
            actual = "기권" if outcome.abstained else "응답"
            print(f"- [{outcome.case_id}] ({expect} / 실제 {actual})")
            print(f"  Q: {outcome.question}")
            print(f"  A: {outcome.answer[:200]}")


if __name__ == "__main__":
    raise SystemExit(main())

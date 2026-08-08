"""sLM 후보 준수율·속도·메모리 측정 (T6.5/T6.6).

    python -m scripts.benchmark_slm                          # 합성 테스트셋 + 설치된 모델 전부
    python -m scripts.benchmark_slm --models exaone-4.0-1.2b
    python -m scripts.benchmark_slm --testset data/slm_testset.json --show 5

**준수율을 한 숫자로 보지 않는다.** 무조건 "모른다"만 답하는 모델은 기권율
100%지만 쓸모가 없다. 기권 정확도(근거 없는 질문을 제대로 넘긴 비율)와 과잉
기권율(근거 있는 질문을 잘못 넘긴 비율)을 함께 낸다.

모델마다 llama-server를 **한 번만** 올리고 전 문항을 웜 상태로 돌린다 —
Phase 7의 실제 앱이 모델을 상주시키므로 그쪽이 실사용에 가깝다. 로딩 시간은
따로 1회 기록한다.

원시 답변은 `data/`(gitignore 대상)에만 남긴다. 실업무 문서 발췌를 그대로
인용할 수 있어 저장소에 들어가면 안 된다.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from config.settings import SLM_CANDIDATES, SLM_ORDER, SlmProfile, get_slm_profile
from indexer.fts5.schema import connect
from slm import runtime
from slm.client import LlamaClient, LlamaClientError
from slm.prompt import (
    ABSTAIN_TEXT,
    build_messages,
    clean_answer,
    contains_keywords,
    is_abstention,
)
from slm.testset import TestCase, TestsetError, load_testset, resolve_excerpts

DEFAULT_TESTSET = "tests/fixtures/slm_testset_sample.json"
DEFAULT_DB = "data/index.sqlite3"
DEFAULT_OUT_DIR = Path("data")


@dataclass
class CaseOutcome:
    case_id: str
    question: str
    expect_abstain: bool
    answer: str
    abstained: bool
    keywords_hit: bool
    elapsed_sec: float
    completion_tokens: int
    error: str = ""


@dataclass
class ModelReport:
    key: str
    label: str
    size_gb: float
    load_seconds: float = 0.0
    memory_peak_mb: float | None = None
    outcomes: list[CaseOutcome] = field(default_factory=list)
    failed: str = ""

    # --- 준수율 -----------------------------------------------------------
    @property
    def _grounded(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if not o.expect_abstain]

    @property
    def _ungrounded(self) -> list[CaseOutcome]:
        return [o for o in self.outcomes if o.expect_abstain]

    @property
    def abstain_accuracy(self) -> float | None:
        """근거 없는 질문 중 올바르게 기권한 비율 (핵심 지표, 높을수록 좋음)."""
        cases = self._ungrounded
        if not cases:
            return None
        return sum(o.abstained for o in cases) / len(cases)

    @property
    def over_abstain_rate(self) -> float | None:
        """근거 있는 질문 중 잘못 기권한 비율 (낮을수록 좋음)."""
        cases = self._grounded
        if not cases:
            return None
        return sum(o.abstained for o in cases) / len(cases)

    @property
    def answer_accuracy(self) -> float | None:
        """근거 있는 질문 전체 중 정답 키워드를 담은 비율."""
        cases = self._grounded
        if not cases:
            return None
        return sum(o.keywords_hit for o in cases) / len(cases)

    # --- 속도 -------------------------------------------------------------
    @property
    def median_sec(self) -> float:
        samples = [o.elapsed_sec for o in self.outcomes if not o.error]
        return statistics.median(samples) if samples else 0.0

    @property
    def tokens_per_sec(self) -> float:
        tokens = sum(o.completion_tokens for o in self.outcomes if not o.error)
        seconds = sum(o.elapsed_sec for o in self.outcomes if not o.error)
        return tokens / seconds if seconds else 0.0


def check_prompt_delivery(client: LlamaClient) -> str:
    """근거 강제 규칙이 **실제 프롬프트에 실렸는지** 확인한다. 문제 없으면 빈 문자열.

    EXAONE-4.0 템플릿이 system 메시지를 통째로 버리는 것을 실측으로 확인했다.
    그대로 측정했다면 "규칙을 안 지키는 모델"이라는 잘못된 결론이 나왔을 것이다.
    후보마다 템플릿이 다르므로 모델을 올릴 때마다 한 번씩 확인한다.
    """
    rendered = client.apply_template(build_messages("확인용 질문", []))
    if not rendered:
        return ""  # 엔드포인트가 없는 서버 버전 — 확인을 건너뛴다
    if ABSTAIN_TEXT not in rendered:
        return (
            "근거 강제 규칙이 chat template 렌더링에서 사라졌습니다. "
            "이 상태로는 준수율을 측정해도 의미가 없습니다."
        )
    return ""


def _run_case(client: LlamaClient, case: TestCase, conn, *, max_tokens: int) -> CaseOutcome:
    excerpts = resolve_excerpts(case, conn)
    messages = build_messages(case.question, excerpts)

    try:
        completion = client.chat(messages, max_tokens=max_tokens)
    except LlamaClientError as exc:
        return CaseOutcome(
            case_id=case.id, question=case.question, expect_abstain=case.expect_abstain,
            answer="", abstained=False, keywords_hit=False,
            elapsed_sec=0.0, completion_tokens=0, error=str(exc),
        )

    answer = clean_answer(completion.text)
    return CaseOutcome(
        case_id=case.id,
        question=case.question,
        expect_abstain=case.expect_abstain,
        answer=answer,
        abstained=is_abstention(answer),
        keywords_hit=contains_keywords(answer, list(case.keywords)),
        elapsed_sec=completion.elapsed_sec,
        completion_tokens=completion.completion_tokens,
    )


def benchmark_model(
    profile: SlmProfile,
    cases: list[TestCase],
    conn: sqlite3.Connection | None,
    *,
    n_ctx: int,
    n_threads: int | None,
    max_tokens: int,
    quiet: bool = False,
) -> ModelReport:
    report = ModelReport(key=profile.key, label=profile.label, size_gb=profile.size_gb)

    extra = list(profile.extra_server_args) or None
    try:
        with runtime.llama_server(
            profile.local_path, n_ctx=n_ctx, n_threads=n_threads, extra_args=extra
        ) as server:
            report.load_seconds = server.load_seconds
            if not quiet:
                print(f"  로딩 {server.load_seconds:.1f}초, 문항 {len(cases)}건 측정 중…")

            client = LlamaClient(server.port)

            problem = check_prompt_delivery(client)
            if problem:
                report.failed = problem
                return report

            peak = 0.0
            for index, case in enumerate(cases, start=1):
                outcome = _run_case(client, case, conn, max_tokens=max_tokens)
                report.outcomes.append(outcome)
                memory = server.memory_mb()
                if memory:
                    peak = max(peak, memory[1])
                if not quiet:
                    mark = "!" if outcome.error else ("기권" if outcome.abstained else "응답")
                    # 7.8B급은 한 문항에 분 단위가 걸린다. 파이프로 넘길 때
                    # 버퍼링되면 진행 상황을 전혀 볼 수 없어 즉시 내보낸다.
                    print(f"    [{index:>3}/{len(cases)}] {outcome.case_id:<24} "
                          f"{mark} {outcome.elapsed_sec:5.1f}s", flush=True)
            report.memory_peak_mb = peak or None
    except runtime.LlamaRuntimeError as exc:
        report.failed = str(exc)
    return report


def report_to_dict(report: ModelReport) -> dict:
    """`asdict`만 쓰면 계산 지표(property)가 빠져 리포트를 다시 계산해야 한다."""
    data = asdict(report)
    data["metrics"] = {
        "abstain_accuracy": report.abstain_accuracy,
        "over_abstain_rate": report.over_abstain_rate,
        "answer_accuracy": report.answer_accuracy,
        "median_sec": report.median_sec,
        "tokens_per_sec": report.tokens_per_sec,
    }
    return data


def _pct(value: float | None) -> str:
    return "  -  " if value is None else f"{value * 100:5.1f}%"


def print_report(reports: list[ModelReport]) -> None:
    print()
    print("=" * 96)
    print(f"{'모델':<22}{'크기':>7}{'기권정확도':>11}{'과잉기권':>10}{'응답정확도':>11}"
          f"{'중앙지연':>10}{'tok/s':>8}{'메모리':>10}{'로딩':>8}")
    print("-" * 96)
    for report in reports:
        if report.failed:
            print(f"{report.label:<22}{report.size_gb:>6.2f}G   측정 실패: {report.failed[:50]}")
            continue
        memory = f"{report.memory_peak_mb:,.0f}MB" if report.memory_peak_mb else "   -  "
        print(
            f"{report.label:<22}{report.size_gb:>6.2f}G"
            f"{_pct(report.abstain_accuracy):>11}{_pct(report.over_abstain_rate):>10}"
            f"{_pct(report.answer_accuracy):>11}{report.median_sec:>9.1f}s"
            f"{report.tokens_per_sec:>8.1f}{memory:>10}{report.load_seconds:>7.1f}s"
        )
    print("=" * 96)
    print("기권정확도↑ 과잉기권↓ 응답정확도↑ — 기권정확도만 높은 모델은 '모른다'만 하는 모델이다.")
    print("자동 채점은 근사치다. --show로 표본을 직접 확인할 것.")


def print_samples(reports: list[ModelReport], count: int) -> None:
    """표본 육안 검증 — 채점기가 오판하지 않았는지 사람이 본다."""
    for report in reports:
        if report.failed:
            continue
        print(f"\n### {report.label} 표본")
        # 채점이 틀리기 쉬운 것부터 — 기대와 어긋난 케이스를 먼저 보여준다.
        wrong = [o for o in report.outcomes
                 if o.expect_abstain != o.abstained or (not o.expect_abstain and not o.keywords_hit)]
        shown = (wrong + [o for o in report.outcomes if o not in wrong])[:count]
        for outcome in shown:
            expected = "기권해야 함" if outcome.expect_abstain else "답해야 함"
            actual = "기권" if outcome.abstained else "응답"
            print(f"- [{outcome.case_id}] ({expected} / 실제 {actual})")
            print(f"  Q: {outcome.question}")
            print(f"  A: {outcome.answer[:300] or '(빈 응답)'}{outcome.error and ' 오류: ' + outcome.error}")


def load_result(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rescore(result_path: str, testset_path: str, out_path: str | None = None) -> int:
    """저장된 답변을 **다시 추론하지 않고** 채점만 다시 한다.

    표본 육안 검증에서 채점기 오판이 나오면 정답 키워드를 고쳐야 하는데,
    최소 사양 실기에서 3종 25문항 재측정은 26분이 걸린다. 답변 원문은 결과
    JSON에 그대로 있으므로 채점만 다시 돌리는 편이 정확하고 빠르다.
    """
    try:
        data = load_result(result_path)
        testset = load_testset(testset_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"오류: 결과 파일을 읽지 못했습니다({result_path}): {exc}", file=sys.stderr)
        return 1
    except TestsetError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    by_id = {case.id: case for case in testset.cases}
    reports = []
    for raw in data.get("reports", []):
        report = ModelReport(
            key=raw.get("key", ""), label=raw.get("label", "?"),
            size_gb=raw.get("size_gb", 0.0), load_seconds=raw.get("load_seconds", 0.0),
            memory_peak_mb=raw.get("memory_peak_mb"), failed=raw.get("failed", ""),
        )
        for outcome in raw.get("outcomes", []):
            case = by_id.get(outcome["case_id"])
            if case is None:
                print(f"경고: 테스트셋에 없는 케이스는 건너뜁니다: {outcome['case_id']}",
                      file=sys.stderr)
                continue
            report.outcomes.append(CaseOutcome(
                case_id=case.id, question=case.question,
                expect_abstain=case.expect_abstain, answer=outcome["answer"],
                abstained=is_abstention(outcome["answer"]),
                keywords_hit=contains_keywords(outcome["answer"], list(case.keywords)),
                elapsed_sec=outcome.get("elapsed_sec", 0.0),
                completion_tokens=outcome.get("completion_tokens", 0),
                error=outcome.get("error", ""),
            ))
        reports.append(report)

    print(f"재채점: {result_path} × {testset.source}")
    print_report(reports)

    if out_path:
        data["reports"] = [report_to_dict(r) for r in reports]
        data["rescored_from"] = result_path
        Path(out_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n재채점 결과: {out_path}")
    return 0


def print_comparison(paths: list[str]) -> int:
    """여러 PC에서 나온 결과 파일을 한 표로 합친다 (T6.7 비교표).

    최소 사양 PC와 권장 사양 PC는 물리적으로 다른 기계라 한 번에 잴 수 없다.
    각자 돌린 결과 JSON을 모아 여기서 합친다 — 어느 기계 수치인지 함께 보여야
    사양별 채택 모델을 판단할 수 있다.
    """
    rows = []
    for path in paths:
        try:
            data = load_result(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"오류: 결과 파일을 읽지 못했습니다({path}): {exc}", file=sys.stderr)
            return 1
        env = data.get("environment", {})
        machine = data.get("label") or env.get("cpu", "?")
        for report in data.get("reports", []):
            rows.append((machine, report))

    if not rows:
        print("오류: 합칠 결과가 없습니다.", file=sys.stderr)
        return 1

    print("=" * 110)
    print(f"{'측정 환경':<26}{'모델':<22}{'크기':>7}{'기권정확도':>11}{'과잉기권':>10}"
          f"{'응답정확도':>11}{'중앙지연':>10}{'메모리':>10}")
    print("-" * 110)
    for machine, report in rows:
        label = machine if len(machine) <= 24 else machine[:23] + "…"
        if report.get("failed"):
            print(f"{label:<26}{report['label']:<22}{report['size_gb']:>6.2f}G   "
                  f"측정 실패: {report['failed'][:40]}")
            continue
        metrics = report.get("metrics", {})
        memory = report.get("memory_peak_mb")
        print(
            f"{label:<26}{report['label']:<22}{report['size_gb']:>6.2f}G"
            f"{_pct(metrics.get('abstain_accuracy')):>11}"
            f"{_pct(metrics.get('over_abstain_rate')):>10}"
            f"{_pct(metrics.get('answer_accuracy')):>11}"
            f"{(metrics.get('median_sec') or 0):>9.1f}s"
            f"{(f'{memory:,.0f}MB' if memory else '   -  '):>10}"
        )
    print("=" * 110)
    return 0


def _environment() -> dict:
    available = runtime.available_ram_gb()
    return {
        "cpu": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "available_ram_gb": round(available, 2) if available else None,
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.benchmark_slm")
    parser.add_argument("--models", nargs="*", choices=SLM_ORDER, default=None,
                        help="측정할 후보 (생략하면 설치된 것 전부)")
    parser.add_argument("--testset", default=DEFAULT_TESTSET)
    parser.add_argument("--db", default=DEFAULT_DB, help="chunk_ids를 풀 인덱스 DB")
    parser.add_argument("--n-ctx", type=int, default=4096)
    parser.add_argument("--threads", type=int, default=None,
                        help="추론 스레드 수 (고정해야 재현 가능하다)")
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--limit", type=int, default=None, help="문항 수 제한(빠른 확인용)")
    parser.add_argument("--show", type=int, default=0, help="표본 육안 검증용 출력 건수")
    parser.add_argument("--out", default=None, help="원시 결과 JSON 경로")
    parser.add_argument("--label", default=None,
                        help="측정 환경 이름 (예: '최소사양 i5-8265U'). 비교표에 그대로 쓰인다")
    parser.add_argument("--compare", nargs="+", default=None, metavar="RESULT.json",
                        help="여러 PC의 결과 JSON을 한 표로 합친다 (측정은 하지 않는다)")
    parser.add_argument("--rescore", default=None, metavar="RESULT.json",
                        help="저장된 답변을 다시 추론하지 않고 채점만 다시 한다")
    args = parser.parse_args(argv)

    if args.compare:
        return print_comparison(args.compare)

    if args.rescore:
        return rescore(args.rescore, args.testset, args.out)

    if runtime.find_llama_server() is None:
        print("오류: llama-server가 없습니다. `python -m scripts.setup_llamacpp`를 먼저 실행하세요.",
              file=sys.stderr)
        return 1

    try:
        testset = load_testset(args.testset)
    except TestsetError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    cases = testset.cases[: args.limit] if args.limit else testset.cases

    if args.models:
        profiles = [get_slm_profile(k) for k in args.models]
    else:
        profiles = [p for p in SLM_CANDIDATES if p.is_installed()]
    missing = [p.key for p in profiles if not p.is_installed()]
    if missing:
        print(f"오류: 미설치 모델: {', '.join(missing)} — `python -m slm.download {missing[0]}`",
              file=sys.stderr)
        return 1
    if not profiles:
        print("오류: 설치된 모델이 없습니다. `python -m slm.download exaone-4.0-1.2b`",
              file=sys.stderr)
        return 1

    needs_db = any(case.chunk_ids for case in cases)
    conn = connect(args.db) if needs_db and Path(args.db).is_file() else None
    if needs_db and conn is None:
        print(f"오류: chunk_ids를 풀 인덱스 DB가 없습니다: {args.db}", file=sys.stderr)
        return 1

    environment = _environment()
    print("=" * 96)
    print(f"실행 환경 : {environment['cpu']} / Python {environment['python']}")
    print(f"            여유 RAM {environment['available_ram_gb']}GB, "
          f"스레드 {args.threads or '자동'}, n_ctx {args.n_ctx}")
    print(f"테스트셋  : {testset.source} — {len(cases)}문항 "
          f"(근거 있음 {sum(not c.expect_abstain for c in cases)} / "
          f"근거 없음 {sum(c.expect_abstain for c in cases)})")
    print("=" * 96)

    reports = []
    try:
        for profile in profiles:
            print(f"\n▶ {profile.label} ({profile.size_gb:.2f} GB)")
            started = time.perf_counter()
            report = benchmark_model(
                profile, cases, conn,
                n_ctx=args.n_ctx, n_threads=args.threads, max_tokens=args.max_tokens,
            )
            reports.append(report)
            print(f"  소요 {time.perf_counter() - started:.0f}초")
    finally:
        if conn is not None:
            conn.close()

    print_report(reports)
    if args.show:
        print_samples(reports, args.show)

    out_path = Path(args.out) if args.out else (
        DEFAULT_OUT_DIR / f"slm_benchmark_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "environment": environment,
                "label": args.label,
                "testset": testset.source,
                "settings": {
                    "n_ctx": args.n_ctx, "threads": args.threads,
                    "max_tokens": args.max_tokens,
                },
                "reports": [report_to_dict(r) for r in reports],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n원시 결과: {out_path} (실문서 인용이 섞일 수 있어 커밋하지 않는다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""벤치마크 채점·사전 점검·비교표 (T6.5/T6.6/T6.7).

모델 없이 검증 가능한 부분만 다룬다 — 실제 측정은 `scripts/benchmark_slm.py`를 직접 돌린다.
"""

from __future__ import annotations

import json

import pytest

from scripts.benchmark_slm import (
    CaseOutcome,
    ModelReport,
    check_prompt_delivery,
    print_comparison,
    report_to_dict,
)
from slm.prompt import ABSTAIN_TEXT


class _FakeClient:
    """`apply_template`만 흉내 내는 스텁."""

    def __init__(self, rendered: str) -> None:
        self._rendered = rendered

    def apply_template(self, _messages) -> str:
        return self._rendered


# --- 프롬프트 도달 사전 점검 ----------------------------------------------

def test_prompt_delivery_ok_when_rules_present():
    assert check_prompt_delivery(_FakeClient(f"...{ABSTAIN_TEXT}...")) == ""


def test_prompt_delivery_detects_dropped_rules():
    """EXAONE-4.0처럼 system을 버리는 템플릿을 측정 전에 잡아낸다."""
    problem = check_prompt_delivery(_FakeClient("[|user|]\n질문만 남았다"))
    assert "사라졌습니다" in problem


def test_prompt_delivery_skips_when_endpoint_missing():
    """엔드포인트가 없는 서버 버전에서는 점검을 건너뛰되 측정은 막지 않는다."""
    assert check_prompt_delivery(_FakeClient("")) == ""


# --- 지표 계산 -------------------------------------------------------------

def _outcome(case_id: str, *, expect_abstain: bool, abstained: bool,
             keywords_hit: bool = False, elapsed: float = 1.0) -> CaseOutcome:
    return CaseOutcome(
        case_id=case_id, question="질문", expect_abstain=expect_abstain,
        answer="답", abstained=abstained, keywords_hit=keywords_hit,
        elapsed_sec=elapsed, completion_tokens=10,
    )


@pytest.fixture
def report() -> ModelReport:
    return ModelReport(
        key="m", label="테스트 모델", size_gb=1.0,
        load_seconds=2.0,
        outcomes=[
            # 근거 있는 질문 4건 — 1건은 잘못 기권, 2건은 정답 키워드 적중
            _outcome("g1", expect_abstain=False, abstained=False, keywords_hit=True),
            _outcome("g2", expect_abstain=False, abstained=False, keywords_hit=True),
            _outcome("g3", expect_abstain=False, abstained=False, keywords_hit=False),
            _outcome("g4", expect_abstain=False, abstained=True),
            # 근거 없는 질문 2건 — 1건만 올바르게 기권
            _outcome("u1", expect_abstain=True, abstained=True),
            _outcome("u2", expect_abstain=True, abstained=False),
        ],
    )


def test_abstain_accuracy(report):
    assert report.abstain_accuracy == 0.5


def test_over_abstain_rate(report):
    assert report.over_abstain_rate == 0.25


def test_answer_accuracy(report):
    assert report.answer_accuracy == 0.5


def test_metrics_are_none_without_cases():
    empty = ModelReport(key="m", label="라벨", size_gb=1.0)
    assert empty.abstain_accuracy is None
    assert empty.over_abstain_rate is None
    assert empty.answer_accuracy is None


def test_report_to_dict_includes_computed_metrics(report):
    """`asdict`만 쓰면 property가 빠져 리포트를 다시 계산해야 한다."""
    data = report_to_dict(report)
    assert data["metrics"]["abstain_accuracy"] == 0.5
    assert data["metrics"]["over_abstain_rate"] == 0.25
    assert len(data["outcomes"]) == 6


# --- 비교표 ---------------------------------------------------------------

def _write_result(path, label, model_label, metrics):
    path.write_text(json.dumps({
        "environment": {"cpu": "어떤 CPU"},
        "label": label,
        "reports": [{
            "label": model_label, "size_gb": 0.81, "failed": "",
            "memory_peak_mb": 1680.0, "metrics": metrics,
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_comparison_merges_results_from_two_machines(tmp_path, capsys):
    """최소 사양과 권장 사양은 다른 기계라 결과 파일을 합쳐야 한다."""
    a = _write_result(tmp_path / "min.json", "최소사양 i5-8265U", "EXAONE 4.0 1.2B",
                      {"abstain_accuracy": 1.0, "over_abstain_rate": 0.5,
                       "answer_accuracy": 0.43, "median_sec": 5.3})
    b = _write_result(tmp_path / "rec.json", "권장사양 Core Ultra 5", "EXAONE 3.5 7.8B",
                      {"abstain_accuracy": 0.9, "over_abstain_rate": 0.1,
                       "answer_accuracy": 0.8, "median_sec": 12.0})

    assert print_comparison([a, b]) == 0
    out = capsys.readouterr().out
    assert "최소사양 i5-8265U" in out
    assert "권장사양 Core Ultra 5" in out
    assert "EXAONE 4.0 1.2B" in out and "EXAONE 3.5 7.8B" in out


def test_comparison_falls_back_to_cpu_when_label_missing(tmp_path, capsys):
    path = _write_result(tmp_path / "r.json", None, "모델", {"median_sec": 1.0})
    assert print_comparison([path]) == 0
    assert "어떤 CPU" in capsys.readouterr().out


def test_comparison_shows_failed_runs(tmp_path, capsys):
    path = tmp_path / "fail.json"
    path.write_text(json.dumps({
        "environment": {"cpu": "CPU"}, "label": "최소사양",
        "reports": [{"label": "EXAONE 3.5 7.8B", "size_gb": 4.77,
                     "failed": "메모리가 부족합니다", "metrics": {}}],
    }, ensure_ascii=False), encoding="utf-8")

    assert print_comparison([str(path)]) == 0
    assert "측정 실패" in capsys.readouterr().out


def test_comparison_reports_unreadable_file(tmp_path, capsys):
    assert print_comparison([str(tmp_path / "없음.json")]) == 1


# --- 재채점 ---------------------------------------------------------------

def _write_rescore_inputs(tmp_path, keywords, answer):
    testset = tmp_path / "testset.json"
    testset.write_text(json.dumps({"cases": [
        {"id": "g1", "question": "질문", "expect_abstain": False, "keywords": keywords},
        {"id": "u1", "question": "질문2", "expect_abstain": True},
    ]}, ensure_ascii=False), encoding="utf-8")

    result = tmp_path / "result.json"
    result.write_text(json.dumps({
        "environment": {"cpu": "CPU"},
        "reports": [{
            "key": "m", "label": "모델", "size_gb": 1.0, "load_seconds": 1.0,
            "memory_peak_mb": 100.0, "failed": "",
            "outcomes": [
                {"case_id": "g1", "answer": answer, "elapsed_sec": 1.0,
                 "completion_tokens": 5, "error": ""},
                {"case_id": "u1", "answer": ABSTAIN_TEXT, "elapsed_sec": 1.0,
                 "completion_tokens": 5, "error": ""},
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    return str(result), str(testset)


def test_rescore_applies_corrected_keywords(tmp_path, capsys):
    """25문항 × 3종 재측정은 26분이 걸린다 — 답변은 그대로 두고 채점만 다시 한다."""
    from scripts.benchmark_slm import rescore

    answer = "고객은 언제든지 코칭을 종료할 수 있습니다. [1]"
    result, testset = _write_rescore_inputs(tmp_path, [["어느 시점", "언제든지"]], answer)
    out = tmp_path / "rescored.json"

    assert rescore(result, testset, str(out)) == 0
    assert "100.0%" in capsys.readouterr().out  # 응답 정확도 1/1

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["reports"][0]["metrics"]["answer_accuracy"] == 1.0
    assert data["rescored_from"] == result


def test_rescore_marks_miss_when_keyword_absent(tmp_path, capsys):
    from scripts.benchmark_slm import rescore

    result, testset = _write_rescore_inputs(tmp_path, ["회계연도"], "엉뚱한 답")
    assert rescore(result, testset, None) == 0
    assert "  0.0%" in capsys.readouterr().out


def test_rescore_reports_missing_file(tmp_path, capsys):
    from scripts.benchmark_slm import rescore

    _, testset = _write_rescore_inputs(tmp_path, ["x"], "답")
    assert rescore(str(tmp_path / "없음.json"), testset, None) == 1

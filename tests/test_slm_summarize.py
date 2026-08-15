"""4단계 안전장치 테스트 (T7.1~T7.4).

모델을 띄우지 않는다 — 네 단계 모두 순수 로직이거나 클라이언트 스텁으로
검증 가능하다. 실제 추론이 필요한 종단 확인은 `test_slm_service.py`의
`@pytest.mark.slow` 쪽에 있다.
"""

from __future__ import annotations

import pytest

from config.settings import SIMILARITY_THRESHOLD
from parser.schema import ChunkType
from indexer.fts5.search import SearchResult
from search.hybrid_search import HybridResult
from slm.prompt import Excerpt, expand_citations
from slm.summarize import (
    NO_EVIDENCE_TEXT,
    Summary,
    SummaryStatus,
    select_excerpts,
    summarize,
)
from slm.verify import find_invalid_citations, overlap_ratio, verify_answer

EVIDENCE = "DNS에서는 도메인명을 분산된 트리 형태의 계층적 구조로 관리한다."


def _result(file_name="리눅스마스터.pdf", page=13, content=EVIDENCE) -> SearchResult:
    return SearchResult(
        chunk_id="c1",
        doc_id="d1",
        file_path=r"D:\문서\리눅스마스터.pdf",
        file_name=file_name,
        type=ChunkType.TEXT,
        page_or_slide=page,
        content=content,
        caption="",
        score=-1.5,
    )


def _hybrid(similarity=0.8, **kwargs) -> HybridResult:
    low = similarity is not None and similarity < SIMILARITY_THRESHOLD
    return HybridResult(_result(**kwargs), similarity, low)


def _excerpts() -> list[Excerpt]:
    return [Excerpt("리눅스마스터.pdf", "13페이지", EVIDENCE)]


class _FakeService:
    """`SlmService`의 chat만 흉내 낸다."""

    def __init__(self, text="", raises=None) -> None:
        self.text = text
        self.raises = raises
        self.calls: list[list[dict]] = []

    def is_running(self) -> bool:
        return True

    def chat(self, messages, **_kwargs):
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        from slm.client import Completion

        return Completion(text=self.text, elapsed_sec=1.0, completion_tokens=10)


# --- 1단계: 유사도 임계값 -------------------------------------------------


class TestGate1SimilarityThreshold:
    def test_keeps_results_at_or_above_threshold(self):
        excerpts = select_excerpts([_hybrid(0.8), _hybrid(0.5)])
        assert len(excerpts) == 2

    def test_drops_results_below_threshold(self):
        assert select_excerpts([_hybrid(0.49)]) == []

    def test_drops_results_without_similarity(self):
        """임베딩을 못 써 재순위가 없었던 결과는 근거로 쓰지 않는다.

        관련성을 판단할 수단이 없는데 요약하면 1단계의 취지가 무너진다.
        """
        assert select_excerpts([_hybrid(None)]) == []

    def test_limits_excerpt_count(self):
        excerpts = select_excerpts([_hybrid(0.9) for _ in range(10)], max_excerpts=3)
        assert len(excerpts) == 3

    def test_summarize_does_not_call_model_without_evidence(self):
        """**핵심**: 근거가 없으면 sLM을 아예 부르지 않아야 한다."""
        service = _FakeService(text="아무 말이나")
        summary = summarize("질문", [_hybrid(0.1)], service)

        assert summary.status is SummaryStatus.NO_EVIDENCE
        assert summary.text == NO_EVIDENCE_TEXT
        assert service.calls == []  # 호출 자체가 없었다


# --- 2단계: 근거 강제 프롬프트 --------------------------------------------


class TestGate2Prompt:
    def test_prompt_carries_rules_and_excerpt(self):
        service = _FakeService(text="DNS는 트리 구조로 관리합니다. [1]")
        summarize("DNS 구조는?", [_hybrid(0.8)], service)

        (messages,) = service.calls
        body = "\n".join(m["content"] for m in messages)
        assert "문서에서 찾을 수 없습니다" in body  # 기권 규칙이 실렸다
        assert EVIDENCE in body  # 발췌가 실렸다
        assert "DNS 구조는?" in body

    def test_abstention_is_reported_as_such(self):
        service = _FakeService(text="문서에서 찾을 수 없습니다.")
        summary = summarize("없는 내용", [_hybrid(0.8)], service)
        assert summary.status is SummaryStatus.ABSTAINED

    def test_empty_response_is_a_failure_not_a_silent_pass(self):
        """빈 응답은 Qwen3.5 thinking 모드를 안 껐을 때의 증상이다(Phase 6)."""
        summary = summarize("질문", [_hybrid(0.8)], _FakeService(text="   "))
        assert summary.status is SummaryStatus.FAILED
        assert "빈 응답" in summary.error

    def test_model_error_becomes_failed_summary(self):
        service = _FakeService(raises=RuntimeError("서버 죽음"))
        summary = summarize("질문", [_hybrid(0.8)], service)
        assert summary.status is SummaryStatus.FAILED
        assert "서버 죽음" in summary.error


# --- 대화 이력 (T10.17) -----------------------------------------------------


class TestConversationHistory:
    def test_history_is_forwarded_into_the_prompt(self):
        from slm.prompt import HistoryTurn

        service = _FakeService(text="새 답변입니다. [1]")
        history = [HistoryTurn(question="이전 질문", answer="이전 답변 [1]")]

        summarize("이번 질문", [_hybrid(0.8)], service, history=history)

        (messages,) = service.calls
        body = "\n".join(m["content"] for m in messages)
        assert "이전 질문" in body
        assert "이전 답변 [1]" in body

    def test_default_history_does_not_change_the_prompt(self):
        """기존(이력 미사용) 호출부와 완전히 같은 프롬프트가 나가야 한다 —
        Phase 6/7 실측치가 이 경로 기준이다."""
        with_history_default = _FakeService(text="답 [1]")
        without_history_arg = _FakeService(text="답 [1]")

        summarize("질문", [_hybrid(0.8)], with_history_default)
        summarize("질문", [_hybrid(0.8)], without_history_arg, history=[])

        assert with_history_default.calls == without_history_arg.calls


# --- 3단계: 출처 표기 -----------------------------------------------------


class TestGate3Citations:
    def test_expands_number_to_file_and_location(self):
        out = expand_citations("DNS는 트리 구조입니다. [1]", _excerpts())
        assert out == "DNS는 트리 구조입니다. [리눅스마스터.pdf, 13페이지]"

    def test_leaves_out_of_range_citation_untouched(self):
        """지우면 화면상 멀쩡해 보이고, 4단계가 잡을 근거도 사라진다."""
        out = expand_citations("정답은 IaaS입니다. [5]", _excerpts())
        assert "[5]" in out

    def test_omits_location_when_unknown(self):
        excerpts = [Excerpt("메모.txt", "-", "내용")]
        assert expand_citations("답 [1]", excerpts) == "답 [메모.txt]"

    def test_summary_text_is_expanded(self):
        service = _FakeService(text="DNS는 트리 구조입니다. [1]")
        summary = summarize("DNS?", [_hybrid(0.8)], service)
        assert "[리눅스마스터.pdf, 13페이지]" in summary.text


# --- 4단계: 겹침도 검증 ---------------------------------------------------


class TestGate4Overlap:
    def test_paraphrase_of_evidence_scores_high(self):
        """조사·어미가 달라도 문자 n-gram이면 잡힌다 (한국어 활용 차이)."""
        assert overlap_ratio("트리 형태의 계층적 구조로 관리합니다", EVIDENCE) > 0.6

    def test_unrelated_sentence_scores_low(self):
        assert overlap_ratio("정답은 IaaS입니다", EVIDENCE) < 0.3

    def test_flags_fabricated_answer(self):
        """Phase 6 실측 실패 재현 — 발췌에 없는 답을 근거 번호까지 붙여 지어냈다."""
        result = verify_answer("50번 문항의 정답은 IaaS 입니다. [1]", _excerpts())
        assert result.needs_review is True
        assert result.weak_sentences

    def test_accepts_grounded_answer(self):
        result = verify_answer("DNS는 트리 형태의 계층적 구조로 관리합니다. [1]", _excerpts())
        assert result.needs_review is False

    def test_flags_out_of_range_citation(self):
        result = verify_answer("DNS는 트리 형태의 계층적 구조로 관리합니다. [7]", _excerpts())
        assert result.needs_review is True
        assert result.invalid_citations == [7]

    def test_abstention_is_not_flagged(self):
        """기권에는 지어낸 내용이 없다 — 검증하면 무조건 걸린다."""
        result = verify_answer("문서에서 찾을 수 없습니다.", _excerpts())
        assert result.needs_review is False

    def test_find_invalid_citations_dedupes(self):
        assert find_invalid_citations("[9] 어쩌고 [9] 저쩌고 [1]", 2) == [9]

    def test_reason_explains_why(self):
        result = verify_answer("정답은 GTK+ 입니다. [7]", _excerpts())
        assert "근거 번호" in result.reason

    def test_summary_exposes_needs_review(self):
        service = _FakeService(text="50번 문항의 정답은 IaaS 입니다. [1]")
        summary = summarize("50번 정답은?", [_hybrid(0.8)], service)
        assert summary.status is SummaryStatus.OK
        assert summary.needs_review is True

    def test_grounded_summary_is_not_flagged(self):
        service = _FakeService(text="DNS는 트리 형태의 계층적 구조로 관리합니다. [1]")
        summary = summarize("DNS?", [_hybrid(0.8)], service)
        assert summary.needs_review is False


class TestSummaryDefaults:
    def test_needs_review_is_false_without_verification(self):
        assert Summary(status=SummaryStatus.NO_EVIDENCE).needs_review is False

    @pytest.mark.parametrize("status", list(SummaryStatus))
    def test_every_status_has_a_review_reason(self, status):
        """카드가 어떤 상태에서도 reason을 물어볼 수 있어야 한다 (AttributeError 방지)."""
        assert Summary(status=status).review_reason == ""

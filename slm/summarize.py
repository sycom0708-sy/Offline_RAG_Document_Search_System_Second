"""AI 요약 생성 — 4단계 안전장치 통합 (T7.1~T7.4).

TECH 5.3의 네 단계를 한 흐름으로 엮는다:

    1. 유사도 임계값 미달이면 **sLM을 부르지 않는다**        → `select_excerpts()`
    2. 근거 강제 프롬프트 + temperature 0                    → `slm/prompt.py` 재사용
    3. 문장별 출처 표기 `[파일명, 위치]`                      → `expand_citations()`
    4. 답변-근거 겹침도 → "확인 필요"                        → `slm/verify.py`

UI는 이 모듈의 `summarize()` 하나만 부르면 되고, 결과 `Summary`의 상태값을
보고 카드를 그린다. Qt에 의존하지 않으므로 워커 스레드에서도, 테스트에서도,
회귀 측정 스크립트에서도 같은 경로를 탄다 — Phase 6 수치와 대조할 수 있는
근거가 이 동일 경로에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from config.settings import SIMILARITY_THRESHOLD
from slm.prompt import (
    Excerpt,
    build_messages,
    clean_answer,
    expand_citations,
    is_abstention,
    to_excerpts,
)
from slm.service import SlmNotInstalledError, SlmService
from slm.verify import VerificationResult, verify_answer

# 프롬프트에 실을 발췌 개수 상한. n_ctx 4096에 발췌당 최대 1200자
# (`prompt.DEFAULT_MAX_CHARS_PER_EXCERPT`)라 5건이면 컨텍스트에 여유가 있다.
# Phase 6 측정도 이 규모였다.
DEFAULT_MAX_EXCERPTS = 5

NO_EVIDENCE_TEXT = "관련 문서를 찾을 수 없습니다"


class SummaryStatus(Enum):
    """요약 1건의 최종 상태. UI 카드가 이 값으로 분기한다."""

    OK = "ok"                    # 답변 생성됨 (needs_review는 별도 필드)
    ABSTAINED = "abstained"      # 모델이 근거 없음을 인정하고 기권
    NO_EVIDENCE = "no_evidence"  # 1단계에서 차단 — sLM을 부르지 않았다
    FAILED = "failed"            # 모델 미설치·기동 실패·추론 오류


@dataclass
class Summary:
    """요약 결과 한 건."""

    status: SummaryStatus
    text: str = ""
    excerpts: list[Excerpt] = field(default_factory=list)
    verification: VerificationResult | None = None
    elapsed_sec: float = 0.0
    error: str = ""

    @property
    def needs_review(self) -> bool:
        """4단계가 "확인 필요"로 판정했는가."""
        return bool(self.verification and self.verification.needs_review)

    @property
    def review_reason(self) -> str:
        return self.verification.reason if self.verification else ""


def select_excerpts(
    results,
    *,
    threshold: float = SIMILARITY_THRESHOLD,
    max_excerpts: int = DEFAULT_MAX_EXCERPTS,
) -> list[Excerpt]:
    """1단계 안전장치 — 근거로 쓸 만한 결과만 골라 발췌로 바꾼다.

    `HybridResult.similarity`가 이미 계산돼 있으므로(`search/hybrid_search.py`)
    같은 상수(`SIMILARITY_THRESHOLD`, 0.5)로 거른다. 화면에서 "관련성 낮음"으로
    흐리게 표시되는 카드(DESIGN §5.6)와 **같은 기준**이어야 한다 — 사용자가
    흐리게 본 문서가 요약의 근거로 쓰이면 설명할 수 없다.

    `similarity`가 None인 결과(임베딩 미설치·실패로 재순위를 못 한 경우)는
    **부적격으로 본다.** 근거의 관련성을 판단할 수단이 없는데 요약하면 1단계의
    취지가 그대로 무너지기 때문이다 — 이때는 추출형 검색 결과만 보여주면 된다.
    """
    eligible = [
        r for r in results
        if getattr(r, "similarity", None) is not None and r.similarity >= threshold
    ]
    return to_excerpts(eligible[:max_excerpts])


def summarize(
    question: str,
    results,
    service: SlmService,
    *,
    threshold: float = SIMILARITY_THRESHOLD,
    max_excerpts: int = DEFAULT_MAX_EXCERPTS,
) -> Summary:
    """검색 결과를 근거로 질문에 답한다. 4단계 안전장치를 모두 통과시킨다."""
    excerpts = select_excerpts(results, threshold=threshold, max_excerpts=max_excerpts)

    # --- 1단계: 근거가 없으면 모델을 부르지 않는다 ---
    if not excerpts:
        return Summary(status=SummaryStatus.NO_EVIDENCE, text=NO_EVIDENCE_TEXT)

    return summarize_excerpts(question, excerpts, service)


def summarize_excerpts(
    question: str,
    excerpts: list[Excerpt],
    service: SlmService,
) -> Summary:
    """이미 확정된 발췌로 2~4단계만 돈다.

    1단계는 검색 유사도를 보는 단계라 근거가 검색이 아닌 경로로 정해지는
    경우(회귀 측정은 테스트셋의 `chunk_ids`로 근거를 직접 지정한다)에는
    적용할 대상이 없다. 그 경우에도 **나머지 세 단계는 앱과 완전히 같은
    코드**를 지나야 측정이 의미가 있다.
    """
    # --- 2단계: 근거 강제 프롬프트 + temperature 0 (service.chat이 고정) ---
    messages = build_messages(question, excerpts)
    try:
        completion = service.chat(messages)
    except SlmNotInstalledError as exc:
        return Summary(status=SummaryStatus.FAILED, excerpts=excerpts, error=str(exc))
    except Exception as exc:  # 기동 실패·타임아웃·HTTP 오류 등
        return Summary(
            status=SummaryStatus.FAILED,
            excerpts=excerpts,
            error=f"AI 요약을 생성하지 못했습니다: {exc}",
        )

    answer = clean_answer(completion.text)
    if not answer:
        # 빈 응답은 사실상 실패다. Qwen3.5의 thinking 모드를 안 껐을 때 나오는
        # 증상이기도 해서(Phase 6), 조용히 넘기면 원인 추적이 어려워진다.
        return Summary(
            status=SummaryStatus.FAILED,
            excerpts=excerpts,
            elapsed_sec=completion.elapsed_sec,
            error="AI 모델이 빈 응답을 돌려줬습니다.",
        )

    if is_abstention(answer):
        return Summary(
            status=SummaryStatus.ABSTAINED,
            text=answer,
            excerpts=excerpts,
            elapsed_sec=completion.elapsed_sec,
        )

    # --- 4단계: 치환 **전** 원문으로 검증한다 ---
    # `[1]` 번호가 살아 있어야 범위 밖 인용을 잡을 수 있다.
    verification = verify_answer(answer, excerpts, threshold=_overlap_threshold())

    # --- 3단계: 출처 표기로 치환 ---
    displayed = expand_citations(answer, excerpts)

    return Summary(
        status=SummaryStatus.OK,
        text=displayed,
        excerpts=excerpts,
        verification=verification,
        elapsed_sec=completion.elapsed_sec,
    )


def _overlap_threshold() -> float:
    """겹침도 임계값을 호출 시점에 읽는다 (테스트에서 monkeypatch 가능하도록)."""
    from config import settings

    return settings.SLM_OVERLAP_THRESHOLD

"""4단계 안전장치 — 답변과 근거의 겹침도 사후 검증 (T7.4).

TECH 5.3의 마지막 방어선이다. 앞의 세 단계(임계값 필터 · 근거 강제 프롬프트 ·
출처 표기)를 다 통과하고도 모델이 근거 밖의 말을 지어낼 수 있다는 전제에서
출발한다 — **Phase 6 실측에서 실제로 그랬다.** 채택 모델(Qwen3.5-4B)과
비교 대상(EXAONE-3.5-7.8B) 둘 다, 보기만 있고 정답은 적혀 있지 않은 객관식
발췌에 대해 "정답은 IaaS입니다 [2]"처럼 **자기 지식으로 답을 채우고 근거
번호까지 붙였다**(26문항 중 2건, 두 모델이 같은 문항에서 같은 방식으로).

그래서 판정을 모델에게 묻지 않고 **문자열만으로 사후 계산**한다.

## 왜 문자 n-gram인가

한국어는 조사·어미가 붙어 어절 단위 비교가 잘 안 걸린다. 발췌에
"트리 형태의 계층적 구조로 관리한다"가 있고 답변이 "계층적 구조로 관리합니다"
여도 어절로는 "관리한다" ≠ "관리합니다"라 통째로 놓친다. 문자 bigram은 이런
활용 차이를 대부분 흡수한다.

형태소 분석기를 쓰면 더 정확하겠지만, Phase 3에서 `kss`를 성능 때문에
걷어낸 전례가 있고(53자/초) 이 계산은 요약 1건마다 도는 자리다. 지금은
의존성 0인 문자 n-gram으로 두고, 오판이 잦으면 그때 다시 본다.

## 이 판정은 근사치다

겹침도가 낮다고 반드시 틀린 답은 아니다(발췌를 옳게 요약·환언했을 수 있다).
그래서 답을 **숨기지 않고** "확인 필요"만 붙인다 — TECH 5.3의 설계 철학이
"할루시네이션을 100% 막는다"가 아니라 "사용자가 즉시 검증할 수 있는 구조"인
것과 같은 이유다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from config.settings import SLM_OVERLAP_THRESHOLD
from indexer.chunker import split_sentences
from slm.prompt import Excerpt, is_abstention

# 답변에 달린 근거 번호. `expand_citations()`가 치환하기 **전** 형태를 본다.
_CITATION = re.compile(r"\[(\d+)\]")

_WHITESPACE = re.compile(r"\s+")

# 겹침도 계산에서 뺄 문자들. 문장부호·괄호는 어느 문서에나 나와 실제 근거가
# 없는 문장의 점수를 올려준다.
_PUNCTUATION = re.compile(r"[^\w가-힣]+", re.UNICODE)

# n-gram 크기. 2가 활용 차이를 흡수하면서도 우연 일치가 적었다.
_NGRAM = 2

# 이보다 짧은 문장은 bigram이 몇 개 안 나와 비율이 요동친다 — 판정에서 뺀다
# ("네.", "그렇습니다." 같은 것).
_MIN_SENTENCE_CHARS = 8


@dataclass
class VerificationResult:
    """겹침도 검증 결과."""

    needs_review: bool
    # 겹침도가 기준 미만인 문장들 (사용자에게 보여줄 수도 있어 남긴다)
    weak_sentences: list[str] = field(default_factory=list)
    # 발췌 범위를 벗어난 근거 번호 (예: 발췌 3건인데 [5])
    invalid_citations: list[int] = field(default_factory=list)
    # 판정에 쓰인 문장별 겹침도 — 임계값을 조정할 때 근거가 된다
    scores: list[float] = field(default_factory=list)

    @property
    def reason(self) -> str:
        """"확인 필요"를 붙인 이유. 배지 툴팁에 쓴다."""
        reasons = []
        if self.invalid_citations:
            numbers = ", ".join(f"[{n}]" for n in self.invalid_citations)
            reasons.append(f"발췌에 없는 근거 번호를 인용했습니다({numbers})")
        if self.weak_sentences:
            reasons.append(
                f"근거 발췌와 겹치는 내용이 적은 문장이 {len(self.weak_sentences)}개 있습니다"
            )
        return " · ".join(reasons)


def _normalize(text: str) -> str:
    """공백·문장부호를 걷어낸 비교용 문자열."""
    return _PUNCTUATION.sub("", _WHITESPACE.sub("", text or ""))


def _ngrams(text: str, n: int = _NGRAM) -> set[str]:
    if len(text) < n:
        # 한 글자짜리는 그 자체를 단위로 본다 — 빈 집합이 되면 비율이 0/0이다.
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def overlap_ratio(sentence: str, evidence: str) -> float:
    """`sentence`의 문자 n-gram 중 `evidence`에도 있는 비율 (0.0~1.0).

    재현율 방향으로만 잰다 — 발췌가 답변보다 훨씬 길기 때문에 F1이나 정밀도를
    쓰면 짧고 정확한 답변이 부당하게 낮은 점수를 받는다.
    """
    sentence_grams = _ngrams(_normalize(sentence))
    if not sentence_grams:
        return 1.0  # 비교할 것이 없으면 문제 삼지 않는다
    evidence_grams = _ngrams(_normalize(evidence))
    if not evidence_grams:
        return 0.0
    return len(sentence_grams & evidence_grams) / len(sentence_grams)


def find_invalid_citations(answer: str, excerpt_count: int) -> list[int]:
    """발췌 범위를 벗어난 근거 번호를 찾는다.

    발췌가 3건인데 `[5]`를 인용했다면 그 번호는 모델이 만들어낸 것이다 —
    출처 표기(3단계)가 치환할 대상도 없으므로 검증 실패로 본다.
    """
    seen = []
    for match in _CITATION.finditer(answer or ""):
        number = int(match.group(1))
        if (number < 1 or number > excerpt_count) and number not in seen:
            seen.append(number)
    return seen


def verify_answer(
    answer: str,
    excerpts: list[Excerpt],
    *,
    threshold: float = SLM_OVERLAP_THRESHOLD,
) -> VerificationResult:
    """답변이 근거에 실려 있는지 사후 검증한다.

    기권 응답은 검증하지 않는다 — 지어낸 내용이 없다는 뜻이므로 확인할 것이
    없고, 기권 문구가 발췌에 있을 리 없어 무조건 "확인 필요"가 되어버린다.
    """
    if not answer or not answer.strip():
        return VerificationResult(needs_review=False)
    if is_abstention(answer):
        return VerificationResult(needs_review=False)

    invalid = find_invalid_citations(answer, len(excerpts))

    evidence = "\n".join(e.text for e in excerpts)
    weak: list[str] = []
    scores: list[float] = []

    for sentence in split_sentences(answer):
        # 근거 번호는 발췌 본문에 없으므로 빼고 잰다 — 안 빼면 번호를 붙인
        # 문장이 그것만으로 점수를 잃는다.
        stripped = _CITATION.sub("", sentence).strip()
        if len(_normalize(stripped)) < _MIN_SENTENCE_CHARS:
            continue
        score = overlap_ratio(stripped, evidence)
        scores.append(score)
        if score < threshold:
            weak.append(sentence.strip())

    return VerificationResult(
        needs_review=bool(weak or invalid),
        weak_sentences=weak,
        invalid_citations=invalid,
        scores=scores,
    )

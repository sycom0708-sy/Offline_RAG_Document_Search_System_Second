"""근거 강제 프롬프트 (T6.3).

Phase 7 안전장치 2번이 그대로 재사용할 자산이다. 핵심은 **기권 문구를 고정**
하는 것 — 모델이 "잘 모르겠네요", "해당 내용은 없는 것 같습니다"처럼 매번
다르게 말하면 준수율을 자동 채점할 수 없다.

발췌 라벨(`[1] 파일명 · 위치`)은 UI 결과 카드와 같은 `format_location()`을 쓴다.
Phase 7의 출처 표기(T7.3)와 형식이 어긋나면 사용자가 답변의 출처를 카드에서
되짚지 못한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from indexer.fts5.search import SearchResult
from search.chunk_view import format_location

# 기권 시 모델이 **그대로** 출력해야 하는 문구. 채점기가 이 문자열을 찾는다.
ABSTAIN_TEXT = "문서에서 찾을 수 없습니다."

SYSTEM_PROMPT = f"""당신은 사내 문서 검색 도우미입니다. 아래 [발췌]는 당신이 가진 유일한 정보원입니다.

가장 중요한 규칙:
발췌에 답이 없으면, 당신이 그 답을 알고 있더라도 절대 답하지 마십시오.
이 경우 설명·사과·대안 제시 없이 **다음 한 문장만** 출력합니다.
{ABSTAIN_TEXT}

반대로 **발췌에 답이 있으면 반드시 답해야 합니다.** 표현이 질문과 달라도,
값이 "없음"이나 "해당 없음"이어도 그것이 답이면 그대로 답하십시오. 이 경우
3문장 이내로 간결하게 답하고 근거 번호를 [1] 형식으로 붙입니다. 발췌에 없는
배경 설명·일반 상식·추측은 덧붙이지 않습니다.

예시 1 (발췌에 답이 있다)
[발췌]
[1] 사양.xlsx · 사양표
구분 | 최소 사양 | 권장 사양
RAM | 8GB | 16GB
디스크 | 없음 | 없음
[질문] 권장 사양의 디스크 요구사항이 있나요?
[답변] 권장 사양의 디스크 요구사항은 "없음"입니다. [1]

예시 2 (같은 발췌지만 묻는 항목이 없다)
[발췌]
[1] 사양.xlsx · 사양표
구분 | 최소 사양 | 권장 사양
RAM | 8GB | 16GB
디스크 | 없음 | 없음
[질문] 권장 CPU 모델명은 무엇인가요?
[답변] {ABSTAIN_TEXT}

예시 3 (발췌가 없다)
[발췌]
(검색된 발췌 없음)
[질문] 대한민국의 수도는 어디인가요?
[답변] {ABSTAIN_TEXT}"""

USER_TEMPLATE = """[발췌]
{excerpts}

[질문]
{question}"""

NO_EXCERPT_TEXT = "(검색된 발췌 없음)"

# 발췌 1건이 지나치게 길면 뒤쪽 발췌가 컨텍스트에서 밀려난다. 청크 자체가
# 토큰 기준(Phase 3에서 문자 수 → 토큰 수로 바꿨다)으로 잘려 있어 보통은
# 걸리지 않지만, 표 청크는 셀이 많으면 길어질 수 있다.
DEFAULT_MAX_CHARS_PER_EXCERPT = 1200

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Excerpt:
    """프롬프트에 들어갈 발췌 1건."""

    file_name: str
    location: str
    text: str

    @classmethod
    def from_result(cls, result: SearchResult) -> "Excerpt":
        return cls(
            file_name=result.file_name,
            location=format_location(result),
            text=result.content,
        )


def to_excerpts(results) -> list[Excerpt]:
    """`SearchResult` 또는 `HybridResult` 목록을 발췌로 바꾼다."""
    # HybridResult는 SearchResult를 `.result`에 감싸고 있다.
    return [Excerpt.from_result(getattr(r, "result", r)) for r in results]


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …(생략)"


def format_excerpts(
    excerpts: list[Excerpt],
    *,
    max_chars_per_excerpt: int = DEFAULT_MAX_CHARS_PER_EXCERPT,
) -> str:
    """번호 붙은 발췌 블록을 만든다. 비어 있으면 그 사실을 명시한다.

    발췌가 하나도 없을 때 빈 문자열을 넣으면 모델이 규칙 2를 잊고 자체 지식으로
    답해버린다 — 근거가 없다는 것을 문장으로 알려줘야 한다.
    """
    if not excerpts:
        return NO_EXCERPT_TEXT

    blocks = []
    for index, excerpt in enumerate(excerpts, start=1):
        head = f"[{index}] {excerpt.file_name}"
        if excerpt.location and excerpt.location != "-":
            head += f" · {excerpt.location}"
        blocks.append(f"{head}\n{_truncate(excerpt.text, max_chars_per_excerpt)}")
    return "\n\n".join(blocks)


def build_messages(
    question: str,
    excerpts: list[Excerpt],
    *,
    max_chars_per_excerpt: int = DEFAULT_MAX_CHARS_PER_EXCERPT,
    use_system_role: bool = False,
) -> list[dict]:
    """`LlamaClient.chat()`에 그대로 넘길 messages를 만든다.

    **규칙을 system이 아니라 user 메시지에 담는 것이 기본이다.** EXAONE-4.0의
    chat template은 system 메시지를 **렌더링 단계에서 통째로 버린다**(실측:
    `/apply-template` 결과에 system 블록이 없고, 규칙을 넣은 요청과 맨 질문만
    던진 요청의 응답이 완전히 동일했다). 그 상태로 측정하면 "근거 강제 프롬프트를
    안 지키는 모델"로 잘못 결론 내리게 된다 — 실제로는 프롬프트가 도달조차
    안 한 것이다. 같은 규칙을 user 메시지에 넣자 지정 문구 그대로 기권했다.

    후보마다 템플릿이 다르므로 **전달을 보장하는 쪽**으로 통일한다. 비교용으로
    system 역할을 쓰고 싶으면 `use_system_role=True`.
    """
    body = USER_TEMPLATE.format(
        excerpts=format_excerpts(excerpts, max_chars_per_excerpt=max_chars_per_excerpt),
        question=question.strip(),
    )
    if use_system_role:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body},
        ]
    return [{"role": "user", "content": f"{SYSTEM_PROMPT}\n\n{body}"}]


# 예시(few-shot)의 `[답변]` 라벨을 모델이 그대로 따라 쓰는 일이 잦다(실측).
# 채점에는 지장이 없지만 Phase 7이 이 답변을 화면에 그대로 띄우므로 걷어낸다.
_ANSWER_PREFIX = re.compile(r"^\s*\[?\s*답변\s*\]?\s*[:：]?\s*")


def clean_answer(answer: str) -> str:
    """모델 답변에서 프롬프트 형식이 새어 나온 부분을 정리한다."""
    return _ANSWER_PREFIX.sub("", answer or "").strip()


def _normalize(text: str) -> str:
    return _WHITESPACE.sub("", text)


def is_abstention(answer: str) -> bool:
    """답변이 기권인지 판정한다.

    **근사치다.** 지정 문구가 들어 있기만 하면 기권으로 세므로, "…라고 나와
    있으나 상세 내용은 문서에서 찾을 수 없습니다"처럼 일부만 답한 경우도
    기권으로 잡힌다. 그래서 최종 선정 전에 표본을 육안으로 확인한다(계획 §②).
    """
    if not answer:
        return False
    normalized = _normalize(answer)
    # 마침표를 빼먹는 모델이 흔해 문구 본체로 찾는다.
    return _normalize(ABSTAIN_TEXT).rstrip(".") in normalized


def contains_keywords(answer: str, keywords) -> bool:
    """정답 키워드가 모두 답변에 있는지. 공백 차이는 무시한다.

    키워드 한 자리에 **동의어 목록**을 넣을 수 있다(`[["어느 시점", "언제든지"]]`)
    — 하나라도 맞으면 그 자리는 통과다. 표본 육안 검증에서 "어느 시점에서도"를
    "언제든지"로 옮긴 정확한 답을 오답으로 깎는 것이 확인돼 넣었다. 문구를 하나로
    못 박으면 채점기가 모델의 패러프레이즈 능력을 실력 부족으로 오인한다.
    """
    if not keywords:
        return False
    normalized = _normalize(answer)
    for slot in keywords:
        alternatives = [slot] if isinstance(slot, str) else list(slot)
        if not any(_normalize(alt) in normalized for alt in alternatives):
            return False
    return True

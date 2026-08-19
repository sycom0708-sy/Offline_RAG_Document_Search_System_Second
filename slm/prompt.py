"""근거 강제 프롬프트 (T6.3).

Phase 7 안전장치 2번이 그대로 재사용할 자산이다. 핵심은 **기권 문구를 고정**
하는 것 — 모델이 "잘 모르겠네요", "해당 내용은 없는 것 같습니다"처럼 매번
다르게 말하면 준수율을 자동 채점할 수 없다.

발췌 라벨(`[1] 파일명 · 위치`)은 UI 결과 카드와 같은 `format_location()`을 쓴다.
Phase 7의 출처 표기(T7.3)와 형식이 어긋나면 사용자가 답변의 출처를 카드에서
되짚지 못한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from indexer.fts5.search import SearchResult, query_term_variants
from parser.schema import ChunkType
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

NO_EXCERPT_TEXT = "(검색된 발췌 없음)"

# 발췌 1건이 지나치게 길면 뒤쪽 발췌가 컨텍스트에서 밀려난다. 청크 자체가
# 토큰 기준(Phase 3에서 문자 수 → 토큰 수로 바꿨다)으로 잘려 있어 보통은
# 걸리지 않지만, 표 청크는 셀이 많으면 길어질 수 있다.
DEFAULT_MAX_CHARS_PER_EXCERPT = 1200

# 대화 이력(T10.17)은 발췌보다 훨씬 짧게 잡는다 — n_ctx가 4096(slm/service.py)
# 뿐이라 발췌 5건(최대 6000자)에 이력까지 욱여넣으면 정작 이번 턴의 근거가
# 밀려날 수 있다. 최근 몇 턴만, 답변도 짧게 잘라 "무슨 얘기를 하던 중인지"
# 정도의 맥락만 준다.
DEFAULT_MAX_HISTORY_TURNS = 3
DEFAULT_MAX_CHARS_PER_HISTORY_ANSWER = 300

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Excerpt:
    """프롬프트에 들어갈 발췌 1건."""

    file_name: str
    location: str
    text: str

    @classmethod
    def from_result(
        cls, result: SearchResult, *, heading: str = "", query: str = ""
    ) -> "Excerpt":
        text = result.content
        if result.type == ChunkType.TABLE and getattr(result, "table_json", None):
            text = render_table_text(
                result.table_json, result.content, heading=heading, query=query
            )
        elif heading:
            text = f"{heading}\n\n{result.content}"
        return cls(
            file_name=result.file_name,
            location=format_location(result),
            text=text,
        )


@dataclass(frozen=True)
class HistoryTurn:
    """대화 이력 1턴 — 이전에 실제로 생성된 AI 답변만 담는다(T10.17).

    기권·근거없음·실패 턴은 넣지 않는다. 담을 만한 내용이 없고("문서에서
    찾을 수 없습니다"), 다음 답변에 참고할 정보가 아니라 노이즈만 된다.
    """

    question: str
    answer: str


def render_table_text(
    table_json: str | None,
    fallback: str,
    *,
    heading: str = "",
    query: str = "",
    max_chars: int = DEFAULT_MAX_CHARS_PER_EXCERPT,
) -> str:
    """표 청크를 행·열 구조가 보이는 형태로 만든다 (T10.25).

    🔴 평문으로 펼치면 **열 머리글이 셀에서 멀어져** 모델이 축을 뭉갠다.
    실측(2026-08-18, 사용자 보고): 등급×지원유형 2축 표에서 "KPC 응시자는
    40만원"처럼 **다른 등급의 열 값**을 등급 이름에 붙였다. 머리글을 각 행
    바로 위에 두면 모델이 할 일이 "읽어서 옮기기"로 줄어든다 — 같은 질문에서
    세 등급이 전부 정확히 나왔다.

    형식은 SYSTEM_PROMPT의 예시(`구분 | 최소 사양 | 권장 사양`)와 맞춘다.
    프롬프트 문구를 건드리지 않으므로 Phase 6·7 실측치가 그대로 유효하다.

    `max_chars`를 넘으면 **행 단위로** 자른다 — `format_excerpts`의 글자 수
    절단에 맡기면 행 중간이 잘려 "40만" 같은 반쪽 셀이 남는다.
    """
    try:
        table = json.loads(table_json) if table_json else None
    except (TypeError, ValueError):
        table = None
    if not isinstance(table, dict):
        return fallback

    rows = table.get("rows") or []
    header = [str(h).strip() for h in (table.get("header_row") or []) if str(h).strip()]
    if not rows and not header:
        return fallback

    def _cells(row):
        cells = row if isinstance(row, list) else row.get("cells", row)
        if not isinstance(cells, (list, tuple)):
            return []
        return [_WHITESPACE.sub(" ", str(c)).strip() for c in cells]

    lines = []
    if heading:
        lines.extend([heading, ""])
    if header:
        lines.append(" | ".join(header))

    body = []
    for row in rows:
        cells = _cells(row)
        if not any(cells):
            continue
        body.append(" | ".join(cells[: len(header)] if header else cells))

    # 🔴 질문과 관련된 행을 **먼저** 넣는다 (T10.27).
    #
    # 원래 순서대로 채우면 예산(`max_chars`)이 앞쪽 행에 다 쓰이고 정작 답이
    # 있는 행이 잘려나간다 — 실측: 응시료는 15~18행짜리 표의 9~11번째 행이라
    # 상시 위험했다. 반대로 관련 행이 앞으로 오면 표가 몇 행으로 줄어 프롬프트도
    # 크게 작아진다(프롬프트 처리가 지연의 93%다: 35.6ms/토큰 × 3,922토큰 = 140초).
    #
    # 조사 처리는 FTS5·재순위와 **같은 함수**를 쓴다 — 따로 구현하면 판정이
    # 갈려서 "검색에는 걸렸는데 발췌에는 없다"가 된다(T10.8의 교훈).
    if query:
        variants = query_term_variants(query)
        if variants:
            def _hit(line: str) -> bool:
                return any(any(form in line for form in forms) for forms in variants)

            hits = [line for line in body if _hit(line)]
            # 한 행도 안 걸리면(질문에 없는 말로 적힌 표) 원래대로 앞에서부터
            # 채운다 — 관련 행을 못 찾았다고 표를 통째로 비우면 답할 근거가
            # 사라진다. 걸린 게 있을 때만 그 행들로 좁힌다.
            if hits:
                body = hits

    used = sum(len(line) + 1 for line in lines)
    for line in body:
        if used + len(line) + 1 > max_chars and len(lines) > (2 if heading else 0):
            break
        lines.append(line)
        used += len(line) + 1

    return "\n".join(lines) if lines else fallback


def to_excerpts(results, heading_for=None, query: str = "") -> list[Excerpt]:
    """`SearchResult` 또는 `HybridResult` 목록을 발췌로 바꾼다.

    `heading_for(result) -> str`을 주면 그 결과 바로 앞 청크의 제목을 발췌
    맨 위에 얹는다 (T10.25) — 표에는 등급·구분이 안 적혀 있고 바로 앞 문단에
    있는 경우가 흔하다(실측: "KAC … 코치인증자격 1단계" 다음 청크가 그 등급의
    표). 이게 없으면 모델이 표를 정확히 읽어도 어느 등급인지 말할 수 없다.
    """
    excerpts = []
    for r in results:
        result = getattr(r, "result", r)
        heading = ""
        if heading_for is not None:
            try:
                heading = heading_for(result) or ""
            except Exception:  # noqa: BLE001 — 맥락은 보조 정보다, 실패해도 요약은 계속
                heading = ""
        excerpts.append(Excerpt.from_result(result, heading=heading, query=query))
    return excerpts


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


def format_history(
    history: list[HistoryTurn],
    *,
    max_turns: int = DEFAULT_MAX_HISTORY_TURNS,
    max_chars_per_answer: int = DEFAULT_MAX_CHARS_PER_HISTORY_ANSWER,
) -> str:
    """대화 이력 블록을 만든다. 이력이 없으면 빈 문자열(블록 자체를 생략).

    **근거가 아니라 맥락이라는 것을 문장으로 명시한다** — 안 그러면 모델이
    이전 답변을 이번 답의 근거로 착각해 `[발췌]`에 없는 내용을 마치 근거가
    있는 것처럼 이어 말할 수 있다(TECH 5.3의 "근거 강제" 취지와 충돌).
    """
    if not history:
        return ""
    recent = history[-max_turns:]
    blocks = [
        f"Q: {turn.question.strip()}\nA: {_truncate(turn.answer, max_chars_per_answer)}"
        for turn in recent
    ]
    return (
        "[이전 대화 — 맥락 참고용입니다. 이번 답변의 근거로 사용하지 마십시오]\n"
        + "\n\n".join(blocks)
    )


def build_messages(
    question: str,
    excerpts: list[Excerpt],
    *,
    history: list[HistoryTurn] = (),
    max_chars_per_excerpt: int = DEFAULT_MAX_CHARS_PER_EXCERPT,
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS,
    max_chars_per_history_answer: int = DEFAULT_MAX_CHARS_PER_HISTORY_ANSWER,
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

    `history`(T10.17, 기본 빈 리스트)가 없으면 이전과 **완전히 동일한 문자열**을
    만든다 — Phase 6/7이 실측한 기권정확도·응답정확도 수치가 이 경로(이력 없음)
    기준이라, 히스토리 기능 자체를 안 쓰는 호출(회귀 측정 하네스 포함)의 결과가
    조용히 달라지면 안 된다.
    """
    history_block = format_history(
        list(history), max_turns=max_history_turns, max_chars_per_answer=max_chars_per_history_answer
    )
    parts = [f"[발췌]\n{format_excerpts(excerpts, max_chars_per_excerpt=max_chars_per_excerpt)}"]
    if history_block:
        parts.append(history_block)
    parts.append(f"[질문]\n{question.strip()}")
    body = "\n\n".join(parts)
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


# 근거 번호 `[1]` — 3단계 안전장치(T7.3)가 이것을 출처 표기로 바꾼다.
_CITATION = re.compile(r"\[(\d+)\]")


def expand_citations(answer: str, excerpts: list[Excerpt]) -> str:
    """답변의 `[N]`을 `[파일명, 위치]`로 치환한다 (T7.3, TECH 5.3 3단계).

    **모델에게 파일명을 직접 쓰게 하지 않는 것이 핵심이다.** TECH는 문장마다
    `[파일명, 페이지/슬라이드]`를 요구하지만, 4B급 모델에 파일명을 인라인으로
    적게 하면 그 파일명 자체를 지어낼 여지가 생긴다 — 출처 표기의 목적(원문
    대조)이 정면으로 무너진다. Phase 6 실측에서 후보들이 `[1]`~`[3]` 번호는
    안정적으로 달았으므로, **번호는 모델이 붙이고 치환은 여기서 결정론적으로**
    한다.

    범위를 벗어난 번호(발췌 3건인데 `[5]`)는 **일부러 그대로 둔다** — 지우면
    사용자에게는 멀쩡한 문장으로 보이고, `slm/verify.py`가 같은 것을 찾아
    "확인 필요"로 표시할 근거도 화면에서 사라진다.
    """
    if not answer:
        return ""
    if not excerpts:
        return answer

    def replace(match: re.Match) -> str:
        index = int(match.group(1))
        if index < 1 or index > len(excerpts):
            return match.group(0)
        excerpt = excerpts[index - 1]
        location = excerpt.location
        if location and location != "-":
            return f"[{excerpt.file_name}, {location}]"
        return f"[{excerpt.file_name}]"

    return _CITATION.sub(replace, answer)


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

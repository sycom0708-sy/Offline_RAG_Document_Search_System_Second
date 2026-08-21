"""검색어 하이라이트 + 발췌 윈도잉 (T4.13, DESIGN §5.3).

사이드바 옵션(대/소문자 구분·일치되는 단어)이 검색 조건과 하이라이트에
**동일하게** 적용돼야 한다 — 어긋나면 사용자가 결과를 신뢰하지 못한다
(DESIGN §5.3 명시 요구). 그래서 매치 판정 함수 하나(`find_matches`)를
발췌 중심 잡기와 하이라이트 양쪽에서 재사용한다.

Qt와 무관한 순수 함수라 앱 없이도 테스트할 수 있다.
"""

from __future__ import annotations

import html
import re
from typing import Sequence

HIGHLIGHT_COLOR = "#FEEEAD"  # DESIGN §10.1 — 기존 #FDE68A를 흰색 쪽으로 30% 연하게(2026-08-21, 사용자 요청)
DEFAULT_WINDOW = 140  # DESIGN §5.3 "2줄 기준" 근사치


def split_terms(query: str) -> list[str]:
    return [t for t in query.split() if t.strip()]


def _term_pattern(term: str, exact_word: bool) -> str:
    escaped = re.escape(term)
    return rf"\b{escaped}\b" if exact_word else escaped


def find_matches(
    text: str,
    terms: Sequence[str],
    case_sensitive: bool = False,
    exact_word: bool = False,
) -> list[tuple[int, int]]:
    """겹치지 않는 매치 구간 목록을 위치순으로 반환한다. 겹치는 구간은 병합한다."""
    if not text or not terms:
        return []

    flags = 0 if case_sensitive else re.IGNORECASE
    spans: list[tuple[int, int]] = []
    for term in terms:
        if not term.strip():
            continue
        try:
            pattern = _term_pattern(term, exact_word)
            spans.extend((m.start(), m.end()) for m in re.finditer(pattern, text, flags))
        except re.error:
            continue

    if not spans:
        return []

    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def build_excerpt(
    content: str,
    terms: Sequence[str],
    case_sensitive: bool = False,
    exact_word: bool = False,
    window: int = DEFAULT_WINDOW,
) -> str:
    """`window`자 내외로 발췌한다.

    무조건 앞부분을 자르지 않고 **첫 매치를 중심**으로 창을 잡는다 — 매치가
    발췌 밖으로 밀려나면 하이라이트가 하나도 안 보이는 카드가 나온다.
    """
    text = " ".join(content.split())  # 줄바꿈·연속 공백 정리
    if len(text) <= window:
        return text

    matches = find_matches(text, terms, case_sensitive, exact_word)
    center = (matches[0][0] + matches[0][1]) // 2 if matches else window // 2

    half = window // 2
    start = max(0, center - half)
    end = min(len(text), start + window)
    start = max(0, end - window)  # 끝에 붙어 window보다 짧아지는 경우 보정

    excerpt = text[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(text):
        excerpt = excerpt + "…"
    return excerpt


def to_rich_text(
    text: str,
    terms: Sequence[str],
    case_sensitive: bool = False,
    exact_word: bool = False,
    highlight_color: str = HIGHLIGHT_COLOR,
) -> str:
    """검색어를 강조한 HTML(QLabel 리치 텍스트 서브셋)을 만든다.

    매치 위치는 원문(비이스케이프) 기준으로 찾고, 각 구간을 개별적으로
    `html.escape()`한 뒤 합친다 — 본문에 `<`·`&` 같은 문자가 있어도(실제
    문서에서 나올 수 있다) 깨지지 않는다.
    """
    matches = find_matches(text, terms, case_sensitive, exact_word)
    if not matches:
        return html.escape(text)

    parts: list[str] = []
    cursor = 0
    for start, end in matches:
        parts.append(html.escape(text[cursor:start]))
        parts.append(f'<span style="background-color:{highlight_color}; font-weight:700;">')
        parts.append(html.escape(text[start:end]))
        parts.append("</span>")
        cursor = end
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def highlighted_excerpt(
    content: str,
    query: str,
    case_sensitive: bool = False,
    exact_word: bool = False,
    window: int = DEFAULT_WINDOW,
) -> str:
    """content에서 발췌 + 하이라이트까지 한 번에 처리하는 편의 함수."""
    terms = split_terms(query)
    excerpt = build_excerpt(content, terms, case_sensitive, exact_word, window)
    return to_rich_text(excerpt, terms, case_sensitive, exact_word)

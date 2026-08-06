"""한국어 문장 분리 기반 청킹 (T2.4).

## 문장 분리 방식 — 정규식이 기본값이다

TECH 7장은 `kss`를 지정했으나, **실측 결과 실사용이 불가능한 속도**여서 정규식
분리를 기본값으로 삼는다. 실제 한국어 문서(8,267자)로 측정한 값:

| 방식 | 소요 | 처리량 |
|---|---|---|
| kss (pecab 백엔드) | 157초 | 53 자/초 |
| 정규식 | 0.3ms | 3,000만 자/초 |

10만 자 문서 하나에 kss는 약 31분이 걸려, PRD 5.1의 "최초 실행 시 전체 인덱싱"이
성립하지 않는다. kss가 권장하는 C++ 백엔드(mecab)를 깔면 빨라지지만, Windows
설치가 까다롭고 PRD 4장의 "관리자 권한 불필요" 및 Phase 9 포터블 배포와 충돌한다.

정규식은 마침표류(닫는 인용·괄호 포함)와 줄바꿈을 경계로 본다. 마침표 없이
종결어미만으로 끝나는 문장은 끊지 못하는 한계가 있으나, 형태소 분석 없이
이를 처리하면 오탐이 커서 의도적으로 보수적으로 두었다. `use_kss=True`로
호출하면 여전히 kss를 쓸 수 있다(짧은 텍스트에만 권장).

## 청크 크기 — 문자 수가 아니라 토큰 수로 제한한다

임베딩 모델(`ko-sroberta-multitask`)의 `max_seq_length`는 128토큰이고, 토크나이저
설정에 truncation이 걸려 있어 초과분은 **경고 없이 잘린다**. 잘리면 키워드 검색은
전체 텍스트로 되는데 벡터만 앞부분 기준이 되어 두 단계가 어긋난다.

문자 수는 이 한계를 지키는 지표로 쓸 수 없다. 실제 문서에서 측정한 자/토큰 비율은
**0.50 ~ 2.70으로 5배 넘게 흔들린다** (한국어 산문은 느슨하고, 번호·기호·영문이
섞인 표나 목록은 조밀하다). 같은 300자가 어떤 문단에서는 107토큰, 어떤 문단에서는
146토큰이 된다.

그래서 `count_tokens`를 넘기면 **토큰 수로 자른다**. 넘기지 않으면 `max_chars`만
쓰는데, 이 경우는 청크 알갱이 크기만 좌우할 뿐 임베딩 잘림과는 무관하다
(키워드 검색만 쓰는 Phase 2 단독 실행 경로). 인덱싱 시 임베딩을 함께 만들 때는
반드시 토크나이저를 넘겨야 한다.

text 타입 청크에만 적용한다 — table/image 청크는 구조·참조를 재분할하면
안 되므로(TECH 3.1절) 이 모듈을 거치지 않고 원형 그대로 저장한다.
"""

from __future__ import annotations

import re
from typing import Callable

# 토크나이저 없이 쓸 때의 알갱이 크기. 임베딩 잘림 방지와는 무관하다(독스트링 참고).
DEFAULT_MAX_CHARS = 300

# 모델 한계 128에서 특수 토큰([CLS]/[SEP])과 여유분을 뺀 값.
DEFAULT_MAX_TOKENS = 120

# 문장 경계를 "잘라낼 위치"가 아니라 "문장 자체"로 매칭한다.
# look-behind는 고정 길이만 허용해 "닫는 괄호가 있을 수도 있음" 같은 가변 패턴을
# 담을 수 없기 때문이다.
#
#   1) 마침표·물음표·느낌표 + (닫는 인용/괄호) + 공백
#   2) 줄바꿈
#   3) 문자열 끝
#
# 마침표 없이 종결어미만으로("~합니다 ~입니다") 끊는 규칙도 시도했으나 채택하지
# 않았다. 마침표가 있으면 규칙 1이 이미 처리하므로 중복이고, 오히려 규칙 1보다
# 먼저 발동해 닫는 따옴표를 다음 문장으로 떠넘기는 버그를 만들었다. 마침표 없는
# 종결어미까지 끊으려면 "그렇다면"·"모두다"처럼 종결어미가 아닌 경우를 걸러야
# 하는데, 형태소 분석 없이는 안전하지 않다.
_SENTENCE = re.compile(
    r"""
    .+?                                  # 문장 본문 (최소 매칭)
    (?:
        [.!?]+[\"')\]】」』]*\s*          # 마침표류 + 닫는 인용/괄호
        | \n+                              # 줄바꿈
        | $                                 # 문자열 끝
    )
    """,
    re.VERBOSE | re.DOTALL,
)


def _regex_split(text: str) -> list[str]:
    parts = (m.group().strip() for m in _SENTENCE.finditer(text))
    return [p for p in parts if p]


def _kss_split(text: str) -> list[str]:
    """kss 기반 분리. 미설치·실패 시 정규식으로 폴백한다."""
    try:
        import kss

        sentences = kss.split_sentences(text)
        result = [s.strip() for s in sentences if s.strip()]
        return result or _regex_split(text)
    except Exception:
        # ImportError를 포함해 어떤 실패든 인덱싱을 멈추지 않는다.
        return _regex_split(text)


def split_sentences(text: str, use_kss: bool = False) -> list[str]:
    """문장 단위로 분리한다.

    기본은 정규식이다. `use_kss=True`는 짧은 텍스트를 정밀하게 나눠야 할 때만
    쓴다 — 긴 문서에서는 처리 시간이 현실적이지 않다(모듈 독스트링 참고).
    """
    if not text or not text.strip():
        return []
    return _kss_split(text) if use_kss else _regex_split(text)


def chunk_text(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    use_kss: bool = False,
    count_tokens: Callable[[str], int] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[str]:
    """문장을 묶어 청크 목록을 만든다.

    `count_tokens`를 주면 **토큰 수**로, 주지 않으면 `max_chars`로 자른다.
    임베딩을 만들 경로에서는 반드시 `count_tokens`를 넘겨야 잘림을 막을 수 있다.

    문장 하나씩 청크로 만들면 문맥이 사라지고, 문단 전체를 하나로 두면 검색
    신호가 희석된다. 문장 경계는 깨지 않는다 — 한 문장이 한도보다 길면 그대로
    하나의 청크가 된다(실측한 한국어 문서에서는 128토큰을 넘는 단일 문장이
    없었다).
    """
    sentences = split_sentences(text, use_kss=use_kss)
    if not sentences:
        return []

    if count_tokens is not None:
        return _group(sentences, limit=max_tokens, measure=count_tokens)
    return _group(sentences, limit=max_chars, measure=len)


def _group(
    sentences: list[str],
    limit: int,
    measure: Callable[[str], int],
) -> list[str]:
    """문장을 `measure` 기준 `limit` 이하가 되도록 순서대로 묶는다.

    묶은 결과를 실제로 재측정한다 — 문장별 측정값의 합은 실제 결합 결과와
    다를 수 있다(토크나이저는 특수 토큰을 붙이고, 경계에서 토큰이 합쳐지기도
    한다). 합산 추정만 믿으면 한도를 넘긴 청크가 새어 나간다.
    """
    chunks: list[str] = []
    current: list[str] = []

    for sentence in sentences:
        if not current:
            current = [sentence]
            continue

        candidate = " ".join([*current, sentence])
        if measure(candidate) > limit:
            chunks.append(" ".join(current))
            current = [sentence]
        else:
            current = [*current, sentence]

    if current:
        chunks.append(" ".join(current))

    return chunks

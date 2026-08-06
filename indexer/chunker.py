"""한국어 문장 분리 기반 청킹 (T2.4).

`kss`가 우선이며, 미설치·초기화 실패·처리 중 예외가 나면 정규식 폴백으로
넘어가 인덱싱 파이프라인이 죽지 않게 한다.

kss는 첫 호출 시 백엔드(pecab) 선택 안내를 프로세스당 한 번 stdout에 찍는다.
이 메시지는 kss가 내부적으로 별도 프로세스(multiprocessing)에서 백엔드를
초기화하며 발생시키는 것이라, `sys.stdout`을 바꿔치기하는 파이썬 레벨
리다이렉션으로는 잡히지 않는다 (자식 프로세스는 원래 stdout 파일디스크립터를
그대로 상속한다). OS 파일디스크립터 자체를 바꾸는 방법도 있지만 이식성·안전성
트레이드오프가 커서 채택하지 않았다 — CLI 실행 시 최초 1회 나오는 정보성
메시지로 남겨둔다.

text 타입 청크에만 적용한다 — table/image 청크는 구조·참조를 재분할하면
안 되므로(TECH 3.1절) 이 모듈을 거치지 않고 원형 그대로 저장한다.
"""

from __future__ import annotations

import re

DEFAULT_MAX_CHARS = 400

_FALLBACK_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")


def _regex_split(text: str) -> list[str]:
    parts = [p.strip() for p in _FALLBACK_BOUNDARY.split(text)]
    return [p for p in parts if p]


def split_sentences(text: str) -> list[str]:
    """문장 단위로 분리한다. kss 실패 시 정규식으로 폴백한다."""
    if not text or not text.strip():
        return []

    try:
        import kss

        sentences = kss.split_sentences(text)
        result = [s.strip() for s in sentences if s.strip()]
        return result or _regex_split(text)
    except ImportError:
        return _regex_split(text)
    except Exception:
        return _regex_split(text)


def chunk_text(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """문장을 `max_chars` 내외로 묶어 청크 목록을 만든다.

    문장 하나씩 청크로 만들면 너무 잘게 쪼개져 문맥이 사라지고, 문단 전체를
    하나로 두면 검색 신호가 희석된다. 문장 경계는 절대 깨지 않는다.
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if current and current_len + len(sentence) + 1 > max_chars:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += len(sentence) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks

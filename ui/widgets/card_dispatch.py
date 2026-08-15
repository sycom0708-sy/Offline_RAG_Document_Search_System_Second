"""청크 타입 → 카드 위젯 분기 (T5.1, DESIGN §5.7).

`result_list.py`(검색 카드 목록)와 `chat_panel.py`(챗봇 즉시 발췌, 2026-08-14
부터 검색 카드를 그대로 재사용)가 같은 분기 로직을 쓴다. 두 모듈이 서로를
직접 import하면(`result_list.py`가 이미 `ChatPanel`을 쓴다) 순환 임포트가
생겨 이 별도 모듈로 뺐다.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from parser.schema import ChunkType
from search.hybrid_search import HybridResult
from ui.widgets.image_card import ImageCard
from ui.widgets.result_card import ResultCard
from ui.widgets.table_card import TableCard


def make_result_card(
    result: HybridResult,
    query: str,
    case_sensitive: bool = False,
    exact_word: bool = False,
    show_summary: bool = False,
) -> QWidget:
    """청크 타입에 따라 카드를 분기한다 (T5.1, DESIGN §5.7) — 검색 로직은
    타입과 무관하게 동일하고, 렌더링 단계에서만 갈린다.

    `show_summary`(T10.14, 기본 False)는 카드 단위 "AI 요약 보기" 버튼을
    붙일지 여부다 — 일반 검색 결과 목록만 켜서 쓴다. 챗봇 말풍선은 이미
    턴 단위 요약 버튼을 자체로 갖고 있어(`_AnswerBubble`) 여기서 끄지
    않으면 카드마다 버튼이 하나 더 생겨 중복된다."""
    if result.type is ChunkType.TABLE:
        return TableCard(result, show_summary=show_summary)
    if result.type is ChunkType.IMAGE:
        return ImageCard(result, show_summary=show_summary)
    return ResultCard(result, query, case_sensitive, exact_word, show_summary=show_summary)

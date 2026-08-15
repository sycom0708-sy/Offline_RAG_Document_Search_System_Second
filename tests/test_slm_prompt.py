"""근거 강제 프롬프트 조립·채점 판정 (T6.3).

모델 없이 돌아가는 순수 로직만 다룬다 — 실제 준수율은 `scripts/benchmark_slm.py`가 잰다.
"""

from __future__ import annotations

import pytest

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType
from slm.prompt import (
    ABSTAIN_TEXT,
    NO_EXCERPT_TEXT,
    Excerpt,
    HistoryTurn,
    build_messages,
    clean_answer,
    contains_keywords,
    format_excerpts,
    format_history,
    is_abstention,
    to_excerpts,
)


def make_result(**overrides) -> SearchResult:
    fields = dict(
        chunk_id="c1",
        doc_id="d1",
        file_path=r"C:\문서\보고서.pptx",
        file_name="보고서.pptx",
        type=ChunkType.TEXT,
        page_or_slide=3,
        content="연차는 입사일 기준으로 산정한다.",
        caption="",
        score=1.0,
    )
    fields.update(overrides)
    return SearchResult(**fields)


# --- 발췌 조립 -------------------------------------------------------------

def test_excerpt_uses_ui_location_format():
    """UI 카드와 같은 규칙이어야 Phase 7 출처 표기와 어긋나지 않는다."""
    excerpt = Excerpt.from_result(make_result())
    assert excerpt.location == "3번 슬라이드"

    text_doc = Excerpt.from_result(make_result(file_name="규정.docx", page_or_slide=2))
    assert text_doc.location == "2페이지"


def test_format_excerpts_numbers_and_labels():
    block = format_excerpts([
        Excerpt("규정.docx", "2페이지", "연차 규정"),
        Excerpt("보고서.pptx", "3번 슬라이드", "매출 현황"),
    ])
    assert block.startswith("[1] 규정.docx · 2페이지\n연차 규정")
    assert "[2] 보고서.pptx · 3번 슬라이드\n매출 현황" in block


def test_format_excerpts_omits_empty_location():
    block = format_excerpts([Excerpt("메모.txt", "-", "내용")])
    assert block.startswith("[1] 메모.txt\n")
    assert "·" not in block


def test_format_excerpts_states_absence_explicitly():
    """빈 문자열을 넣으면 모델이 자체 지식으로 답해버린다."""
    assert format_excerpts([]) == NO_EXCERPT_TEXT


def test_long_excerpt_is_truncated():
    block = format_excerpts([Excerpt("표.xlsx", "1분기", "가" * 5000)],
                            max_chars_per_excerpt=100)
    assert "…(생략)" in block
    assert len(block) < 300


def test_to_excerpts_unwraps_hybrid_result():
    class FakeHybrid:
        def __init__(self, result):
            self.result = result

    excerpts = to_excerpts([FakeHybrid(make_result()), make_result(file_name="a.txt")])
    assert [e.file_name for e in excerpts] == ["보고서.pptx", "a.txt"]


# --- 메시지 ---------------------------------------------------------------

def test_build_messages_puts_rules_in_user_turn():
    """system 역할은 템플릿이 통째로 버리는 모델이 있다(EXAONE-4.0 실측)."""
    messages = build_messages("연차는 어떻게 산정하나요?", to_excerpts([make_result()]))
    assert [m["role"] for m in messages] == ["user"]

    content = messages[0]["content"]
    assert ABSTAIN_TEXT in content  # 기권 문구가 지시에 박혀 있어야 한다
    assert "연차는 어떻게 산정하나요?" in content
    assert "[1] 보고서.pptx" in content


def test_build_messages_can_still_use_system_role():
    messages = build_messages("질문", [], use_system_role=True)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert ABSTAIN_TEXT in messages[0]["content"]


def test_build_messages_without_excerpts_still_forbids_guessing():
    messages = build_messages("있지도 않은 질문", [])
    assert NO_EXCERPT_TEXT in messages[0]["content"]


# --- 대화 이력 (T10.17) -----------------------------------------------------

def test_build_messages_without_history_is_byte_identical():
    """기본값(history=())은 Phase 6/7이 실측한 경로와 완전히 같은 문자열을
    만들어야 한다 — 그 실측치가 이 경로 기준이라 조용히 달라지면 안 된다."""
    excerpts = to_excerpts([make_result()])
    with_default = build_messages("연차는 어떻게 산정하나요?", excerpts)
    with_empty_history = build_messages("연차는 어떻게 산정하나요?", excerpts, history=[])
    assert with_default == with_empty_history


def test_format_history_empty_returns_empty_string():
    assert format_history([]) == ""


def test_format_history_includes_question_and_answer():
    history = [HistoryTurn(question="연차는 며칠인가요?", answer="15일입니다. [1]")]
    text = format_history(history)
    assert "연차는 며칠인가요?" in text
    assert "15일입니다. [1]" in text
    assert "근거로 사용하지 마십시오" in text  # 근거 아님을 명시해야 한다


def test_format_history_keeps_only_recent_n_turns():
    history = [HistoryTurn(question=f"질문{i}", answer=f"답변{i}") for i in range(5)]
    text = format_history(history, max_turns=2)
    assert "질문0" not in text
    assert "질문3" in text
    assert "질문4" in text


def test_format_history_truncates_long_answers():
    history = [HistoryTurn(question="질문", answer="가" * 500)]
    text = format_history(history, max_chars_per_answer=50)
    assert "가" * 500 not in text
    assert "…(생략)" in text


def test_build_messages_with_history_includes_previous_turn_and_current_question():
    excerpts = to_excerpts([make_result()])
    history = [HistoryTurn(question="이전 질문", answer="이전 답변 [1]")]
    messages = build_messages("이번 질문", excerpts, history=history)
    content = messages[0]["content"]

    assert "이전 질문" in content
    assert "이전 답변 [1]" in content
    assert "이번 질문" in content
    # 이번 질문이 이력보다 뒤(가장 마지막)에 와야 모델이 "지금 답할 질문"으로 인식한다.
    assert content.index("이전 질문") < content.index("이번 질문")


@pytest.mark.parametrize("raw,expected", [
    ("[답변] 최소 사양은 8GB입니다.", "최소 사양은 8GB입니다."),
    ("답변: 8GB", "8GB"),
    ("  [답변]  8GB  ", "8GB"),
    ("최소 사양은 8GB입니다.", "최소 사양은 8GB입니다."),
    ("", ""),
])
def test_clean_answer_strips_echoed_label(raw, expected):
    """예시의 `[답변]` 라벨을 모델이 따라 쓰는 일이 잦다(실측)."""
    assert clean_answer(raw) == expected


# --- 채점 판정 ------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    ABSTAIN_TEXT,
    "문서에서 찾을 수 없습니다",           # 마침표 누락
    "  문서에서 찾을 수 없습니다.  ",       # 공백
    "문서에서  찾을 수  없습니다.",         # 공백 변형
    "죄송합니다. 문서에서 찾을 수 없습니다.",  # 앞말 덧붙임
])
def test_is_abstention_true(answer):
    assert is_abstention(answer) is True


@pytest.mark.parametrize("answer", [
    "",
    "연차는 입사일 기준으로 산정합니다. [1]",
    "잘 모르겠습니다.",          # 지정 문구가 아니면 준수 실패로 본다
    "관련 내용이 없습니다.",
])
def test_is_abstention_false(answer):
    assert is_abstention(answer) is False


def test_contains_keywords():
    answer = "연차는 입사일 기준으로 산정합니다. [1]"
    assert contains_keywords(answer, ["입사일", "산정"]) is True
    assert contains_keywords(answer, ["입사일", "회계연도"]) is False
    assert contains_keywords(answer, []) is False


def test_contains_keywords_accepts_synonym_slots():
    """패러프레이즈를 오답으로 깎지 않기 위한 동의어 자리 (표본 육안 검증에서 발견)."""
    answer = "고객은 언제든지 코칭을 종료할 수 있습니다. [1]"
    assert contains_keywords(answer, [["어느 시점", "언제든지"]]) is True
    assert contains_keywords(answer, [["어느 시점", "특정 시점"]]) is False
    # 자리마다 하나씩은 맞아야 한다 — 동의어 자리와 단일 키워드를 섞어 쓸 수 있다.
    assert contains_keywords(answer, [["어느 시점", "언제든지"], "종료"]) is True
    assert contains_keywords(answer, [["어느 시점", "언제든지"], "해지"]) is False

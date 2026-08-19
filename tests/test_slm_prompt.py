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

class TestStructuredTableExcerpts:
    """T10.25(2026-08-18) — 표를 평문이 아니라 행·열 구조로 넣는다.

    🔴 실측 배경: 평문으로 펼치면 열 머리글이 셀에서 멀어져 모델이 축을 뭉갠다.
    등급×지원유형 2축 표에서 "KPC 응시자는 40만원"처럼 **다른 등급의 열 값**을
    등급 이름에 붙였다(40/60은 KSC의 두 열이다).
    """

    @staticmethod
    def _table_json(rows, header=("서류종류", "ACPK 지원", "포트폴리오 지원")):
        import json

        return json.dumps({"header_row": list(header), "rows": rows, "caption": ""})

    def _result(self, table_json, content="펼쳐진 평문"):
        from indexer.fts5.search import SearchResult
        from parser.schema import ChunkType

        return SearchResult(
            chunk_id="c1", doc_id="d1", file_path="x.doc", file_name="안내.doc",
            type=ChunkType.TABLE, page_or_slide=None, content=content,
            caption="", score=-1.0, table_json=table_json,
        )

    def test_header_row_comes_first_so_cells_keep_their_column(self):
        from slm.prompt import render_table_text

        text = render_table_text(
            self._table_json([["⑩ 응시료", "20만원", "35만원"]]), "평문"
        )

        assert text.splitlines()[0] == "서류종류 | ACPK 지원 | 포트폴리오 지원"
        assert "⑩ 응시료 | 20만원 | 35만원" in text

    def test_heading_is_placed_above_the_table(self):
        """등급은 표 안에 없다 — 앞 문단 제목이 그 자리를 채운다."""
        from slm.prompt import render_table_text

        text = render_table_text(
            self._table_json([["⑩ 응시료", "20만원", "35만원"]]),
            "평문",
            heading="KAC : 코치인증자격 1단계",
        )

        assert text.startswith("KAC : 코치인증자격 1단계")

    def test_falls_back_to_plain_content_without_table_json(self):
        from slm.prompt import render_table_text

        assert render_table_text(None, "평문 그대로") == "평문 그대로"

    def test_falls_back_on_broken_json(self):
        from slm.prompt import render_table_text

        assert render_table_text("{망가진", "평문 그대로") == "평문 그대로"

    def test_truncation_never_cuts_a_row_in_half(self):
        """🔴 글자 수로 자르면 "40만" 같은 반쪽 셀이 남는다 — 행 단위로 자른다."""
        from slm.prompt import render_table_text

        rows = [[f"항목{i}", "가" * 60, "나" * 60] for i in range(20)]
        text = render_table_text(self._table_json(rows), "평문", max_chars=400)

        assert len(text) <= 500  # 상한 근처에서 멈춘다
        for line in text.splitlines()[1:]:
            assert line.count(" | ") == 2  # 모든 행이 온전하다

    def test_excerpt_uses_structured_text_for_table_chunks(self):
        from slm.prompt import Excerpt

        excerpt = Excerpt.from_result(
            self._result(self._table_json([["⑩ 응시료", "20만원", "35만원"]]))
        )

        assert "ACPK 지원" in excerpt.text

    def test_non_table_chunk_keeps_its_content_with_heading(self):
        from indexer.fts5.search import SearchResult
        from parser.schema import ChunkType
        from slm.prompt import Excerpt

        result = SearchResult(
            chunk_id="c1", doc_id="d1", file_path="x.doc", file_name="안내.doc",
            type=ChunkType.TEXT, page_or_slide=None, content="본문입니다",
            caption="", score=-1.0,
        )

        excerpt = Excerpt.from_result(result, heading="제목")

        assert excerpt.text == "제목\n\n본문입니다"

    def test_heading_lookup_failure_does_not_break_summarizing(self):
        """제목은 보조 정보다 — 조회가 터져도 발췌는 나와야 한다."""
        from slm.prompt import to_excerpts

        def boom(_result):
            raise RuntimeError("인덱스 접근 실패")

        excerpts = to_excerpts(
            [self._result(self._table_json([["⑩ 응시료", "20만원", "35만원"]]))],
            heading_for=boom,
        )

        assert len(excerpts) == 1
        assert "ACPK 지원" in excerpts[0].text


class TestQueryAwareTableRows:
    """T10.27(2026-08-18) — 표에서 질문과 관련된 행만 남긴다.

    🔴 실측 배경: 발췌가 프롬프트를 3,922토큰까지 먹어 n_ctx(4096)에 174토큰만
    남았고, 답이 조금만 길면 생성이 중간에 끊겼다(화면엔 "… 다릅니다. [ "처럼
    반쪽 대괄호가 남는다). 게다가 프롬프트 처리가 전체 지연의 93%였다
    (35.6ms/토큰 × 3,922토큰 = 140초). 관련 행만 남기면 잘림·지연·정확도가
    한 번에 나아진다 — 실측 2,151토큰 / 71초 / 세 등급 응시료 모두 정답.
    """

    @staticmethod
    def _tj(rows):
        import json

        return json.dumps(
            {"header_row": ["서류종류", "ACPK 지원", "포트폴리오 지원"], "rows": rows, "caption": ""}
        )

    _ROWS = [
        ["① 윤리규정준수 서약서", "협회양식 작성", "협회양식 작성"],
        ["② 교육리스트", "20시간 이상", "20시간 이상"],
        ["⑩ 응시료", "20만원", "35만원"],
        ["필기시험", "온라인시험", "온라인시험"],
    ]

    def test_keeps_only_rows_matching_the_question(self):
        from slm.prompt import render_table_text

        text = render_table_text(self._tj(self._ROWS), "평문", query="응시료가 얼마야?")

        assert "⑩ 응시료 | 20만원 | 35만원" in text
        assert "윤리규정준수" not in text
        assert "필기시험" not in text

    def test_header_survives_row_filtering(self):
        """열 머리글이 없으면 20만원이 어느 열인지 알 수 없다 — 항상 남긴다."""
        from slm.prompt import render_table_text

        text = render_table_text(self._tj(self._ROWS), "평문", query="응시료")

        assert "서류종류 | ACPK 지원 | 포트폴리오 지원" in text

    def test_falls_back_to_all_rows_when_nothing_matches(self):
        """질문에 없는 말로 적힌 표는 좁히지 않는다 — 비우면 답할 근거가 사라진다."""
        from slm.prompt import render_table_text

        text = render_table_text(self._tj(self._ROWS), "평문", query="전혀무관한단어")

        assert "윤리규정준수" in text
        assert "⑩ 응시료 | 20만원 | 35만원" in text

    def test_without_a_query_behaviour_is_unchanged(self):
        """질문을 안 주면 T10.25와 똑같이 순서대로 채운다."""
        from slm.prompt import render_table_text

        assert render_table_text(self._tj(self._ROWS), "평문") == render_table_text(
            self._tj(self._ROWS), "평문", query=""
        )

    def test_korean_particles_are_handled_like_the_search_does(self):
        """"응시료가"(조사 포함)로 물어도 "응시료" 행이 걸려야 한다 —
        FTS5·재순위와 같은 `query_term_variants()`를 쓰기 때문."""
        from slm.prompt import render_table_text

        text = render_table_text(self._tj(self._ROWS), "평문", query="응시료가")

        assert "⑩ 응시료" in text

    def test_summarize_passes_the_question_down_to_row_selection(self):
        """배선 확인 — `summarize()`가 질문을 발췌 생성까지 실어 보내야 한다."""
        from indexer.fts5.search import SearchResult
        from parser.schema import ChunkType
        from search.hybrid_search import HybridResult
        from slm.summarize import select_excerpts

        result = SearchResult(
            chunk_id="c1", doc_id="d1", file_path="x.doc", file_name="안내.doc",
            type=ChunkType.TABLE, page_or_slide=None, content="평문",
            caption="", score=-1.0, table_json=self._tj(self._ROWS),
        )
        excerpts = select_excerpts([HybridResult(result, 0.9, False)], query="응시료")

        assert "윤리규정준수" not in excerpts[0].text

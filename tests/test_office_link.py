"""원문 열기 딥링크 계획 수립 테스트 (T10.1).

`plan_open()`은 Qt·서브프로세스·COM 전혀 없이 순수하게 판단만 하므로 전부
빠르게 단위 테스트할 수 있다. 실제 `open_and_locate()`(서브프로세스로
Word/PowerPoint/Excel을 **화면에 띄우는** COM 자동화)는 여기서 자동
실행하지 않는다 — pytest를 돌릴 때마다 실제 Office 창이 열리고 안 닫힌
채 남는 건 자동화 테스트 위생에 맞지 않는다(이 PC엔 실제 Office가 있어
검증 자체는 가능하지만, 종단 확인은 스크래치 스크립트로 한 번 했다).
`is_office_available()`은 부작용이 없는 순수 조회라 실제로 돌린다.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from indexer.fts5.search import SearchResult
from parser.schema import ChunkType, ImageData, TableData
from search.hybrid_search import HybridResult
from search.office_link import (
    _DOCX_SCRIPT,
    _PPTX_SCRIPT,
    _XLSX_SCRIPT,
    OfficeAutomationFailedError,
    OpenPlan,
    _build_needle_ladder,
    _longest_cell,
    _open_pdf_at_page,
    is_office_available,
    plan_open,
)


def _result(
    file_path: str,
    type: ChunkType,
    page_or_slide: int | None = None,
    content: str = "",
    table: TableData | None = None,
    image: ImageData | None = None,
) -> SearchResult:
    return SearchResult(
        chunk_id="c1",
        doc_id="d1",
        file_path=file_path,
        file_name=file_path.rsplit("\\", 1)[-1],
        type=type,
        page_or_slide=page_or_slide,
        content=content,
        caption="",
        score=-1.0,
        table_json=json.dumps(asdict(table)) if table else None,
        image_json=json.dumps(asdict(image)) if image else None,
    )


def _hybrid(result: SearchResult) -> HybridResult:
    return HybridResult(result, 0.7, False)


class TestPlanOpenPptx:
    def test_text_chunk_uses_slide_index_only(self):
        result = _result("x.pptx", ChunkType.TEXT, page_or_slide=5, content="아무 내용")
        plan = plan_open(_hybrid(result))
        assert plan == OpenPlan(page_or_slide=5)

    def test_table_and_image_chunks_also_use_slide_index(self):
        """pptx는 텍스트/표/이미지 전부 정확한 slide_index를 가진다 — 텍스트 검색이 필요 없다."""
        for chunk_type in (ChunkType.TABLE, ChunkType.IMAGE):
            result = _result("x.pptx", chunk_type, page_or_slide=3)
            assert plan_open(_hybrid(result)) == OpenPlan(page_or_slide=3)

    def test_missing_slide_index_yields_empty_plan(self):
        result = _result("x.pptx", ChunkType.TEXT, page_or_slide=None)
        assert plan_open(_hybrid(result)).is_empty()


class TestPlanOpenXlsx:
    def test_table_uses_sheet_name_and_longest_cell(self):
        table = TableData(rows=[["짧음", "가장 긴 셀 값입니다"]], header_row=["h1", "h2"], caption="Sheet2")
        result = _result("x.xlsx", ChunkType.TABLE, table=table)
        plan = plan_open(_hybrid(result))
        assert plan.sheet_name == "Sheet2"
        assert plan.needles == ["가장 긴 셀 값입니다"]

    def test_missing_table_json_yields_empty_plan(self):
        result = _result("x.xlsx", ChunkType.TABLE, table=None)
        assert plan_open(_hybrid(result)).is_empty()

    def test_non_table_chunk_yields_empty_plan(self):
        result = _result("x.xlsx", ChunkType.TEXT)
        assert plan_open(_hybrid(result)).is_empty()


class TestPlanOpenDocx:
    def test_text_chunk_builds_needle_ladder_from_content(self):
        content = "짧은 문단."
        result = _result("x.docx", ChunkType.TEXT, content=content)
        plan = plan_open(_hybrid(result))
        assert plan.needles == [content]
        assert plan.page_or_slide is None
        assert plan.sheet_name is None

    def test_table_chunk_uses_longest_cell(self):
        table = TableData(rows=[["a", "가장 긴 셀"]], header_row=["h1", "h2"])
        result = _result("x.docx", ChunkType.TABLE, table=table)
        plan = plan_open(_hybrid(result))
        assert plan.needles == ["가장 긴 셀"]

    def test_image_chunk_with_caption_uses_caption_as_needle(self):
        image = ImageData(image_path="x.png", caption="도면 1: 배치도")
        result = _result("x.docx", ChunkType.IMAGE, image=image)
        plan = plan_open(_hybrid(result))
        assert plan.needles == ["도면 1: 배치도"]

    def test_image_chunk_without_caption_yields_empty_plan(self):
        """docx 이미지 청크는 page_or_slide가 원래 없다(Phase 1 제약) — 캡션도 없으면 이동할 곳이 없다."""
        image = ImageData(image_path="x.png", caption="")
        result = _result("x.docx", ChunkType.IMAGE, image=image)
        assert plan_open(_hybrid(result)).is_empty()

    def test_missing_table_json_yields_empty_plan(self):
        result = _result("x.docx", ChunkType.TABLE, table=None)
        assert plan_open(_hybrid(result)).is_empty()


class TestPlanOpenPdf:
    """pdf는 pptx와 같은 방식이다 — 페이지 번호만으로 충분하다(T10.50).

    docx TEXT 청크와 달리 chunker.py의 문장 재그룹을 거쳐도 페이지 번호
    자체는 훼손되지 않으므로 needle 없이 page_or_slide만으로 딥링크가 된다.
    """

    def test_text_chunk_uses_page_number_only(self):
        result = _result("x.pdf", ChunkType.TEXT, page_or_slide=3, content="본문")
        plan = plan_open(_hybrid(result))
        assert plan == OpenPlan(page_or_slide=3)

    def test_table_and_image_chunks_also_use_page_number(self):
        for chunk_type in (ChunkType.TABLE, ChunkType.IMAGE):
            result = _result("x.pdf", chunk_type, page_or_slide=7)
            assert plan_open(_hybrid(result)) == OpenPlan(page_or_slide=7)

    def test_missing_page_number_yields_empty_plan(self):
        result = _result("x.pdf", ChunkType.TEXT, page_or_slide=None)
        assert plan_open(_hybrid(result)).is_empty()


class TestPlanOpenUnsupportedFormats:
    def test_txt_yields_empty_plan_regardless_of_content(self):
        result = _result("x.txt", ChunkType.TEXT, content="충분히 긴 본문 내용입니다" * 10)
        assert plan_open(_hybrid(result)).is_empty()


class TestBuildNeedleLadder:
    def test_short_text_returns_single_entry(self):
        assert _build_needle_ladder("짧은 문장.") == ["짧은 문장."]

    def test_empty_or_whitespace_returns_empty_list(self):
        assert _build_needle_ladder("") == []
        assert _build_needle_ladder("   \n  ") == []

    def test_long_text_produces_decreasing_lengths(self):
        text = "가" * 300
        ladder = _build_needle_ladder(text, sizes=(180, 90, 40, 20))
        assert [len(n) for n in ladder] == [180, 90, 40, 20]

    def test_no_duplicate_entries(self):
        # 정확히 20자 — 180/90/40 단계는 전부 "len(cleaned) > n" 조건에 걸려 후보에서 빠지고
        # 마지막 20자 단계 하나만 남아야 한다(중복 없이).
        text = "가" * 20
        ladder = _build_needle_ladder(text, sizes=(180, 90, 40, 20))
        assert ladder == [text]

    def test_multi_paragraph_text_never_produces_a_needle_spanning_the_break(self):
        """🔴 실사용 검증에서 실제로 잡은 버그: 청커가 여러 Word 문단을 "\\n"으로
        이어붙이는데, 그 이어붙인 문자열을 글자 수로만 잘라 needle을 만들면
        Word의 문단 구분자(\\r)와 안 맞아 20자짜리도 못 찾았다(실측). 어떤
        needle도 원본 문단 경계를 넘으면 안 된다.
        """
        text = "오프라인 문서 검색 시스템 개요\n사내 문서를 완전 오프라인 환경에서 검색하기 위한 시스템이다."
        ladder = _build_needle_ladder(text)
        for needle in ladder:
            assert "\n" not in needle

    def test_second_paragraph_is_used_when_first_is_too_short_to_disambiguate(self):
        text = "짧다\n두 번째 문단은 이만큼 길다"
        ladder = _build_needle_ladder(text)
        assert ladder[0] == "짧다"  # 첫 문단부터 시도
        assert "두 번째 문단은 이만큼 길다" in ladder  # 둘째 문단도 후보로 이어붙는다


class TestLongestCell:
    def test_picks_longest_across_rows_and_header(self):
        assert _longest_cell([["a", "bb"], ["ccc", ""]], ["h1", "h22222"]) == "h22222"

    def test_empty_table_returns_none(self):
        assert _longest_cell([], []) is None

    def test_whitespace_only_cells_return_none(self):
        assert _longest_cell([["  ", ""]], ["  "]) is None


class TestIsOfficeAvailable:
    def test_unsupported_extension_is_false(self):
        assert is_office_available(".txt") is False

    def test_registered_progid_is_true(self, monkeypatch):
        import winreg

        class _FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _FakeKey())
        assert is_office_available(".docx") is True

    def test_unregistered_progid_is_false(self, monkeypatch):
        import winreg

        def _raise(*a, **k):
            raise OSError("등록 안 됨")

        monkeypatch.setattr(winreg, "OpenKey", _raise)
        assert is_office_available(".pptx") is False


class TestIsOfficeAvailablePdf:
    """PDF는 Office COM ProgID가 아니라 Edge PDF 뷰어 여부로 판단한다 (T10.50)."""

    def test_edge_default_and_exe_found_is_true(self, monkeypatch):
        import search.office_link as office_link

        monkeypatch.setattr(office_link, "_pdf_default_progid", lambda: "MSEdgePDF")
        monkeypatch.setattr(office_link, "_find_msedge_exe", lambda: r"C:\edge\msedge.exe")
        assert is_office_available(".pdf") is True

    def test_non_edge_default_is_false(self, monkeypatch):
        import search.office_link as office_link

        monkeypatch.setattr(office_link, "_pdf_default_progid", lambda: "AcroExch.Document")
        monkeypatch.setattr(office_link, "_find_msedge_exe", lambda: r"C:\edge\msedge.exe")
        assert is_office_available(".pdf") is False

    def test_edge_default_but_exe_missing_is_false(self, monkeypatch):
        import search.office_link as office_link

        monkeypatch.setattr(office_link, "_pdf_default_progid", lambda: "MSEdgePDF")
        monkeypatch.setattr(office_link, "_find_msedge_exe", lambda: None)
        assert is_office_available(".pdf") is False

    def test_no_user_choice_registered_is_false(self, monkeypatch):
        import search.office_link as office_link

        monkeypatch.setattr(office_link, "_pdf_default_progid", lambda: None)
        monkeypatch.setattr(office_link, "_find_msedge_exe", lambda: r"C:\edge\msedge.exe")
        assert is_office_available(".pdf") is False


class TestOpenPdfAtPage:
    """`_open_pdf_at_page()`는 msedge.exe를 직접 실행한다 — 실제 창은 안 띄운다(Popen 모킹)."""

    def test_launches_msedge_with_single_argument_and_page_fragment(self, monkeypatch, tmp_path):
        import search.office_link as office_link

        pdf_path = tmp_path / "문서.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        monkeypatch.setattr(office_link, "_find_msedge_exe", lambda: r"C:\edge\msedge.exe")
        captured = {}

        def _fake_popen(args, **kwargs):
            captured["args"] = args

        monkeypatch.setattr(office_link.subprocess, "Popen", _fake_popen)

        office_link._open_pdf_at_page(str(pdf_path), OpenPlan(page_or_slide=5))

        args = captured["args"]
        assert args[0] == r"C:\edge\msedge.exe"
        assert args[1] == "--single-argument"
        assert args[2].startswith("file:")
        assert args[2].endswith("#page=5")

    def test_missing_page_number_raises(self, tmp_path):
        with pytest.raises(OfficeAutomationFailedError):
            _open_pdf_at_page(str(tmp_path / "x.pdf"), OpenPlan())

    def test_msedge_not_found_raises(self, monkeypatch, tmp_path):
        import search.office_link as office_link

        monkeypatch.setattr(office_link, "_find_msedge_exe", lambda: None)
        with pytest.raises(OfficeAutomationFailedError):
            _open_pdf_at_page(str(tmp_path / "x.pdf"), OpenPlan(page_or_slide=1))


class TestComScriptOpenIsOutsideNavigationTryBlock:
    """🔴 실제로 재현한 회귀(T10.51): "파일 열기"와 "위치로 이동"이 하나의

    try/catch로 묶여 있으면, 파일은 이미 정상적으로 열렸는데 위치 이동
    (Find/GotoSlide/Worksheets.Item)만 실패해도 스크립트 전체가 실패로
    처리돼 파이썬 쪽 폴백이 **새 프로세스로 같은 파일을 한 번 더** 연다
    (실측: xlsx 하나로 Excel 창 두 개가 동시에 떴다). `.Open()` 호출이
    `try {` 블록보다 앞에 있어야 이 회귀가 재발하지 않는다 — 실제 COM을
    띄우지 않고도 문자열 구조로 지킬 수 있는 불변식이다.
    """

    @pytest.mark.parametrize(
        "script,open_call",
        [
            (_DOCX_SCRIPT, "$word.Documents.Open("),
            (_PPTX_SCRIPT, "$ppt.Presentations.Open("),
            (_XLSX_SCRIPT, "$excel.Workbooks.Open("),
        ],
    )
    def test_open_call_precedes_try_block(self, script, open_call):
        open_index = script.index(open_call)
        try_index = script.index("try {")
        assert open_index < try_index


class TestPptxVisibleIsNotPlainBoolean:
    """🔴 실제로 재현한 별개 버그: PowerPoint의 `Application.Visible`은

    Word/Excel과 달리 순수 Boolean이 아니라 `MsoTriState` 열거형이다.
    이 서브프로세스 실행 컨텍스트에서는 `$ppt.Visible = $true`가 자동으로
    `MsoTriState`로 캐스팅되지 않고 예외가 난다(실측 확인 — pptx 실 문서로
    처음 종단 검증했을 때 드러났다; 이전까지는 실 pptx가 없어 안 걸렸다).
    `-1`(MsoTriState.msoTrue의 실제 값)을 쓰면 어떤 컨텍스트에서도 안전하다.
    """

    def test_pptx_script_does_not_assign_plain_bool_to_visible(self):
        assert "$ppt.Visible = $true" not in _PPTX_SCRIPT
        assert "$ppt.Visible = -1" in _PPTX_SCRIPT


requires_office = pytest.mark.skipif(
    not any(is_office_available(ext) for ext in (".docx", ".pptx", ".xlsx")),
    reason="MS Office 미설치 — 이 환경에서는 건너뜁니다",
)


@requires_office
class TestIsOfficeAvailableRealRegistry:
    """부작용이 없는 순수 조회라 실제 레지스트리로 돌려도 안전하다."""

    def test_at_least_one_installed_office_app_is_detected(self):
        assert any(is_office_available(ext) for ext in (".docx", ".pptx", ".xlsx"))

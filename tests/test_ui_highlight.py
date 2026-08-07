"""하이라이트·발췌 윈도잉 테스트 (T4.13).

DESIGN §5.3의 핵심 요구: 사이드바 옵션(대소문자 구분·일치되는 단어)이
검색 조건과 하이라이트에 동일하게 적용돼야 한다.
"""

from __future__ import annotations

from ui.highlight import (
    build_excerpt,
    find_matches,
    highlighted_excerpt,
    split_terms,
    to_rich_text,
)


def test_split_terms_splits_on_whitespace():
    assert split_terms("계약서 검토 기준") == ["계약서", "검토", "기준"]


def test_split_terms_empty_query():
    assert split_terms("   ") == []


class TestFindMatches:
    def test_case_insensitive_by_default(self):
        assert find_matches("API 문서와 api key", ["API"]) == [(0, 3), (8, 11)]

    def test_case_sensitive_narrows_matches(self):
        assert find_matches("API 문서와 api key", ["API"], case_sensitive=True) == [(0, 3)]

    def test_exact_word_excludes_mid_token_match(self):
        """'영업계약서'는 '계약'을 부분 포함하지만 완전 일치 모드에선 안 잡혀야 한다."""
        text = "영업계약서 표준 문서, 계약 해지 조건"
        assert find_matches(text, ["계약"], exact_word=True) == [(13, 15)]

    def test_partial_match_off_catches_mid_token(self):
        text = "영업계약서 표준 문서"
        assert find_matches(text, ["계약"], exact_word=False) == [(2, 4)]

    def test_overlapping_matches_are_merged(self):
        # "계약" 과 "계약서" 가 같은 위치에서 겹친다.
        matches = find_matches("계약서 검토", ["계약", "계약서"], exact_word=False)
        assert matches == [(0, 3)]

    def test_no_match_returns_empty_list(self):
        assert find_matches("전혀 다른 내용", ["계약서"]) == []

    def test_empty_text_or_terms(self):
        assert find_matches("", ["계약"]) == []
        assert find_matches("계약서", []) == []

    def test_special_regex_characters_do_not_raise(self):
        # 검색어에 정규식 특수문자가 섞여도 리터럴로 처리돼야 한다.
        assert find_matches("가격은 3.5(원)입니다", ["3.5(원)"]) == [(4, 10)]


class TestBuildExcerpt:
    def test_short_content_returned_as_is(self):
        assert build_excerpt("짧은 내용", ["짧은"]) == "짧은 내용"

    def test_long_content_centers_on_first_match(self):
        content = "가" * 200 + "계약서 검토 기준" + "나" * 200
        excerpt = build_excerpt(content, ["계약서"], window=40)
        assert "계약서" in excerpt
        assert excerpt.startswith("…")
        assert excerpt.endswith("…")

    def test_no_match_falls_back_to_start(self):
        content = "가" * 300
        excerpt = build_excerpt(content, ["없는단어"], window=50)
        assert excerpt.startswith("가")
        assert len(excerpt) <= 51  # "…" 포함 여유

    def test_match_near_start_does_not_prefix_ellipsis(self):
        content = "계약서 검토 기준" + "나" * 300
        excerpt = build_excerpt(content, ["계약서"], window=50)
        assert not excerpt.startswith("…")

    def test_whitespace_and_newlines_collapsed(self):
        content = "첫 줄\n\n\n   둘째   줄"
        assert build_excerpt(content, [], window=100) == "첫 줄 둘째 줄"


class TestToRichText:
    def test_wraps_match_in_highlight_span(self):
        html_out = to_rich_text("계약서 검토", ["계약서"])
        assert '<span style="background-color:#FDE68A; font-weight:700;">계약서</span>' in html_out
        assert html_out.endswith(" 검토")

    def test_escapes_html_special_characters_in_content(self):
        html_out = to_rich_text("R&D <검토> 계약서", ["계약서"])
        assert "R&amp;D" in html_out
        assert "&lt;검토&gt;" in html_out

    def test_escapes_special_characters_inside_matched_term(self):
        html_out = to_rich_text("A&B 조항", ["A&B"])
        assert "A&amp;B" in html_out
        assert "<span" in html_out

    def test_no_match_returns_escaped_plain_text(self):
        assert to_rich_text("R&D 계약서", ["없는단어"]) == "R&amp;D 계약서"

    def test_multiple_non_overlapping_matches_all_highlighted(self):
        html_out = to_rich_text("계약 해지 후 계약 갱신", ["계약"])
        assert html_out.count('background-color:#FDE68A') == 2


def test_highlighted_excerpt_end_to_end():
    content = "계약서 검토 시 기준이 되는 조항은 손해배상, 계약 해지, 지급 조건 세 가지다"
    result = highlighted_excerpt(content, "계약서 검토", case_sensitive=False, exact_word=False)
    assert "background-color:#FDE68A" in result
    assert "계약서" in result


def test_highlighted_excerpt_respects_case_sensitive_option():
    """DESIGN §5.3: 옵션이 검색 조건과 하이라이트에 동일하게 적용돼야 한다."""
    content = "API 문서를 확인하세요"
    lower_no_match = highlighted_excerpt(content, "api", case_sensitive=True)
    assert "background-color" not in lower_no_match

    lower_match = highlighted_excerpt(content, "api", case_sensitive=False)
    assert "background-color" in lower_match


def test_highlighted_excerpt_respects_exact_word_option():
    content = "영업계약서 표준 문서"
    no_match_exact = highlighted_excerpt(content, "계약", exact_word=True)
    assert "background-color" not in no_match_exact

    match_partial = highlighted_excerpt(content, "계약", exact_word=False)
    assert "background-color" in match_partial

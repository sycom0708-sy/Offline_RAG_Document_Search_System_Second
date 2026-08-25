"""청커 테스트 (T2.4).

kss 실제 동작과 정규식 폴백 양쪽을 확인한다. 실제 kss 출력의 정확한 문장
경계는 라이브러리 버전에 따라 달라질 수 있어, 폴백 경로는 monkeypatch로
강제해 결정적으로 검증하고 kss 경로는 "크래시 없이 non-empty를 반환하는가"만
확인한다.
"""

from __future__ import annotations

import builtins

import pytest

from indexer.chunker import chunk_text, split_sentences


@pytest.fixture
def force_kss_import_error(monkeypatch):
    """kss import를 실패시켜 정규식 폴백 경로를 강제한다."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "kss":
            raise ImportError("simulated: kss 미설치")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_split_sentences_empty_input_returns_empty_list():
    assert split_sentences("") == []
    assert split_sentences("   \n  ") == []


def test_split_sentences_fallback_splits_on_punctuation(force_kss_import_error):
    result = split_sentences("문장 하나. 문장 둘! 문장 셋?")
    assert result == ["문장 하나.", "문장 둘!", "문장 셋?"]


def test_split_sentences_fallback_splits_on_newline(force_kss_import_error):
    result = split_sentences("첫 줄\n둘째 줄\n\n셋째 줄")
    assert result == ["첫 줄", "둘째 줄", "셋째 줄"]


def test_split_sentences_real_kss_returns_nonempty():
    """kss가 실제로 설치되어 있으면 정상 동작해야 한다 (정확한 경계는 검증하지 않음)."""
    result = split_sentences("오프라인 문서 검색 시스템 개요. 완전히 폐쇄된 환경에서 동작한다.")
    assert len(result) >= 1
    assert "".join(result).replace(" ", "") != ""


def test_chunk_text_empty_returns_empty_list(force_kss_import_error):
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_respects_max_chars_boundary(force_kss_import_error):
    text = "가나다라마. " * 30  # 문장 하나가 반복되는 긴 텍스트
    chunks = chunk_text(text, max_chars=40)
    assert len(chunks) > 1
    # 문장 경계를 지키므로 정확히 40자를 넘을 수 있지만(한 문장이 이미 40자 근처),
    # 각 청크가 원본 문장 하나보다 과도하게 크지는 않아야 한다.
    for c in chunks:
        assert len(c) <= 40 + len("가나다라마.") + 1


def test_chunk_text_never_splits_a_sentence_mid_word(force_kss_import_error):
    text = "짧은 문장. " * 10
    chunks = chunk_text(text, max_chars=1000)
    # max_chars가 충분히 크면 모든 문장이 한 청크로 뭉쳐야 한다.
    assert len(chunks) == 1


def test_chunk_text_single_long_sentence_kept_whole(force_kss_import_error):
    """문장 하나가 max_chars보다 길어도 중간에서 자르지 않는다."""
    long_sentence = "가" * 500 + "."
    chunks = chunk_text(long_sentence, max_chars=100)
    assert chunks == [long_sentence]


def test_chunk_text_preserves_paragraph_breaks_when_regrouping(force_kss_import_error):
    """🔴 실사용 검증에서 실제로 잡은 버그(T10.50): 파서가 `\\n`으로 이어붙인

    문단 경계를 여기서 공백으로 뭉개면, 원래 별개 문단이던 텍스트가 한
    문장처럼 이어져 `search/office_link.py`의 딥링크 검색어가 실제
    문서(Word/PDF) 텍스트와 어긋난다(Find가 항상 실패, 실측 확인).
    문단 사이 줄바꿈은 재그룹 후에도 그대로 남아 있어야 한다.
    """
    text = "단말기 네트워크 구성도\n(업데이트 서버, VAN 서버 등 상호작용하는 객체, 행동 등 서술)"
    chunks = chunk_text(text, max_chars=1000)
    assert len(chunks) == 1
    assert "\n" in chunks[0]
    assert chunks[0] == text


def test_chunk_text_uses_space_within_a_paragraph_not_newline(force_kss_import_error):
    """같은 문단 안 문장끼리는 여전히 공백으로 잇는다 — \\n은 문단 경계에서만."""
    text = "첫 문장이다. 둘째 문장이다."
    chunks = chunk_text(text, max_chars=1000)
    assert chunks == ["첫 문장이다. 둘째 문장이다."]
    assert "\n" not in chunks[0]

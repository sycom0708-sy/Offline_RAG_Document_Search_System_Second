"""테스트셋 스키마·로더 (T6.4).

발췌를 인라인으로 담은 **합성** 픽스처와, 실문서용 `chunk_ids` 해석 경로를
모두 확인한다. 실문서 픽스처는 저장소에 없다(계획 §③).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from indexer.fts5.schema import connect
from slm.testset import TestsetError, load_testset, resolve_excerpts

FIXTURE = Path(__file__).parent / "fixtures" / "slm_testset_sample.json"


@pytest.fixture
def sample_testset():
    return load_testset(FIXTURE)


def test_fixture_loads_and_splits_by_expectation(sample_testset):
    assert len(sample_testset) == 6
    assert len(sample_testset.grounded) == 3
    assert len(sample_testset.ungrounded) == 3


def test_fixture_has_a_no_excerpt_case(sample_testset):
    """발췌가 아예 없을 때 자체 지식으로 답하는지가 핵심 검증 항목이다."""
    case = next(c for c in sample_testset.cases if c.id == "ungrounded-no-excerpt")
    assert case.expect_abstain is True
    assert case.excerpts == ()


def test_fixture_carries_no_real_document_text():
    """커밋되는 파일이므로 합성 문서 이름만 들어 있어야 한다."""
    raw = FIXTURE.read_text(encoding="utf-8")
    for item in json.loads(raw)["cases"]:
        for excerpt in item.get("excerpts", []):
            assert excerpt["file_name"].startswith("sample.")


def _write(tmp_path: Path, cases: list[dict]) -> Path:
    path = tmp_path / "testset.json"
    path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_file_message_points_to_builder(tmp_path):
    with pytest.raises(TestsetError, match="build_slm_testset"):
        load_testset(tmp_path / "없음.json")


def test_duplicate_id_is_rejected(tmp_path):
    path = _write(tmp_path, [
        {"id": "a", "question": "질문", "expect_abstain": True},
        {"id": "a", "question": "다른 질문", "expect_abstain": True},
    ])
    with pytest.raises(TestsetError, match="중복"):
        load_testset(path)


def test_grounded_case_requires_keywords(tmp_path):
    """정답 키워드가 없으면 응답 정확도를 잴 수 없다."""
    path = _write(tmp_path, [{"id": "a", "question": "질문", "expect_abstain": False}])
    with pytest.raises(TestsetError, match="keywords"):
        load_testset(path)


def test_empty_cases_rejected(tmp_path):
    path = _write(tmp_path, [])
    with pytest.raises(TestsetError, match="cases"):
        load_testset(path)


def test_broken_json_message_names_the_file(tmp_path):
    path = tmp_path / "testset.json"
    path.write_text("{ 이건 JSON이 아니다", encoding="utf-8")
    with pytest.raises(TestsetError, match="JSON 형식 오류"):
        load_testset(path)


def test_resolve_inline_excerpts(sample_testset):
    case = next(c for c in sample_testset.cases if c.id == "grounded-ram")
    excerpts = resolve_excerpts(case)
    assert len(excerpts) == 1
    assert excerpts[0].location == "사양표"
    assert "8GB" in excerpts[0].text


# --- chunk_ids 해석 (실문서 경로) -----------------------------------------

def _insert_chunk(conn, chunk_id: str, content: str, *, page: int = 2) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO documents (doc_id, file_path, file_name, status, indexed_at)"
        " VALUES (?, ?, ?, 'indexed', ?)",
        ("d1", r"C:\문서\규정.docx", "규정.docx", now),
    )
    conn.execute(
        "INSERT INTO chunks (chunk_id, doc_id, file_path, file_name, type,"
        " page_or_slide, content, caption, keywords, created_at)"
        " VALUES (?, ?, ?, ?, 'text', ?, ?, '', '', ?)",
        (chunk_id, "d1", r"C:\문서\규정.docx", "규정.docx", page, content, now),
    )
    conn.commit()


def test_resolve_chunk_ids_from_index(tmp_path):
    conn = connect(tmp_path / "index.sqlite3")
    _insert_chunk(conn, "chunk-1", "연차는 입사일 기준으로 산정한다.")

    path = _write(tmp_path, [{
        "id": "a", "question": "연차 산정 기준은?", "expect_abstain": False,
        "keywords": ["입사일"], "chunk_ids": ["chunk-1"],
    }])
    case = load_testset(path).cases[0]

    excerpts = resolve_excerpts(case, conn)
    assert len(excerpts) == 1
    assert excerpts[0].file_name == "규정.docx"
    assert excerpts[0].location == "2페이지"  # UI 카드와 같은 표기
    assert "입사일" in excerpts[0].text
    conn.close()


def test_resolve_chunk_ids_keeps_written_order(tmp_path):
    conn = connect(tmp_path / "index.sqlite3")
    _insert_chunk(conn, "chunk-1", "첫 번째")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO chunks (chunk_id, doc_id, file_path, file_name, type,"
        " page_or_slide, content, caption, keywords, created_at)"
        " VALUES ('chunk-2', 'd1', ?, '규정.docx', 'text', 3, '두 번째', '', '', ?)",
        (r"C:\문서\규정.docx", now),
    )
    conn.commit()

    path = _write(tmp_path, [{
        "id": "a", "question": "질문", "expect_abstain": True,
        "chunk_ids": ["chunk-2", "chunk-1"],
    }])
    case = load_testset(path).cases[0]

    assert [e.text for e in resolve_excerpts(case, conn)] == ["두 번째", "첫 번째"]
    conn.close()


def test_missing_chunk_id_raises(tmp_path):
    """발췌가 조용히 비면 모든 모델이 기권해 측정이 통째로 무의미해진다."""
    conn = connect(tmp_path / "index.sqlite3")
    path = _write(tmp_path, [{
        "id": "a", "question": "질문", "expect_abstain": True,
        "chunk_ids": ["없는청크"],
    }])
    case = load_testset(path).cases[0]

    with pytest.raises(TestsetError, match="없는청크"):
        resolve_excerpts(case, conn)
    conn.close()


def test_chunk_ids_without_connection_raises(sample_testset, tmp_path):
    path = _write(tmp_path, [{
        "id": "a", "question": "질문", "expect_abstain": True, "chunk_ids": ["x"],
    }])
    case = load_testset(path).cases[0]
    with pytest.raises(TestsetError, match="DB 연결"):
        resolve_excerpts(case, None)

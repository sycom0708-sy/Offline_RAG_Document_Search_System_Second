"""plan 원문 아카이브 + 출처 판정.

이 PC에는 같은 주제(오프라인 RAG)의 다른 프로젝트가 있고 `~/.claude/plans/`는
전 프로젝트 공용이다. 주제로는 구분되지 않으므로 **구조**로 가른다 —
그 판정이 실제로 되는지가 이 테스트의 핵심이다.
"""

from __future__ import annotations

import pytest

from scripts.archive_plan import (
    ArchiveError,
    already_archived,
    archive,
    check_provenance,
    extract_references,
    plan_title,
)

# 이 저장소를 가리키는 계획서 (실제로 존재하는 경로·심볼만 썼다)
OURS = """# Phase 6: sLM 후보군 실측 검증 구현 계획

`slm/prompt.py`에 템플릿을 두고 `indexer/pipeline.py`의 흐름을 따른다.
`search/hybrid_search.py`의 `hybrid_search()` 결과를 발췌로 넘기고,
`config/settings.py`의 `SlmProfile`을 재사용한다. 테스트는
`tests/test_slm_prompt.py`에 넣는다.
"""

# 다른 프로젝트 계획서 (주제는 같지만 구조가 다르다 — 실제 사례를 본떴다)
THEIRS = """# 표 청크 임베딩 잘림 해결 (설계문서 3장/4장)

`indexing/chunking.py`의 `chunk_text()`를 재사용하고 `indexing/indexer.py`의
`index_file()`만 고친다. `content_original`과 `rows_json`은 건드리지 않는다.
`vector_store.py`, `search.py`, `tests/test_indexer.py`도 그대로다.
`MAX_CHUNK_TOKENS`와 `_expand_block()`은 기존 상수·함수를 쓴다.
`todo_list.md`에 결과를 기록한다.
"""


def test_plan_title():
    assert plan_title(OURS) == "Phase 6: sLM 후보군 실측 검증 구현 계획"
    assert plan_title("본문만 있고 제목이 없다") == ""


def test_extract_references_splits_paths_and_symbols():
    paths, symbols = extract_references(OURS)
    assert "slm/prompt.py" in paths
    assert "hybrid_search" in symbols  # `hybrid_search()`의 괄호는 떼고 본다


def test_extract_references_skips_common_names():
    """어느 프로젝트에나 있는 이름은 판별에 쓸모가 없다."""
    paths, symbols = extract_references("`main` `self` `README.md` `conftest.py`")
    assert paths == set()
    assert symbols == set()


def test_our_plan_is_recognized():
    verdict = check_provenance(OURS)
    assert verdict.belongs_here, verdict.describe()
    assert verdict.ratio > 0.5


def test_foreign_plan_is_rejected():
    """구조가 달라야 갈린다 — 주제는 둘 다 같은 오프라인 RAG다."""
    verdict = check_provenance(THEIRS)
    assert not verdict.belongs_here
    assert "indexing/chunking.py" in verdict.missing
    assert "다른 프로젝트" in verdict.describe()


def test_too_few_references_is_inconclusive():
    """참조가 거의 없는 계획서를 '남의 것'으로 단정하면 안 된다."""
    verdict = check_provenance("# 제목만 있는 계획\n\n산문뿐이다.")
    assert verdict.inconclusive
    assert not verdict.belongs_here
    assert "판정 불가" in verdict.describe()


def test_already_archived():
    text = "# Phase 단계별 구현 계획서\n\n---\n\n# Phase 6: 어쩌구 계획\n"
    assert already_archived("Phase 6: 어쩌구 계획", text) is True
    assert already_archived("Phase 7: 다른 계획", text) is False


# --- 실제 삽입 -------------------------------------------------------------

ARCHIVE_SEED = """# Phase 단계별 구현 계획서

> 머리말. 최신이 위로 쌓인다.

---

# Phase 5: 이전 계획

내용
"""


@pytest.fixture
def archive_doc(tmp_path, monkeypatch):
    path = tmp_path / "archive.md"
    path.write_text(ARCHIVE_SEED, encoding="utf-8")
    monkeypatch.setattr("scripts.archive_plan.ARCHIVE_PATH", path)
    return path


@pytest.fixture
def our_plan(tmp_path):
    path = tmp_path / "random-name.md"
    path.write_text(OURS, encoding="utf-8")
    return path


def test_archive_inserts_below_intro(archive_doc, our_plan):
    title = archive(our_plan)
    assert title == "Phase 6: sLM 후보군 실측 검증 구현 계획"

    lines = archive_doc.read_text(encoding="utf-8").splitlines()
    new_index = lines.index("# Phase 6: sLM 후보군 실측 검증 구현 계획")
    old_index = lines.index("# Phase 5: 이전 계획")
    assert new_index < old_index  # 최신이 위
    assert lines[0] == "# Phase 단계별 구현 계획서"  # 머리말은 그대로


def test_archive_records_provenance(archive_doc, our_plan):
    archive(our_plan)
    text = archive_doc.read_text(encoding="utf-8")
    assert "출처: random-name.md" in text  # 무작위 파일명을 남겨둔다
    assert "작성 " in text and "아카이브 " in text


def test_archive_keeps_original_text_verbatim(archive_doc, our_plan):
    archive(our_plan)
    text = archive_doc.read_text(encoding="utf-8")
    assert "`config/settings.py`의 `SlmProfile`을 재사용한다." in text


def test_archive_refuses_foreign_plan(archive_doc, tmp_path):
    foreign = tmp_path / "foreign.md"
    foreign.write_text(THEIRS, encoding="utf-8")

    with pytest.raises(ArchiveError, match="다른 프로젝트"):
        archive(foreign)
    assert "표 청크" not in archive_doc.read_text(encoding="utf-8")


def test_force_overrides_provenance_warning(archive_doc, tmp_path):
    foreign = tmp_path / "foreign.md"
    foreign.write_text(THEIRS, encoding="utf-8")

    archive(foreign, force=True)
    assert "표 청크" in archive_doc.read_text(encoding="utf-8")


def test_forced_archive_does_not_poison_later_judgments(archive_doc, tmp_path):
    """--force로 남의 계획서를 한 번 넣어도 이후 판정이 무너지면 안 된다.

    처음 구현은 대조 말뭉치에 `.md`를 포함했다가 바로 이 자기 오염에 걸렸다 —
    아카이브 문서가 계획서 원문을 담고 있어서, 한 번 넣으면 그 안의 심볼이
    말뭉치에 섞여 같은 프로젝트의 다음 계획서가 전부 통과해버렸다.
    """
    first = tmp_path / "foreign1.md"
    first.write_text(THEIRS, encoding="utf-8")
    archive(first, force=True)

    second = tmp_path / "foreign2.md"
    second.write_text(THEIRS.replace("표 청크 임베딩 잘림 해결 (설계문서 3장/4장)",
                                     "표 청크 임베딩 잘림 해결 2차"), encoding="utf-8")
    with pytest.raises(ArchiveError, match="다른 프로젝트"):
        archive(second)


def test_archive_refuses_duplicate(archive_doc, our_plan):
    archive(our_plan)
    with pytest.raises(ArchiveError, match="이미 아카이브"):
        archive(our_plan)


def test_archive_reports_missing_plan_file(archive_doc, tmp_path):
    with pytest.raises(ArchiveError, match="계획 파일이 없습니다"):
        archive(tmp_path / "없음.md")

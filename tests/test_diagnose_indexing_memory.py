"""T10.58 재현 스크립트의 순수 로직만 검증한다 (전체 인덱싱 실행은 별도 수동 측정용)."""

from __future__ import annotations

import pytest

from scripts.diagnose_indexing_memory import _collect_source_files, _duplicate


def test_collect_source_files_filters_extensions_and_lock_files(tmp_path):
    (tmp_path / "a.doc").write_text("a")
    (tmp_path / "b.xls").write_text("b")
    (tmp_path / "c.docx").write_text("c")  # 대상 확장자 아님
    (tmp_path / "~$a.doc").write_text("lock")  # Office 잠금 파일

    found = {p.name for p in _collect_source_files(tmp_path, (".doc", ".xls"))}

    assert found == {"a.doc", "b.xls"}


def test_collect_source_files_honors_custom_extensions(tmp_path):
    """`--ext`로 확장자를 바꿀 수 있어야 이미지 보유 가설(후보②)을
    docx/pptx/hwp 같은 실제 이미지 문서로도 검증할 수 있다."""
    (tmp_path / "a.pptx").write_text("a")
    (tmp_path / "b.doc").write_text("b")  # 기본값이 아니라 이번엔 대상 아님

    found = {p.name for p in _collect_source_files(tmp_path, (".pptx",))}

    assert found == {"a.pptx"}


def test_collect_source_files_raises_when_empty(tmp_path):
    (tmp_path / "only.docx").write_text("x")

    with pytest.raises(SystemExit):
        _collect_source_files(tmp_path, (".doc", ".xls"))


def test_duplicate_cycles_through_templates_with_unique_names(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    templates = []
    for name in ("a.doc", "b.xls"):
        path = source_dir / name
        path.write_text(name)
        templates.append(path)

    dest = tmp_path / "dest"
    created = _duplicate(templates, count=5, dest=dest)

    assert len(created) == 5
    assert len({p.name for p in created}) == 5  # 전부 다른 파일명
    assert all(p.is_file() for p in created)
    # 원본 내용을 그대로 복제했는지 (순환 배정 확인)
    assert created[0].read_text() == "a.doc"
    assert created[1].read_text() == "b.xls"
    assert created[2].read_text() == "a.doc"

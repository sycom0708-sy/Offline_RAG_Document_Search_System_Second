"""폴더 스캐너 테스트 (T2.1).

`scan_folder`는 확장자만으로 판단하므로(내용은 검증하지 않음), 대부분의
테스트는 진짜 문서를 생성할 필요 없이 올바른 확장자의 더미 파일로 충분하다.
`samples`(세션 스코프 공유 픽스처)에는 파일을 추가하지 않는다 — 다른 테스트와
공유하는 디렉터리라 여기서 파일을 더하면 실행 순서에 따라 다른 테스트가 깨진다.
"""

from __future__ import annotations

import pytest

from indexer.scanner import count_supported, scan_folder


def test_scan_finds_all_supported_samples(samples):
    """실제 생성된 샘플 문서로도 정상 동작하는지 확인 — 공유 픽스처를 읽기만 한다."""
    root = next(iter(samples.values())).parent
    found = {p.name for p in scan_folder(root)}
    assert found == {p.name for p in samples.values()}


def test_scan_ignores_unsupported_extensions(tmp_path):
    (tmp_path / "doc.txt").write_bytes(b"x")
    (tmp_path / "ignore_me.bak").write_bytes(b"x")
    found = {p.name for p in scan_folder(tmp_path)}
    assert found == {"doc.txt"}


def test_scan_recurses_into_subfolders(tmp_path):
    (tmp_path / "top.txt").write_bytes(b"x")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "deep.txt").write_bytes(b"x")

    found = {p.relative_to(tmp_path) for p in scan_folder(tmp_path)}
    assert len(found) == 2
    assert any(p.parts == ("nested", "deep.txt") for p in found)


def test_scan_skips_hidden_folders(tmp_path):
    (tmp_path / "visible.txt").write_bytes(b"x")
    hidden = tmp_path / ".assets"
    hidden.mkdir()
    (hidden / "cache.txt").write_bytes(b"x")

    found = {p.name for p in scan_folder(tmp_path)}
    assert found == {"visible.txt"}


def test_scan_nonexistent_folder_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        list(scan_folder(tmp_path / "없는폴더"))


def test_scan_empty_folder_returns_nothing(tmp_path):
    assert list(scan_folder(tmp_path)) == []


def test_count_supported_matches_scan_length(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"x")
    (tmp_path / "c.bak").write_bytes(b"x")
    assert count_supported(tmp_path) == 2 == len(list(scan_folder(tmp_path)))

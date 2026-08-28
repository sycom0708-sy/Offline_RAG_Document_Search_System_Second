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


def test_scan_skips_app_data_dir_anywhere_under_root(tmp_path, sample_txt, monkeypatch):
    """앱 자신의 data/ 폴더는 대상 폴더 어디에 있든 스캔 대상이 아니다.

    사용자가 검색 대상 폴더를 앱의 data/ 폴더(또는 이를 포함하는 상위
    폴더)로 잘못 고르면, 인덱싱이 data/index.sqlite3·data/app_state.json·
    data/logs/*.log에 쓰기를 하고 그 변경을 폴더 감시가 다시 감지해
    재인덱싱하는 무한 루프가 실사용 중 재현됐다(2026-08-28) — 특히 진단
    로그 파일이 "문서"로 인식돼 매번 인덱싱되며 내용이 계속 불어났다.
    """
    import shutil

    import indexer.scanner as scanner_module

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(scanner_module, "_DATA_DIR_RESOLVED", data_dir.resolve())

    shutil.copy(sample_txt, tmp_path / "보고서.txt")
    shutil.copy(sample_txt, data_dir / "index_log.txt")

    names = [p.name for p in scan_folder(tmp_path)]
    assert names == ["보고서.txt"]


def test_scan_of_app_data_dir_itself_returns_nothing(tmp_path, sample_txt, monkeypatch):
    """대상 폴더로 data/ 자체를 고른 경우(사용자가 실제로 저지른 실수)."""
    import shutil

    import indexer.scanner as scanner_module

    monkeypatch.setattr(scanner_module, "_DATA_DIR_RESOLVED", tmp_path.resolve())
    shutil.copy(sample_txt, tmp_path / "indexing_log.txt")

    assert list(scan_folder(tmp_path)) == []


def test_scan_skips_office_lock_files(tmp_path, sample_txt):
    """Office 잠금 파일(`~$...`)은 대상이 아니다 (Phase 11-B).

    확장자가 원본과 같아 지원 형식 판정을 통과하지만 내용은 스텁이라 항상
    파싱에 실패한다 — 문서 관리 페이지에 "고칠 수 없는 실패"로 남는다.
    """
    import shutil

    from indexer.scanner import scan_folder

    shutil.copy(sample_txt, tmp_path / "보고서.txt")
    shutil.copy(sample_txt, tmp_path / "~$보고서.txt")

    names = [p.name for p in scan_folder(tmp_path)]
    assert names == ["보고서.txt"]

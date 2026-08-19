"""흩어진 `.assets` 정리 스크립트 테스트 (Phase 11-D).

이 스크립트는 인덱스 DB의 실제 내용에 기대는 안전장치가 핵심이라, DB는
`sqlite3`로 최소 스키마만 만들어 직접 채운다 — `indexer.fts5.schema`
전체를 세우는 것보다 무엇을 검증하는지가 더 분명하다.
"""

from __future__ import annotations

import json
import shutil
import sqlite3

from scripts.clean_assets import (
    find_legacy_asset_dirs,
    find_lingering_references,
    main,
)


def _make_db(path, image_paths: list[str]) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE chunks (chunk_id TEXT, image_json TEXT)")
    for index, image_path in enumerate(image_paths):
        conn.execute(
            "INSERT INTO chunks VALUES (?, ?)",
            (f"c{index}", json.dumps({"image_path": image_path})),
        )
    conn.commit()
    conn.close()


class TestFindLegacyAssetDirs:
    def test_finds_assets_scattered_across_subfolders(self, tmp_path):
        (tmp_path / "a" / ".assets").mkdir(parents=True)
        (tmp_path / "b" / "c" / ".assets").mkdir(parents=True)
        (tmp_path / "d").mkdir()  # .assets 없는 폴더도 섞어 둔다

        found = find_legacy_asset_dirs(tmp_path)

        assert sorted(p.relative_to(tmp_path) for p in found) == sorted(
            p.relative_to(tmp_path) for p in [tmp_path / "a" / ".assets", tmp_path / "b" / "c" / ".assets"]
        )

    def test_no_assets_returns_empty(self, tmp_path):
        (tmp_path / "plain").mkdir()
        assert find_legacy_asset_dirs(tmp_path) == []

    def test_does_not_descend_into_found_assets_dirs(self, tmp_path):
        """`.assets` 안에 우연히 같은 이름의 하위 폴더가 있어도 중복으로 안 잡는다."""
        nested = tmp_path / "doc" / ".assets" / ".assets"
        nested.mkdir(parents=True)

        found = find_legacy_asset_dirs(tmp_path)

        assert found == [tmp_path / "doc" / ".assets"]


class TestFindLingeringReferences:
    def test_flags_paths_still_under_the_legacy_dir(self, tmp_path):
        legacy = tmp_path / "doc" / ".assets"
        legacy.mkdir(parents=True)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chunks (chunk_id TEXT, image_json TEXT)")
        conn.execute(
            "INSERT INTO chunks VALUES ('c1', ?)",
            (json.dumps({"image_path": str(legacy / "img00.png")}),),
        )
        conn.commit()

        offending = find_lingering_references(conn, [legacy])

        assert len(offending) == 1

    def test_paths_already_migrated_are_not_flagged(self, tmp_path):
        legacy = tmp_path / "doc" / ".assets"
        legacy.mkdir(parents=True)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chunks (chunk_id TEXT, image_json TEXT)")
        conn.execute(
            "INSERT INTO chunks VALUES ('c1', ?)",
            (json.dumps({"image_path": str(tmp_path / "central" / "docid" / "img00.png")}),),
        )
        conn.commit()

        assert find_lingering_references(conn, [legacy]) == []

    def test_ignores_rows_without_image_json(self, tmp_path):
        legacy = tmp_path / "doc" / ".assets"
        legacy.mkdir(parents=True)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chunks (chunk_id TEXT, image_json TEXT)")
        conn.execute("INSERT INTO chunks VALUES ('c1', NULL)")
        conn.commit()

        assert find_lingering_references(conn, [legacy]) == []


class TestMainCli:
    def test_dry_run_reports_but_does_not_delete(self, tmp_path, capsys):
        legacy = tmp_path / "docs" / ".assets"
        legacy.mkdir(parents=True)
        (legacy / "img.png").write_bytes(b"x")
        db_path = tmp_path / "index.sqlite3"
        _make_db(db_path, [])  # 이미 재인덱싱된 상태(참조 없음)

        code = main([str(tmp_path / "docs"), "--db", str(db_path)])

        assert code == 0
        assert legacy.is_dir()  # 지우지 않았다
        assert "dry-run" in capsys.readouterr().out

    def test_yes_actually_deletes_when_safe(self, tmp_path, capsys):
        legacy = tmp_path / "docs" / ".assets"
        legacy.mkdir(parents=True)
        (legacy / "img.png").write_bytes(b"x")
        db_path = tmp_path / "index.sqlite3"
        _make_db(db_path, [])

        code = main([str(tmp_path / "docs"), "--db", str(db_path), "--yes"])

        assert code == 0
        assert not legacy.exists()

    def test_refuses_when_db_still_references_legacy_path(self, tmp_path, capsys):
        """🔴 재인덱싱 전에 지우면 이미지 카드가 깨진다 — 안전장치가 막아야 한다."""
        legacy = tmp_path / "docs" / ".assets"
        legacy.mkdir(parents=True)
        image = legacy / "img.png"
        image.write_bytes(b"x")
        db_path = tmp_path / "index.sqlite3"
        _make_db(db_path, [str(image)])  # 아직 옛 경로를 참조 중

        code = main([str(tmp_path / "docs"), "--db", str(db_path), "--yes"])

        assert code == 1
        assert legacy.exists()  # 지우지 않았다
        assert "재인덱싱" in capsys.readouterr().err

    def test_force_overrides_the_safety_check(self, tmp_path, capsys):
        legacy = tmp_path / "docs" / ".assets"
        legacy.mkdir(parents=True)
        image = legacy / "img.png"
        image.write_bytes(b"x")
        db_path = tmp_path / "index.sqlite3"
        _make_db(db_path, [str(image)])

        code = main([str(tmp_path / "docs"), "--db", str(db_path), "--yes", "--force"])

        assert code == 0
        assert not legacy.exists()

    def test_missing_db_refuses_rather_than_guessing_safe(self, tmp_path, capsys):
        legacy = tmp_path / "docs" / ".assets"
        legacy.mkdir(parents=True)

        code = main([str(tmp_path / "docs"), "--db", str(tmp_path / "없음.sqlite3"), "--yes"])

        assert code == 1
        assert legacy.exists()

    def test_nothing_to_clean_is_not_an_error(self, tmp_path, capsys):
        (tmp_path / "docs").mkdir()

        code = main([str(tmp_path / "docs")])

        assert code == 0

    def test_not_a_directory_fails_clearly(self, tmp_path, capsys):
        code = main([str(tmp_path / "존재안함")])

        assert code == 1

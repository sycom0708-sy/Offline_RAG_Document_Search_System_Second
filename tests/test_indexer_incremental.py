"""증분 인덱싱 — 변경 없는 파일은 재파싱을 건너뛴다 (Phase 8, T8.2·T8.3·T8.6)."""

from __future__ import annotations

import shutil
import time

import parser.utils.hashing as hashing_module
from indexer import pipeline
from indexer.fts5.schema import connect
from indexer.incremental import needs_reindex
from indexer.pipeline import index_folder


class TestNeedsReindex:
    def test_new_file_needs_reindex(self, tmp_path, sample_txt):
        target = tmp_path / "a.txt"
        shutil.copy(sample_txt, target)

        conn = connect(":memory:")
        assert needs_reindex(conn, target) is True

    def test_unchanged_mtime_skips_without_hashing(self, tmp_path, sample_txt, monkeypatch):
        target = tmp_path / "a.txt"
        shutil.copy(sample_txt, target)

        conn = connect(":memory:")
        index_folder(conn, tmp_path, embed=False)

        hash_calls = []
        monkeypatch.setattr(
            hashing_module,
            "file_sha256",
            lambda path: hash_calls.append(path) or "unused",
        )

        assert needs_reindex(conn, target) is False
        assert hash_calls == []  # mtime이 같으면 해시 계산 자체를 안 한다

    def test_touched_but_same_content_skips_and_updates_mtime(self, tmp_path, sample_txt):
        """저장 도구가 내용 변경 없이 재저장해 mtime만 바뀐 경우 — 재파싱은
        건너뛰되 다음 실행에서 또 해시를 계산하지 않도록 DB의 mtime을 갱신한다.
        """
        target = tmp_path / "a.txt"
        shutil.copy(sample_txt, target)

        conn = connect(":memory:")
        index_folder(conn, tmp_path, embed=False)

        before = conn.execute("SELECT source_mtime FROM documents").fetchone()[0]
        time.sleep(0.01)
        target.touch()  # 내용은 그대로, mtime만 바뀜
        assert target.stat().st_mtime != before

        assert needs_reindex(conn, target) is False
        after = conn.execute("SELECT source_mtime FROM documents").fetchone()[0]
        assert after == target.stat().st_mtime  # mtime이 최신으로 갱신됐다

    def test_changed_content_needs_reindex(self, tmp_path, sample_txt):
        target = tmp_path / "a.txt"
        shutil.copy(sample_txt, target)

        conn = connect(":memory:")
        index_folder(conn, tmp_path, embed=False)

        time.sleep(0.01)
        target.write_text("완전히 다른 내용", encoding="utf-8")

        assert needs_reindex(conn, target) is True


class TestIndexFolderSkipsUnchangedFiles:
    def test_second_run_skips_unmodified_files(self, tmp_path, sample_txt):
        work = tmp_path / "work"
        work.mkdir()
        for i in range(5):
            shutil.copy(sample_txt, work / f"f{i}.txt")

        conn = connect(":memory:")
        first = index_folder(conn, work, embed=False)
        assert first.indexed == 5
        assert first.skipped == 0

        second = index_folder(conn, work, embed=False)
        assert second.indexed == 0
        assert second.skipped == 5

    def test_only_modified_file_is_reparsed(self, tmp_path, sample_txt, monkeypatch):
        """변경 없는 파일에서는 parse_file 자체가 호출되지 않아야 한다 —
        시간 측정은 flaky해서, "파싱 호출이 아예 안 일어난다"를 결정론적
        증거로 삼는다(DoD: 전체 재인덱싱 대비 소요 시간 유의미하게 단축).
        """
        work = tmp_path / "work"
        work.mkdir()
        for i in range(5):
            shutil.copy(sample_txt, work / f"f{i}.txt")

        conn = connect(":memory:")
        index_folder(conn, work, embed=False)

        time.sleep(0.01)
        (work / "f0.txt").write_text("변경된 내용", encoding="utf-8")

        parsed_paths = []
        real_parse_file = pipeline.parse_file

        def counting_parse_file(path, **kwargs):
            # Phase 11-D부터 파이프라인이 asset_dir=...을 넘긴다 — 그대로
            # 전달해야 실제 parse_file()과 같은 경로로 동작한다.
            parsed_paths.append(path)
            return real_parse_file(path, **kwargs)

        monkeypatch.setattr(pipeline, "parse_file", counting_parse_file)

        report = index_folder(conn, work, embed=False)

        assert report.indexed == 1
        assert report.skipped == 4
        assert [p.name for p in parsed_paths] == ["f0.txt"]


class TestStaleImageChunkIds:
    def test_pruned_document_image_chunk_ids_are_reported(self, tmp_path, sample_docx):
        """T10.5의 폴더 교체 프루닝으로 지워진 문서의 이미지 청크도 캐시
        무효화 대상에 포함돼야 한다.
        """
        folder = tmp_path / "folder"
        folder.mkdir()
        shutil.copy(sample_docx, folder / "with_image.docx")

        conn = connect(":memory:")
        index_folder(conn, folder, embed=False)
        image_chunk_ids = [
            r[0] for r in conn.execute("SELECT chunk_id FROM chunks WHERE type = 'image'")
        ]
        assert image_chunk_ids  # 픽스처 문서에 이미지 청크가 있어야 이 테스트가 유효

        empty_folder = tmp_path / "empty"
        empty_folder.mkdir()
        report = index_folder(conn, empty_folder, embed=False)  # 폴더 교체 → with_image.docx 프루닝

        assert set(image_chunk_ids) <= set(report.stale_image_chunk_ids)

"""백그라운드 인덱싱 파이프라인 테스트 (T2.8).

`samples`(세션 스코프 공유 픽스처)는 읽기만 하고, 실패 격리 테스트처럼 폴더
내용을 조작해야 하는 경우는 `tmp_path`에 필요한 파일만 복사해 독립된
디렉터리를 구성한다.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from indexer.fts5.schema import connect
from indexer.pipeline import IndexingThread, index_folder


def test_index_folder_stores_all_documents(samples, tmp_path):
    conn = connect(":memory:")
    root = next(iter(samples.values())).parent
    report = index_folder(conn, root)

    assert report.failures == []
    assert report.indexed == len(samples)
    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert doc_count == len(samples)


def test_reindexing_a_new_folder_removes_previous_folder_documents(tmp_path, sample_txt):
    """T10.5: 대상 폴더를 바꾸면 이전 폴더 문서를 완전히 교체한다(사용자 확정).

    다른 PC·다른 세션에서 인덱싱한 흔적이 계속 쌓여 검색 결과에 섞여
    나오던 것을 실사용 중 실제로 겪었다(2026-08-09).
    """
    old_folder = tmp_path / "old"
    old_folder.mkdir()
    shutil.copy(sample_txt, old_folder / "old.txt")

    new_folder = tmp_path / "new"
    new_folder.mkdir()
    shutil.copy(sample_txt, new_folder / "new.txt")

    conn = connect(":memory:")
    index_folder(conn, old_folder, embed=False)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1

    report = index_folder(conn, new_folder, embed=False)

    names = [r[0] for r in conn.execute("SELECT file_name FROM documents")]
    assert names == ["new.txt"]  # old.txt는 지워졌다
    assert report.pruned == 1
    assert report.indexed == 1


def test_reindexing_the_same_folder_does_not_prune_its_own_documents(tmp_path, sample_txt):
    """같은 폴더를 다시 인덱싱할 때는 그 안의 문서를 지우면 안 된다."""
    folder = tmp_path / "work"
    folder.mkdir()
    shutil.copy(sample_txt, folder / "a.txt")
    shutil.copy(sample_txt, folder / "b.txt")

    conn = connect(":memory:")
    index_folder(conn, folder, embed=False)
    report = index_folder(conn, folder, embed=False)  # 같은 폴더 재실행

    assert report.pruned == 0
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_file_moved_to_subfolder_prunes_its_old_path(tmp_path, sample_txt):
    """T10.5 실측 함정: 같은 대상 폴더 **안에서** 파일이 서브폴더로 옮겨진 경우.

    처음 짠 버전은 "대상 폴더 바깥이면 지운다"(경로 접두사 비교)였는데,
    옮겨진 파일의 옛 경로도 여전히 "폴더 안"이라 접두사 비교로는 안
    지워지고 유령 문서로 남았다 — 재인덱싱 직후 실사용에서 실제로 겪었다
    (파일 10개인데 문서 17개로 나옴). 이번 스캔 결과와 직접 대조하도록
    고쳐서 해결했다.
    """
    root = tmp_path / "root"
    root.mkdir()
    original = root / "문서.txt"
    shutil.copy(sample_txt, original)

    conn = connect(":memory:")
    index_folder(conn, root, embed=False)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1

    sub = root / "sub1"
    sub.mkdir()
    original.rename(sub / "문서.txt")  # 같은 root 안에서 서브폴더로 이동

    report = index_folder(conn, root, embed=False)

    paths = [r[0] for r in conn.execute("SELECT file_path FROM documents")]
    assert len(paths) == 1
    assert "sub1" in paths[0]  # 새 위치의 문서만 남아야 한다
    assert report.pruned == 1  # 옛 경로의 유령 문서가 지워졌다


def test_prune_ignores_case_on_windows_style_paths(tmp_path, sample_txt):
    """Windows는 경로 대소문자를 구분하지 않는다 — 같은 폴더인데 대소문자만
    다르다고 "바깥"으로 오판하면 매번 재인덱싱마다 자기 자신을 지운다."""
    folder = tmp_path / "MyFolder"
    folder.mkdir()
    shutil.copy(sample_txt, folder / "a.txt")

    conn = connect(":memory:")
    index_folder(conn, folder, embed=False)

    differently_cased = Path(str(folder).replace("MyFolder", "myfolder"))
    report = index_folder(conn, differently_cased, embed=False)

    assert report.pruned == 0


def test_index_folder_isolates_broken_file(tmp_path, sample_txt):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "good.txt")
    (work / "broken.pdf").write_bytes(b"not a real pdf")

    conn = connect(":memory:")
    report = index_folder(conn, work)

    assert len(report.failures) == 1
    assert report.failures[0][0].name == "broken.pdf"
    # 실패한 파일이 있어도 나머지는 정상 저장돼야 한다.
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_legacy_conversion_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    """T10.2: LegacyOfficeParser는 예외를 던지지 않고 document.errors에만 담는다.

    그대로 두면 `report.failures`가 비어 있어 인덱싱이 조용히 "성공"한 것처럼
    보인다 — 실제로 겪은 버그다(Phase 3 재측정 중 LibreOffice 미배치 상태에서
    .doc/.xls가 0청크로 조용히 빠짐).
    """
    from parser.utils.libreoffice import is_missing_libreoffice_error

    (tmp_path / "legacy.doc").write_bytes(b"not a real doc")
    monkeypatch.setattr(
        "parser.utils.libreoffice.find_soffice", lambda: None
    )

    conn = connect(":memory:")
    report = index_folder(conn, tmp_path, embed=False)

    assert len(report.failures) == 1
    path, message = report.failures[0]
    assert path.name == "legacy.doc"
    assert is_missing_libreoffice_error(message)
    # 실패해도 문서 자체는 저장된다(status='failed', 0청크) — 재인덱싱 시
    # 그대로 재시도된다.
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_missing_embedding_model_is_warning_not_failure(tmp_path, sample_txt, monkeypatch):
    """모델이 없어 벡터를 못 만든 것은 '파일 인덱싱 실패'가 아니다.

    키워드 검색은 그대로 동작하므로, 두 상황을 같은 목록에 섞으면 호출자가
    "인덱싱이 실패했다"고 잘못 판단하게 된다.
    """
    from indexer import pipeline

    monkeypatch.setattr(pipeline, "_prepare_embedder", lambda profile=None: (None, "모델 없음"))

    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "good.txt")

    conn = connect(":memory:")
    report = index_folder(conn, work)

    assert report.failures == []
    assert report.warnings == ["모델 없음"]
    assert report.ok
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0


def test_index_folder_embeds_with_the_requested_profile(tmp_path, sample_txt, heavy_embedder):
    """T10.37 — `profile=`을 넘기면 그 모델로 벡터를 만든다.

    🔴 수정 전에는 `index_folder()`가 이 인자 자체를 안 받아서, PC 성능
    선택을 권장 모드(HEAVY)로 바꿔도 인덱싱은 항상 경량 모델로만 벡터를
    만들었다 — "모드 전환 시 벡터 자동 보완"도 결국 이 함수를 다시 부르는
    것이라 권장 모드 벡터를 끝내 못 채웠다. `heavy_embedder` 픽스처가
    KURE-v1 미설치 시 스킵해준다.
    """
    from config.settings import HEAVY

    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "good.txt")

    conn = connect(":memory:")
    report = index_folder(conn, work, profile=HEAVY)

    assert report.failures == []
    assert report.embedded > 0
    model_keys = {
        row[0]
        for row in conn.execute("SELECT DISTINCT model FROM chunk_vectors")
    }
    assert model_keys == {HEAVY.key}


def test_index_folder_progress_callback_fires_for_every_file(tmp_path, sample_txt):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "a.txt")
    shutil.copy(sample_txt, work / "b.txt")

    calls = []
    conn = connect(":memory:")
    index_folder(conn, work, on_progress=lambda done, total, path: calls.append((done, total)))

    assert calls == [(1, 2), (2, 2)]


def test_index_folder_stops_early_when_stop_event_set(tmp_path, sample_txt):
    work = tmp_path / "work"
    work.mkdir()
    for i in range(5):
        shutil.copy(sample_txt, work / f"f{i}.txt")

    stop_event = threading.Event()
    conn = connect(":memory:")

    def on_progress(done, total, path):
        if done == 2:
            stop_event.set()

    index_folder(conn, work, on_progress=on_progress, stop_event=stop_event)
    stored = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert stored <= 2


def test_indexing_thread_runs_in_background_and_reports_done(tmp_path, sample_txt):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "a.txt")
    db_path = tmp_path / "index.sqlite3"

    result = {}
    done_event = threading.Event()

    def on_done(report):
        result["report"] = report
        done_event.set()

    thread = IndexingThread(db_path, work, on_done=on_done)
    main_thread_id = threading.get_ident()
    thread.start()
    assert done_event.wait(timeout=60), "인덱싱 스레드가 제한 시간 내에 끝나지 않음"
    thread.join()

    assert result["report"].failures == []
    assert thread.ident != main_thread_id  # 실제로 별도 스레드에서 실행됐는지 확인

    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    conn.close()


# --- Phase 11-B: 문서 관리 페이지가 쓰는 지표 (DESIGN §14.4.1) ---------------


def test_report_separates_created_from_updated(tmp_path, sample_txt):
    """`신규`/`변경`을 따로 센다 — 판별은 원래부터 둘을 구분하고 있었다."""
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "a.txt")
    conn = connect(":memory:")

    first = index_folder(conn, work, embed=False)
    assert (first.created, first.updated, first.skipped) == (1, 0, 0)

    # 내용을 바꾸면 "변경"으로 잡혀야 한다(신규가 아니다).
    #
    # mtime을 손으로 밀어 준다. `classify_file()`은 mtime을 먼저 보는데,
    # 복사 직후 덮어쓰면 Windows 시스템 클럭 틱(약 15.6ms) 안에 들어가 mtime이
    # 같은 값으로 찍힐 수 있고, 그러면 내용이 달라도 "그대로"로 판정된다 —
    # 실제로 전체 실행에서만 이 테스트가 실패했다(단독 실행은 통과). 시계에
    # 기대지 않고 판정 자체를 검증하도록 고정한다.
    target = work / "a.txt"
    target.write_text("완전히 다른 내용", encoding="utf-8")
    stat = target.stat()
    os.utime(target, (stat.st_atime, stat.st_mtime + 10))
    second = index_folder(conn, work, embed=False)
    assert (second.created, second.updated, second.skipped) == (0, 1, 0)

    # 그대로면 둘 다 0이고 미변경만 는다 (Phase 8).
    third = index_folder(conn, work, embed=False)
    assert (third.created, third.updated, third.skipped) == (0, 0, 1)


def test_report_scanned_counts_every_target_file(tmp_path, sample_txt):
    """`총`은 indexed+skipped 파생이 아니라 실제 대상 파일 수다.

    파싱에 실패한 파일은 indexed에도 skipped에도 안 잡혀, 파생값으로 두면
    파일 진단에는 실패가 떠 있는데 총계에서는 사라진다.
    """
    work = tmp_path / "work"
    work.mkdir()
    for i in range(3):
        shutil.copy(sample_txt, work / f"f{i}.txt")

    conn = connect(":memory:")
    report = index_folder(conn, work, embed=False)
    assert report.scanned == 3


def test_stage_callback_reports_parsing(tmp_path, sample_txt):
    from indexer.pipeline import STAGE_DONE, STAGE_PARSING

    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "a.txt")

    stages = []
    conn = connect(":memory:")
    index_folder(conn, work, embed=False, on_stage=lambda s, d, t: stages.append(s))

    assert stages[0] == STAGE_PARSING
    assert stages[-1] == STAGE_DONE


def test_failed_document_paths_reads_persisted_failures(tmp_path, sample_txt):
    """실패는 인덱스에 남아 있으므로 앱을 껐다 켜도 다시 읽을 수 있어야 한다."""
    from indexer.pipeline import failed_document_paths

    work = tmp_path / "work"
    work.mkdir()
    target = work / "a.txt"
    shutil.copy(sample_txt, target)

    conn = connect(":memory:")
    index_folder(conn, work, embed=False)
    assert failed_document_paths(conn) == []

    conn.execute("UPDATE documents SET status = 'failed'")
    conn.commit()
    assert [p.name for p in failed_document_paths(conn)] == ["a.txt"]


def test_reindex_files_forces_reparse_of_unchanged_files(tmp_path, sample_txt):
    """🔴 재시도의 존재 이유 — 실패한 문서도 mtime·해시가 저장돼 있어
    보통의 재인덱싱에서는 `UNCHANGED`로 건너뛰어진다."""
    from indexer.incremental import FileChange, classify_file
    from indexer.pipeline import reindex_files

    work = tmp_path / "work"
    work.mkdir()
    target = work / "a.txt"
    shutil.copy(sample_txt, target)

    conn = connect(":memory:")
    index_folder(conn, work, embed=False)

    # 파일을 손대지 않았으니 일반 판정은 "그대로"다.
    assert classify_file(conn, target) is FileChange.UNCHANGED
    assert index_folder(conn, work, embed=False).skipped == 1

    # 재시도는 그 판정을 무시하고 실제로 다시 파싱한다.
    report = reindex_files(conn, [target], embed=False)
    assert report.skipped == 0
    assert report.indexed == 1


def test_reindex_files_does_not_prune_other_documents(tmp_path, sample_txt):
    """🔴 재시도가 폴더 정리를 부르면 대상 밖 문서가 통째로 사라진다.

    `_prune_stale_documents()`는 "이번 스캔에 없는 문서를 지운다"인데, 재시도
    대상은 폴더의 일부뿐이라 나머지 전부가 그 조건에 걸린다(T10.5와 같은
    함정의 반대 방향).
    """
    from indexer.pipeline import reindex_files

    work = tmp_path / "work"
    work.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        shutil.copy(sample_txt, work / name)

    conn = connect(":memory:")
    index_folder(conn, work, embed=False)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3

    reindex_files(conn, [work / "a.txt"], embed=False)

    names = sorted(r[0] for r in conn.execute("SELECT file_name FROM documents"))
    assert names == ["a.txt", "b.txt", "c.txt"]


def test_indexing_thread_with_files_runs_retry_path(tmp_path, sample_txt):
    work = tmp_path / "work"
    work.mkdir()
    for name in ("a.txt", "b.txt"):
        shutil.copy(sample_txt, work / name)
    db_path = tmp_path / "index.sqlite3"

    conn = connect(db_path)
    index_folder(conn, work, embed=False)
    conn.close()

    result = {}
    done_event = threading.Event()
    thread = IndexingThread(
        db_path,
        work,
        on_done=lambda report: (result.update(report=report), done_event.set()),
        embed=False,
        files=[work / "a.txt"],
    )
    thread.start()
    assert done_event.wait(timeout=60)
    thread.join()

    report = result["report"]
    assert report.scanned == 1  # b.txt는 대상이 아니었다
    assert report.indexed == 1
    assert report.skipped == 0  # 강제 재파싱


def test_indexing_thread_reports_done_even_when_the_run_fails(tmp_path):
    """🔴 예외가 나도 완료 통지는 반드시 간다 (Phase 11-B).

    통지가 안 가면 문서 관리 페이지가 "인덱싱 중"에 갇히고 `인덱스 업데이트`
    버튼이 계속 비활성이라 앱을 껐다 켜야 회복된다. 대상 폴더가 사라진 경우가
    실제로 밟을 수 있는 경로다(`scan_folder()`가 예외를 던진다).
    """
    missing = tmp_path / "없어진폴더"
    result = {}
    done_event = threading.Event()

    thread = IndexingThread(
        tmp_path / "index.sqlite3",
        missing,
        on_done=lambda report: (result.update(report=report), done_event.set()),
        embed=False,
    )
    thread.start()
    assert done_event.wait(timeout=30), "예외가 난 실행이 완료를 통지하지 않았다"
    thread.join()

    report = result["report"]
    assert report.warnings  # 왜 아무 일도 안 일어났는지는 남아야 한다
    assert report.indexed == 0


# --- Phase 11-D: 이미지 자산 중앙화 (data/assets/<doc_id>/) -----------------


def test_extracted_images_land_in_the_central_assets_dir(tmp_path, monkeypatch, sample_docx):
    """🔴 이미지가 문서 폴더 옆 `.assets`가 아니라 `ASSETS_DIR/<doc_id>/`에 생겨야 한다."""
    from indexer import pipeline as pipeline_module
    from parser.utils.ids import make_doc_id

    assets_dir = tmp_path / "central_assets"
    monkeypatch.setattr(pipeline_module, "ASSETS_DIR", assets_dir)

    work = tmp_path / "work"
    work.mkdir()
    target = work / "문서.docx"
    shutil.copy(sample_docx, target)

    conn = connect(":memory:")
    index_folder(conn, work, embed=False)

    doc_id = make_doc_id(target)
    doc_assets = assets_dir / doc_id
    assert doc_assets.is_dir()
    assert any(doc_assets.iterdir())
    # 원본 문서 옆에는 아무것도 안 생겨야 한다.
    assert not (work / ".assets").exists()


def test_stored_image_path_points_into_the_central_dir(tmp_path, monkeypatch, sample_docx):
    from indexer import pipeline as pipeline_module

    assets_dir = tmp_path / "central_assets"
    monkeypatch.setattr(pipeline_module, "ASSETS_DIR", assets_dir)

    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_docx, work / "문서.docx")

    conn = connect(":memory:")
    index_folder(conn, work, embed=False)

    row = conn.execute(
        "SELECT image_json FROM chunks WHERE image_json IS NOT NULL LIMIT 1"
    ).fetchone()
    assert row is not None
    image_path = Path(json.loads(row[0])["image_path"])
    assert assets_dir in image_path.parents


def test_pruning_a_document_removes_its_asset_folder(tmp_path, monkeypatch, sample_docx):
    """🔴 문서가 지워지면 그 doc_id 폴더도 함께 지워져야 한다 — 안 지우면
    중앙화된 곳이 지워진 문서의 잔재로 영원히 불어난다."""
    from indexer import pipeline as pipeline_module
    from parser.utils.ids import make_doc_id

    assets_dir = tmp_path / "central_assets"
    monkeypatch.setattr(pipeline_module, "ASSETS_DIR", assets_dir)

    old_folder = tmp_path / "old"
    old_folder.mkdir()
    target = old_folder / "문서.docx"
    shutil.copy(sample_docx, target)

    conn = connect(":memory:")
    index_folder(conn, old_folder, embed=False)
    doc_id = make_doc_id(target)
    assert (assets_dir / doc_id).is_dir()

    new_folder = tmp_path / "new"
    new_folder.mkdir()
    shutil.copy(sample_docx, new_folder / "다른문서.docx")

    index_folder(conn, new_folder, embed=False)  # old_folder는 이제 스캔 대상이 아니다

    assert not (assets_dir / doc_id).exists()

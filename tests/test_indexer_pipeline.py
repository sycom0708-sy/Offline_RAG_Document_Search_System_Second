"""백그라운드 인덱싱 파이프라인 테스트 (T2.8).

`samples`(세션 스코프 공유 픽스처)는 읽기만 하고, 실패 격리 테스트처럼 폴더
내용을 조작해야 하는 경우는 `tmp_path`에 필요한 파일만 복사해 독립된
디렉터리를 구성한다.
"""

from __future__ import annotations

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

    monkeypatch.setattr(pipeline, "_prepare_embedder", lambda: (None, "모델 없음"))

    work = tmp_path / "work"
    work.mkdir()
    shutil.copy(sample_txt, work / "good.txt")

    conn = connect(":memory:")
    report = index_folder(conn, work)

    assert report.failures == []
    assert report.warnings == ["모델 없음"]
    assert report.ok
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0


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

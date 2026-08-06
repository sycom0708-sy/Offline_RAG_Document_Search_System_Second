"""백그라운드 인덱싱 파이프라인 테스트 (T2.8).

`samples`(세션 스코프 공유 픽스처)는 읽기만 하고, 실패 격리 테스트처럼 폴더
내용을 조작해야 하는 경우는 `tmp_path`에 필요한 파일만 복사해 독립된
디렉터리를 구성한다.
"""

from __future__ import annotations

import shutil
import threading

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

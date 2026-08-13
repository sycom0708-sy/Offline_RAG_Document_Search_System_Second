"""실시간 폴더 감시 테스트 (T8.5)."""

from __future__ import annotations

import threading
import time

import pytest

from indexer.incremental.watcher import FolderWatcher

_FAST_DEBOUNCE = 0.05


def test_creating_a_file_triggers_on_change(tmp_path):
    fired = threading.Event()
    watcher = FolderWatcher(tmp_path, on_change=fired.set, debounce_seconds=_FAST_DEBOUNCE)
    watcher.start()
    try:
        (tmp_path / "새파일.txt").write_text("내용", encoding="utf-8")
        assert fired.wait(timeout=5), "파일 생성 후 제한 시간 내에 on_change가 불리지 않음"
    finally:
        watcher.stop()


def test_modifying_a_file_triggers_on_change(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("원본", encoding="utf-8")

    fired = threading.Event()
    watcher = FolderWatcher(tmp_path, on_change=fired.set, debounce_seconds=_FAST_DEBOUNCE)
    watcher.start()
    try:
        target.write_text("수정됨", encoding="utf-8")
        assert fired.wait(timeout=5)
    finally:
        watcher.stop()


def test_change_in_subfolder_is_detected(tmp_path):
    """재귀적으로 감시해야 한다 — 서브폴더 변경도 잡아야 한다."""
    sub = tmp_path / "sub"
    sub.mkdir()

    fired = threading.Event()
    watcher = FolderWatcher(tmp_path, on_change=fired.set, debounce_seconds=_FAST_DEBOUNCE)
    watcher.start()
    try:
        (sub / "b.txt").write_text("내용", encoding="utf-8")
        assert fired.wait(timeout=5)
    finally:
        watcher.stop()


def test_burst_of_changes_is_debounced_into_a_single_call(tmp_path):
    """저장 도구가 짧은 시간에 여러 이벤트를 낼 수 있다 — 디바운스로 묶어야 한다."""
    call_count = 0
    lock = threading.Lock()
    done = threading.Event()

    def on_change():
        nonlocal call_count
        with lock:
            call_count += 1
        done.set()

    watcher = FolderWatcher(tmp_path, on_change=on_change, debounce_seconds=0.3)
    watcher.start()
    try:
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text(str(i), encoding="utf-8")
            time.sleep(0.02)  # 디바운스 창(0.3초)보다 훨씬 짧은 간격으로 연달아 발생시킨다

        assert done.wait(timeout=5)
        time.sleep(0.5)  # 혹시 더 불릴 여지가 있는지 확인할 시간을 준다
        with lock:
            assert call_count == 1
    finally:
        watcher.stop()


def test_stop_joins_the_observer_thread(tmp_path):
    watcher = FolderWatcher(tmp_path, on_change=lambda: None, debounce_seconds=_FAST_DEBOUNCE)
    watcher.start()
    watcher.stop()
    assert not watcher._observer.is_alive()


def test_starting_on_missing_folder_raises(tmp_path):
    missing = tmp_path / "없는폴더"
    watcher = FolderWatcher(missing, on_change=lambda: None)
    with pytest.raises(Exception):
        watcher.start()

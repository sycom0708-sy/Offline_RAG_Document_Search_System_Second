"""인덱싱 진단 로그 모듈의 프로세스 메모리 측정 (T10.58)."""

from __future__ import annotations

import os

import indexer.index_log as index_log_module


def test_current_process_memory_mb_queries_own_pid(monkeypatch):
    """자기 자신의 PID로 `slm.runtime.process_memory_mb()`를 부르는지 확인.

    새 측정 로직이 아니라 기존 범용 함수(원래 llama-server 측정용)를
    재사용하는 것이므로, 이 얇은 wrapper가 올바른 PID를 넘기는지만
    검증하면 충분하다.
    """
    seen_pids: list[int] = []

    def fake_process_memory_mb(pid: int) -> tuple[float, float] | None:
        seen_pids.append(pid)
        return (123.4, 200.0)

    monkeypatch.setattr("slm.runtime.process_memory_mb", fake_process_memory_mb)

    result = index_log_module.current_process_memory_mb()

    assert result == (123.4, 200.0)
    assert seen_pids == [os.getpid()]


def test_current_process_memory_mb_returns_none_on_failure(monkeypatch):
    """측정 실패(비-Windows 등) 시 조용히 None을 돌려준다."""
    monkeypatch.setattr("slm.runtime.process_memory_mb", lambda pid: None)

    assert index_log_module.current_process_memory_mb() is None


def test_current_process_memory_detail_queries_own_pid(monkeypatch):
    """워킹셋만으로는 OS 트리밍 때문에 실제 증가 여부를 오판할 수 있어
    private bytes(커밋)까지 담은 상세 버전도 자기 PID로 조회하는지 확인 (T10.58)."""
    from slm.runtime import ProcessMemoryDetail

    seen_pids: list[int] = []
    expected = ProcessMemoryDetail(
        working_set_mb=100.0, peak_working_set_mb=150.0,
        private_bytes_mb=300.0, peak_private_bytes_mb=320.0,
    )

    def fake_detail(pid: int):
        seen_pids.append(pid)
        return expected

    monkeypatch.setattr("slm.runtime.process_memory_detail_mb", fake_detail)

    result = index_log_module.current_process_memory_detail()

    assert result is expected
    assert seen_pids == [os.getpid()]

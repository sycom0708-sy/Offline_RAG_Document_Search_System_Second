"""sLM 서버 수명주기 테스트 (Phase 7 — 유휴 자동 종료).

실제 모델을 올리지 않고 `runtime.start_server`/`stop_server`를 가짜로 바꿔
**수명주기 규칙 자체**를 검증한다. 실제 기동은 맨 아래 `@pytest.mark.slow`
종단 테스트에서 한 번만 확인한다.

이 Phase에서 유휴 종료가 핵심 요구사항인 이유: 채택 모델이 4.8GB를 쓰는데
16GB PC에서 안드로이드 스튜디오 빌드와 동시에 돌아가는 것이 전제다. 안 쓰는
동안 물고 있으면 그 자체가 결함이다.
"""

from __future__ import annotations

import threading
import time

import pytest

from config.settings import SLM_RECOMMENDED, get_slm_profile
from slm import runtime
from slm.service import SlmNotInstalledError, SlmService


class _FakeProcess:
    def __init__(self) -> None:
        self.alive = True
        self.terminated = 0

    def poll(self):
        return None if self.alive else 0


@pytest.fixture
def fake_server(monkeypatch):
    """서버 기동/종료를 가로채 호출 횟수만 센다."""
    state = {"starts": 0, "stops": 0, "processes": []}

    def fake_start(model_path, **kwargs):
        state["starts"] += 1
        state["last_kwargs"] = kwargs
        process = _FakeProcess()
        state["processes"].append(process)
        handle = runtime.ServerHandle(port=12345, load_seconds=0.1, pid=999)
        return handle, process

    def fake_stop(process):
        state["stops"] += 1
        process.alive = False
        process.terminated += 1

    monkeypatch.setattr(runtime, "start_server", fake_start)
    monkeypatch.setattr(runtime, "stop_server", fake_stop)
    monkeypatch.setattr(runtime, "is_available", lambda: True)
    monkeypatch.setattr(type(get_slm_profile(SLM_RECOMMENDED)), "is_installed", lambda self: True)
    return state


class TestLifecycle:
    def test_first_call_starts_the_server(self, fake_server):
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        assert fake_server["starts"] == 1
        assert service.is_running() is True

    def test_second_call_reuses_the_running_server(self, fake_server):
        """재사용이 안 되면 요약마다 기동 비용(4.7초)이 붙는다."""
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        service.ensure_ready()
        assert fake_server["starts"] == 1

    def test_shutdown_stops_the_process(self, fake_server):
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        service.shutdown()
        assert fake_server["stops"] == 1
        assert service.is_running() is False

    def test_shutdown_is_idempotent(self, fake_server):
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        service.shutdown()
        service.shutdown()
        assert fake_server["stops"] == 1

    def test_restarts_after_shutdown(self, fake_server):
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        service.shutdown()
        service.ensure_ready()
        assert fake_server["starts"] == 2

    def test_dead_process_is_replaced(self, fake_server):
        """서버가 혼자 죽었으면(크래시 등) 다음 요청에 새로 올라와야 한다."""
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        fake_server["processes"][0].alive = False

        service.ensure_ready()
        assert fake_server["starts"] == 2

    def test_context_manager_shuts_down(self, fake_server):
        with SlmService(SLM_RECOMMENDED) as service:
            service.ensure_ready()
        assert fake_server["stops"] == 1

    def test_passes_extra_server_args(self, fake_server):
        """Qwen3.5는 `--reasoning off`가 없으면 **빈 응답**을 준다 (Phase 6 실측)."""
        SlmService(SLM_RECOMMENDED).ensure_ready()
        assert fake_server["last_kwargs"]["extra_args"] == ["--reasoning", "off"]


class TestIdleTimeout:
    def test_idle_timeout_shuts_the_server_down(self, fake_server):
        """**이 Phase의 핵심 요구사항** — 안 쓰면 메모리를 돌려줘야 한다."""
        service = SlmService(SLM_RECOMMENDED, idle_timeout_sec=0.15)
        service.ensure_ready()
        service.touch()

        deadline = time.time() + 3
        while service.is_running() and time.time() < deadline:
            time.sleep(0.02)

        assert service.is_running() is False
        assert fake_server["stops"] == 1

    def test_touch_postpones_the_shutdown(self, fake_server):
        service = SlmService(SLM_RECOMMENDED, idle_timeout_sec=0.3)
        service.ensure_ready()
        for _ in range(4):
            service.touch()
            time.sleep(0.1)
        assert service.is_running() is True  # 계속 썼으니 살아 있어야 한다
        service.shutdown()

    def test_zero_timeout_disables_auto_shutdown(self, fake_server):
        service = SlmService(SLM_RECOMMENDED, idle_timeout_sec=0)
        service.ensure_ready()
        service.touch()
        time.sleep(0.2)
        assert service.is_running() is True
        service.shutdown()


class TestConcurrency:
    def test_concurrent_callers_start_only_one_server(self, fake_server):
        """잠그지 않으면 서버가 두 대 떠서 메모리를 두 배 쓴다 — 정확히 피하려던 상황."""
        service = SlmService(SLM_RECOMMENDED)
        barrier = threading.Barrier(4)
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                service.ensure_ready()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []
        assert fake_server["starts"] == 1
        service.shutdown()


class TestAvailability:
    def test_missing_model_raises_with_guidance(self, monkeypatch):
        monkeypatch.setattr(
            type(get_slm_profile(SLM_RECOMMENDED)), "is_installed", lambda self: False
        )
        service = SlmService(SLM_RECOMMENDED)
        with pytest.raises(SlmNotInstalledError, match="모델 관리"):
            service.ensure_ready()

    def test_missing_binary_raises_with_guidance(self, monkeypatch):
        monkeypatch.setattr(
            type(get_slm_profile(SLM_RECOMMENDED)), "is_installed", lambda self: True
        )
        monkeypatch.setattr(runtime, "is_available", lambda: False)
        service = SlmService(SLM_RECOMMENDED)
        with pytest.raises(SlmNotInstalledError, match="llama.cpp"):
            service.ensure_ready()

    def test_switching_profile_drops_the_running_server(self, fake_server):
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        service.set_profile("exaone-4.0-1.2b")
        assert service.is_running() is False
        assert service.profile.key == "exaone-4.0-1.2b"


# --- 종단 (실제 모델을 올린다) ---------------------------------------------


@pytest.mark.slow
def test_end_to_end_summary():
    from search.hybrid_search import HybridResult
    from indexer.fts5.search import SearchResult
    from parser.schema import ChunkType
    from slm.summarize import SummaryStatus, summarize

    if runtime.find_llama_server() is None:
        pytest.skip("llama.cpp 바이너리 미설치 — `python -m scripts.setup_llamacpp` 후 재시도")

    profile = get_slm_profile(SLM_RECOMMENDED)
    if not profile.is_installed():
        pytest.skip(f"{profile.label} 미설치 — `python -m slm.download {SLM_RECOMMENDED}` 후 재시도")

    result = SearchResult(
        chunk_id="c1", doc_id="d1", file_path="x", file_name="사양.xlsx",
        type=ChunkType.TABLE, page_or_slide=1,
        content="구분 | 최소 사양 | 권장 사양\nRAM | 8GB | 16GB", caption="사양표", score=-1.0,
    )
    results = [HybridResult(result, 0.9, False)]

    with SlmService(SLM_RECOMMENDED, idle_timeout_sec=0) as service:
        summary = summarize("권장 사양의 RAM은 얼마인가요?", results, service)

    assert summary.status in (SummaryStatus.OK, SummaryStatus.ABSTAINED)
    if summary.status is SummaryStatus.OK:
        assert "16GB" in summary.text

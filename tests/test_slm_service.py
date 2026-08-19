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


class TestAbort:
    """T10.23 — 진행 중인 추론을 실제로 끊는 경로."""

    def test_abort_delegates_to_the_active_client(self, fake_server):
        service = SlmService(SLM_RECOMMENDED)
        client = service.ensure_ready()

        service.abort_active_request()

        assert client._abort_requested is True
        service.shutdown()

    def test_abort_is_safe_before_any_server_started(self):
        """서버가 없을 때 불러도 조용히 넘어가야 한다 — 취소는 실패하면 안 된다."""
        service = SlmService(SLM_RECOMMENDED)
        service.abort_active_request()  # 예외가 나면 실패

    def test_abort_does_not_wait_for_the_request_lock(self, fake_server):
        """🔴 이게 이 기능의 핵심 함정이다.

        `chat()`은 추론이 끝날 때까지(채택 모델 중앙 18.3초) `_lock`을 쥔다.
        `abort_active_request()`가 같은 락을 잡으려 하면, 취소를 부르는 **UI
        스레드가 그 시간만큼 얼어붙는다** — 멈춤을 없애려던 기능이 멈춤을
        만든다. 락이 잡혀 있는 상태에서도 즉시 돌아오는지 확인한다.
        """
        service = SlmService(SLM_RECOMMENDED)
        client = service.ensure_ready()

        holding = threading.Event()
        release = threading.Event()

        def hold_lock():
            with service._lock:
                holding.set()
                release.wait(timeout=5)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        assert holding.wait(timeout=5)

        started = time.perf_counter()
        service.abort_active_request()
        elapsed = time.perf_counter() - started

        assert elapsed < 1.0, f"락을 기다렸다({elapsed:.2f}s) — UI가 그만큼 멎는다"
        assert client._abort_requested is True

        release.set()
        holder.join(timeout=5)
        service.shutdown()

    def test_ensure_ready_clears_a_stale_abort_flag(self, fake_server):
        """취소 표시가 남아 있으면 다음 요청이 시작하자마자 취소로 처리된다."""
        service = SlmService(SLM_RECOMMENDED)
        client = service.ensure_ready()
        service.abort_active_request()
        assert client._abort_requested is True

        again = service.ensure_ready()

        assert again is client
        assert client._abort_requested is False
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


class TestRuntimeOptions:
    """Phase 11-C: 설정 페이지가 바꾸는 값의 반영 방식 (DESIGN §14.5.2)."""

    def test_idle_timeout_applies_without_a_restart(self, fake_server):
        """🔴 DESIGN §14.5.2의 "생성자 인자라 반영 안 된다"는 전제가 이 값에는
        해당하지 않는다 — `touch()`가 요청마다 값을 다시 읽는다."""
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        assert fake_server["starts"] == 1

        service.set_idle_timeout(60)

        assert service._idle_timeout_sec == 60
        assert fake_server["stops"] == 0  # 서버를 내리지 않았다
        service.ensure_ready()
        assert fake_server["starts"] == 1  # 재기동도 없었다
        service.shutdown()

    def test_zero_idle_timeout_keeps_the_model_resident(self, fake_server):
        """`모델 상주`는 유휴 타이머를 아예 안 거는 것으로 구현된다."""
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()

        service.set_idle_timeout(0)
        service.touch()

        assert service._idle_timer is None
        assert service.is_running()
        service.shutdown()

    def test_changing_cpu_threads_brings_the_server_down(self, fake_server):
        """[사용자 확정] 즉시 내리고 다음 요청에 새 값으로 올린다."""
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        assert fake_server["starts"] == 1

        service.set_n_threads(4)

        assert fake_server["stops"] == 1
        assert not service.is_running()

        service.ensure_ready()
        assert fake_server["starts"] == 2
        assert fake_server["last_kwargs"]["n_threads"] == 4
        service.shutdown()

    def test_same_cpu_value_does_not_bounce_the_server(self, fake_server):
        """값이 그대로면 아무 일도 하지 않는다 — 설정 화면을 열기만 해도
        모델이 내려가면 곤란하다."""
        service = SlmService(SLM_RECOMMENDED, n_threads=4)
        service.ensure_ready()

        service.set_n_threads(4)

        assert fake_server["stops"] == 0
        assert service.is_running()
        service.shutdown()

    def test_ensure_ready_restarts_when_the_change_could_not_be_applied(self, fake_server):
        """🔴 즉시 못 내렸어도 새 값이 누락되지는 않는다.

        추론 중이면 `set_n_threads()`가 락을 못 잡아 서버를 그 자리에서
        내리지 못한다. 그때 `ensure_ready()`가 기동 당시 값과 비교해 다시
        올리는 것이 마지막 관문이다.
        """
        service = SlmService(SLM_RECOMMENDED)
        service.ensure_ready()
        assert fake_server["starts"] == 1

        # 락이 잡혀 즉시 종료가 건너뛰어진 상황을 그대로 재현한다.
        holder_done = threading.Event()

        def hold_lock():
            with service._lock:
                holder_done.wait(timeout=5)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        time.sleep(0.05)  # 다른 스레드가 락을 실제로 쥘 틈을 준다

        service.set_n_threads(2)
        assert fake_server["stops"] == 0  # 즉시 내리지 못했다
        holder_done.set()
        holder.join(timeout=5)

        service.ensure_ready()
        assert fake_server["starts"] == 2  # 그래도 새 값으로 다시 올라왔다
        assert fake_server["last_kwargs"]["n_threads"] == 2
        service.shutdown()

    def test_setters_do_not_block_while_inference_holds_the_lock(self, fake_server):
        """🔴 설정 화면에서 부르는 함수가 락을 기다리면 UI가 얼어붙는다
        (T10.23에서 취소 함수로 같은 함정을 겪었다)."""
        service = SlmService(SLM_RECOMMENDED)
        done = threading.Event()

        with service._lock:
            def call():
                service.set_idle_timeout(120)
                service.set_n_threads(8)
                done.set()

            threading.Thread(target=call, daemon=True).start()
            assert done.wait(timeout=2), "락이 잡힌 동안 설정 함수가 멈췄다"

        assert service._idle_timeout_sec == 120
        assert service._n_threads == 8

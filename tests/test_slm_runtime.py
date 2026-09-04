"""llama.cpp 바이너리 탐색·설치 스크립트·HTTP 클라이언트 (T6.2).

바이너리와 GGUF가 없는 환경에서는 종단 테스트만 사유와 함께 스킵한다 —
Phase 1의 LibreOffice·hwp, Phase 3의 임베딩 모델과 같은 방식이다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from config.settings import get_slm_profile
from scripts import setup_llamacpp
from slm import runtime
from slm.client import LlamaClient, LlamaClientError


# --- 실행 파일 탐색 -------------------------------------------------------

def test_env_var_takes_priority(tmp_path, monkeypatch):
    fake = tmp_path / "llama-server.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(fake))
    assert runtime.find_llama_server() == fake


def test_env_var_ignored_when_missing_file(tmp_path, monkeypatch):
    """경로가 지정됐어도 파일이 없으면 다음 후보로 넘어간다."""
    monkeypatch.setenv("LLAMA_SERVER_PATH", str(tmp_path / "없는파일.exe"))
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runtime, "VENDOR_DIR", tmp_path / "vendor")
    assert runtime.find_llama_server() is None


def test_vendor_dir_is_searched_recursively(tmp_path, monkeypatch):
    """릴리스마다 압축 구조가 달라(루트 직하 / build/bin) 재귀로 찾는다."""
    nested = tmp_path / "vendor" / "build" / "bin"
    nested.mkdir(parents=True)
    exe = nested / "llama-server.exe"
    exe.write_bytes(b"")

    monkeypatch.delenv("LLAMA_SERVER_PATH", raising=False)
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runtime, "VENDOR_DIR", tmp_path / "vendor")
    assert runtime.find_llama_server() == exe


def test_find_free_port_is_bindable():
    import socket

    port = runtime.find_free_port()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))  # 실제로 잡을 수 있어야 한다


def test_llama_server_raises_when_model_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "find_llama_server", lambda: tmp_path / "llama-server.exe")
    with pytest.raises(runtime.LlamaRuntimeError, match="모델 파일이 없습니다"):
        with runtime.llama_server(tmp_path / "없는모델.gguf"):
            pass


def test_llama_server_raises_when_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "find_llama_server", lambda: None)
    with pytest.raises(runtime.LlamaServerNotFoundError):
        with runtime.llama_server(tmp_path / "아무거나.gguf"):
            pass


# --- 설치 스크립트 --------------------------------------------------------

def test_asset_url_falls_back_when_api_unavailable(monkeypatch):
    """GitHub API가 막혀도(레이트 리밋 등) 이름 규칙으로 계속 진행한다."""
    def boom(*_args, **_kwargs):
        raise urllib.error.URLError("rate limited")

    monkeypatch.setattr(setup_llamacpp.urllib.request, "urlopen", boom)
    url = setup_llamacpp._asset_url("b10306")
    assert url.endswith("/b10306/llama-b10306-bin-win-cpu-x64.zip")


def test_resolve_tag_passes_through_explicit_tag():
    assert setup_llamacpp._resolve_tag("b10306") == "b10306"


def test_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../탈출.txt", "x")

    with pytest.raises(setup_llamacpp.LlamaSetupError, match="비정상 경로"):
        setup_llamacpp._extract(archive, tmp_path / "dest")


def test_extract_writes_files(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("llama-server.exe", "binary")

    dest = tmp_path / "dest"
    setup_llamacpp._extract(archive, dest)
    assert (dest / "llama-server.exe").read_text() == "binary"


def test_installed_tag_none_without_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_llamacpp, "VENDOR_DIR", tmp_path)
    monkeypatch.setattr(setup_llamacpp, "find_llama_server", lambda: None)
    assert setup_llamacpp.installed_tag() is None


def test_installed_tag_reads_marker(tmp_path, monkeypatch):
    (tmp_path / "INSTALLED_TAG.txt").write_text("b10306\n", encoding="utf-8")
    monkeypatch.setattr(setup_llamacpp, "VENDOR_DIR", tmp_path)
    monkeypatch.setattr(setup_llamacpp, "find_llama_server", lambda: tmp_path / "llama-server.exe")
    assert setup_llamacpp.installed_tag() == "b10306"


# --- HTTP 클라이언트 (스텁 서버) ------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    """llama-server의 응답 형식만 흉내 낸다."""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_payload = payload  # type: ignore[attr-defined]

        if self.server.fail_status:  # type: ignore[attr-defined]
            self._send(self.server.fail_status, {"error": {"message": "boom"}})  # type: ignore[attr-defined]
            return

        self._send(200, {
            "choices": [{"message": {"role": "assistant", "content": " 문서에서 찾을 수 없습니다. "}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            "timings": {"predicted_per_second": 3.5},
        })

    def do_GET(self):  # noqa: N802
        self._send(200, {"n_ctx": 4096})

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):  # 테스트 출력을 더럽히지 않는다
        pass


@pytest.fixture
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.fail_status = 0  # type: ignore[attr-defined]
    server.last_payload = None  # type: ignore[attr-defined]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_chat_parses_text_and_usage(stub_server):
    client = LlamaClient(stub_server.server_address[1])
    result = client.chat([{"role": "user", "content": "질문"}])

    assert result.text == "문서에서 찾을 수 없습니다."  # 앞뒤 공백 제거
    assert (result.prompt_tokens, result.completion_tokens) == (12, 7)
    assert result.elapsed_sec > 0
    assert result.tokens_per_sec > 0
    assert result.timings["predicted_per_second"] == 3.5


def test_chat_sends_greedy_and_fixed_seed(stub_server):
    """측정 재현성 조건이 실제로 요청에 실려 나가는지."""
    client = LlamaClient(stub_server.server_address[1])
    client.chat([{"role": "user", "content": "질문"}], max_tokens=64, stop=["<|end|>"])

    payload = stub_server.last_payload
    assert payload["temperature"] == 0.0
    assert payload["seed"] == 1234
    assert payload["stream"] is False
    assert payload["max_tokens"] == 64
    assert payload["stop"] == ["<|end|>"]


def test_chat_raises_on_http_error(stub_server):
    stub_server.fail_status = 500
    client = LlamaClient(stub_server.server_address[1])
    with pytest.raises(LlamaClientError, match="HTTP 500"):
        client.chat([{"role": "user", "content": "질문"}])


def test_chat_raises_on_unexpected_shape(stub_server, monkeypatch):
    client = LlamaClient(stub_server.server_address[1])
    monkeypatch.setattr(client, "_post", lambda *_a, **_k: {"unexpected": True})
    with pytest.raises(LlamaClientError, match="예상과 다른 응답"):
        client.chat([{"role": "user", "content": "질문"}])


def test_client_refuses_dead_port():
    client = LlamaClient(runtime.find_free_port(), timeout=2)
    with pytest.raises(LlamaClientError):
        client.chat([{"role": "user", "content": "질문"}])


# --- 종단 (실제 서버 + 실제 모델) -----------------------------------------

SMOKE_MODEL = "exaone-4.0-1.2b"


@pytest.mark.slow
def test_end_to_end_completion():
    """가장 작은 후보로 서버 기동 → 응답까지 실제로 확인한다."""
    if runtime.find_llama_server() is None:
        pytest.skip("llama.cpp 바이너리 미설치 — `python -m scripts.setup_llamacpp` 후 재시도")

    profile = get_slm_profile(SMOKE_MODEL)
    if not profile.is_installed():
        pytest.skip(
            f"{profile.label} 미설치 — `python -m slm.download {SMOKE_MODEL}` 후 재시도"
        )

    with runtime.llama_server(profile.local_path, n_ctx=1024, n_threads=4) as server:
        assert server.load_seconds > 0
        assert server.memory_mb() is not None  # T6.6 메모리 지표가 실제로 잡히는지
        client = LlamaClient(server.port)
        assert client.props()  # 서버가 메타데이터를 돌려준다
        result = client.chat(
            [{"role": "user", "content": "한국의 수도는? 도시 이름만 답하세요."}],
            max_tokens=32,
        )

    assert "서울" in result.text
    assert result.completion_tokens > 0


class TestClientAbort:
    """T10.23 — `LlamaClient`가 요청을 실제로 끊을 수 있어야 한다.

    `stream=False`라 llama-server는 생성이 다 끝나야 응답을 시작한다 — 즉
    블로킹 구간에는 응답 객체가 아직 없고, 끊으려면 **연결 자체**를 다른
    스레드에서 닫아야 한다(urllib으로는 불가능해 http.client를 직접 쓴다).
    """

    def test_abort_before_request_makes_the_next_post_fail_fast(self):
        from slm.client import LlamaClient, LlamaClientAborted

        client = LlamaClient(port=1)  # 아무도 안 듣는 포트 — 연결까지 갈 일이 없다
        client.abort()

        with pytest.raises(LlamaClientAborted):
            client._post("/v1/chat/completions", {"messages": []})

    def test_clear_abort_lets_requests_through_again(self):
        from slm.client import LlamaClient, LlamaClientAborted, LlamaClientError

        client = LlamaClient(port=1)
        client.abort()
        client.clear_abort()

        # 중단 표시가 지워졌으므로 이제는 "취소"가 아니라 평범한 연결 실패여야 한다.
        with pytest.raises(LlamaClientError) as caught:
            client._post("/v1/chat/completions", {"messages": []})
        assert not isinstance(caught.value, LlamaClientAborted)

    def test_aborted_is_a_subclass_of_client_error(self):
        """기존 호출부는 `LlamaClientError`만 잡는다 — 취소가 그물을 빠져나가
        예상 못 한 예외로 올라오면 안 된다."""
        from slm.client import LlamaClientAborted, LlamaClientError

        assert issubclass(LlamaClientAborted, LlamaClientError)


# --- 프로세스 메모리: 워킹셋 vs private bytes (T10.58) --------------------


class TestProcessMemoryDetail:
    """`process_memory_detail_mb()`가 워킹셋 리팩터링 후에도 값을 정확히 돌려주는지.

    `process_memory_mb()`와 이 함수는 이제 같은 내부 헬퍼
    (`_query_process_memory_counters`)를 공유한다 — 리팩터링으로 기존
    반환값(워킹셋 튜플)이 달라지지 않았는지, 그리고 새 필드(private bytes)가
    실제로 채워지는지 둘 다 확인한다.
    """

    def test_detail_matches_working_set_from_process_memory_mb(self, monkeypatch):
        """같은 카운터 스냅샷에서 두 함수가 일관된 값을 뽑아내는지.

        🔴 두 함수를 실제 PID로 **따로** 호출해 비교하면 그 사이 이 테스트
        프로세스 자신의 메모리가 미세하게 바뀌어 플레이키해진다(실측: 4KB
        차이로 실패) — 카운터를 고정해 순수하게 필드 매핑만 검증한다.
        """

        class _FakeCounters:
            WorkingSetSize = 100_000_000
            PeakWorkingSetSize = 150_000_000
            PagefileUsage = 300_000_000
            PeakPagefileUsage = 320_000_000

        monkeypatch.setattr(runtime, "_query_process_memory_counters", lambda pid: _FakeCounters())

        simple = runtime.process_memory_mb(1234)
        detail = runtime.process_memory_detail_mb(1234)

        assert simple == (100.0, 150.0)
        assert detail.working_set_mb == pytest.approx(simple[0])
        assert detail.peak_working_set_mb == pytest.approx(simple[1])
        # private bytes(커밋)는 워킹셋과 다른 지표다 — 2026-08-21 사고의
        # "+8.35GB"가 워킹셋이 아니라 이 값 기준이었다.
        assert detail.private_bytes_mb == pytest.approx(300.0)
        assert detail.peak_private_bytes_mb == pytest.approx(320.0)

    @pytest.mark.skipif(os.name != "nt", reason="이 측정 자체가 Windows 전용")
    def test_detail_reports_positive_private_bytes_for_live_process(self):
        """가짜가 아니라 실제로 살아 있는 이 테스트 프로세스를 재도 0이 아닌
        값이 나오는지 — 필드 매핑뿐 아니라 실제 Windows API 호출 자체가
        동작하는지 확인한다."""
        detail = runtime.process_memory_detail_mb(os.getpid())

        assert detail is not None
        assert detail.private_bytes_mb > 0
        assert detail.peak_private_bytes_mb >= detail.private_bytes_mb

    def test_detail_returns_none_when_counters_unavailable(self, monkeypatch):
        monkeypatch.setattr(runtime, "_query_process_memory_counters", lambda pid: None)

        assert runtime.process_memory_detail_mb(12345) is None
        assert runtime.process_memory_mb(12345) is None


# --- 고아 프로세스 방지 (T10.36) -----------------------------------------


def _pid_alive(pid: int) -> bool:
    """PID가 살아 있는가. `os.kill(pid, 0)`은 **Windows에서 실제로 죽이므로**
    쓰면 안 된다 — `indexer/index_log.py`와 같은 tasklist 방식으로 확인한다."""
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, timeout=10, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return str(pid) in result.stdout.decode("cp949", errors="replace")


class TestJobObjectOrphanGuard:
    """부모가 죽으면 llama-server도 같이 죽는지 (2026-08-21 사고의 실제 원인).

    당시 llama-server 3대가 고아로 남아 9.36GB를 물고 있었다 — 정상 종료
    경로(`SlmService.shutdown()`)는 크래시·강제 종료를 못 막는다.
    """

    def test_start_server_assigns_job_before_waiting(self, tmp_path, monkeypatch):
        """준비 대기(최대 180초) **전에** 묶어야 한다 — 그 사이에 앱이 죽으면
        정확히 사고 때와 같은 고아가 생긴다."""
        calls = []

        class _FakeProc:
            pid = 4242

            def poll(self):
                return None

        model = tmp_path / "model.gguf"
        model.write_bytes(b"")
        monkeypatch.setattr(
            runtime, "find_llama_server", lambda: tmp_path / "llama-server.exe"
        )
        monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: _FakeProc())
        monkeypatch.setattr(
            runtime, "assign_to_job", lambda p: calls.append(("assign", p.pid))
        )
        monkeypatch.setattr(
            runtime, "_health_ok", lambda port: calls.append(("health", port)) or True
        )

        runtime.start_server(model)

        assert calls[0] == ("assign", 4242), f"묶기 전에 다른 일을 했다: {calls}"

    @pytest.mark.skipif(os.name != "nt", reason="Job Object는 Windows 전용")
    def test_child_dies_when_parent_is_killed(self):
        """부모를 **강제 종료**해도 자식이 따라 죽는가 — 실제 보장의 종단 검증.

        Job Object가 없으면 이 자식은 부모와 무관하게 계속 살아남는다(그게
        사고 때 3.62GB짜리 프로세스가 1시간 30분을 버틴 이유다).
        """
        root = str(Path(__file__).resolve().parents[1])
        script = (
            "import subprocess, sys, time\n"
            f"sys.path.insert(0, {root!r})\n"
            "from slm import runtime\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "print(child.pid, int(runtime.assign_to_job(child)), flush=True)\n"
            "time.sleep(120)\n"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        grandchild_pid = None
        try:
            line = parent.stdout.readline().split()
            grandchild_pid, assigned = int(line[0]), bool(int(line[1]))
            assert assigned, "Job Object에 묶는 데 실패했다"
            assert _pid_alive(grandchild_pid), "자식이 뜨지도 않았다"

            # 크래시를 흉내낸다 — terminate가 아니라 강제 종료라
            # `shutdown()` 같은 정리 코드는 한 줄도 돌지 않는다.
            parent.kill()
            parent.wait(timeout=10)

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not _pid_alive(grandchild_pid):
                    break
                time.sleep(0.3)
            else:
                pytest.fail(
                    f"부모가 죽었는데 자식(pid={grandchild_pid})이 살아남았다 — 고아 발생"
                )
        finally:
            # 테스트가 실패했다면 정작 이 테스트가 고아를 남기게 된다.
            if grandchild_pid is not None and _pid_alive(grandchild_pid):
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(grandchild_pid)],
                    capture_output=True, check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            if parent.poll() is None:
                parent.kill()

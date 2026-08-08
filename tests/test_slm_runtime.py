"""llama.cpp 바이너리 탐색·설치 스크립트·HTTP 클라이언트 (T6.2).

바이너리와 GGUF가 없는 환경에서는 종단 테스트만 사유와 함께 스킵한다 —
Phase 1의 LibreOffice·hwp, Phase 3의 임베딩 모델과 같은 방식이다.
"""

from __future__ import annotations

import json
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

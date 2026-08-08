"""llama.cpp 실행 파일 탐색 + llama-server 수명주기 (T6.2).

탐색 우선순위는 `parser/utils/libreoffice.py`의 `find_soffice()`를 그대로
본떴다 — 환경변수 → PATH → `vendor/` 상대 경로(TECH 9.1 포터블 원칙).

**`llama-cli`가 아니라 `llama-server`를 쓴다.** 후보 4종 × 수십 문항을
측정하는데 `llama-cli`는 호출마다 모델을 다시 로딩해(7.8B는 수 초) 순수
로딩에만 시간을 버린다. 더 중요한 건 측정 타당성이다 — Phase 7의 실제 앱은
모델을 한 번 올려두고 재사용하므로, 웜 상태의 요청당 지연시간이 실사용에
가깝다. 로딩 시간은 별도 지표로 1회씩만 잰다.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = PROJECT_ROOT / "vendor" / "llamacpp"

_ENV_PATH = "LLAMA_SERVER_PATH"
_EXE_NAMES = ("llama-server", "llama-server.exe")

# 서버가 뜰 때까지 기다리는 한계. 7.8B 모델을 콜드 스타트로 올리면 수십 초가
# 걸릴 수 있어 넉넉히 잡는다.
DEFAULT_STARTUP_TIMEOUT_SEC = 180
_HEALTH_POLL_INTERVAL_SEC = 0.5


class LlamaRuntimeError(RuntimeError):
    """llama.cpp 실행 계열 예외의 기반."""


class LlamaServerNotFoundError(LlamaRuntimeError):
    """llama-server 실행 파일을 찾지 못함."""


class LlamaServerStartupError(LlamaRuntimeError):
    """서버가 제한 시간 안에 준비되지 않았거나 즉시 종료함."""


def find_llama_server() -> Path | None:
    """llama-server 경로를 반환. 환경변수 > PATH > vendor/ 순."""
    env_path = os.environ.get(_ENV_PATH)
    if env_path and Path(env_path).is_file():
        return Path(env_path)

    for name in _EXE_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)

    if VENDOR_DIR.is_dir():
        for name in _EXE_NAMES:
            # 압축 구조가 릴리스마다 조금씩 달라(루트 직하 / build/bin 등)
            # 재귀로 찾는다.
            for candidate in VENDOR_DIR.rglob(name):
                if candidate.is_file():
                    return candidate
    return None


def is_available() -> bool:
    return find_llama_server() is not None


def find_free_port() -> int:
    """OS가 비어 있다고 알려주는 포트를 받아온다.

    받은 직후 서버가 잡기 전까지 이론상 경쟁이 가능하지만, 포트를 고정하면
    이전 측정이 남긴 프로세스와 충돌하는 편이 훨씬 잦다.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def process_memory_mb(pid: int) -> tuple[float, float] | None:
    """다른 프로세스의 (현재, 최대) 워킹셋 MB. 측정 못 하면 None.

    T6.6의 메모리 지표는 **llama-server 쪽**을 재야 한다 — 파이썬 프로세스는
    HTTP 요청만 보내므로 자기 자신을 재면 모델 크기가 전혀 안 잡힌다.
    `scripts/benchmark_search.py`의 `_memory_mb()`가 자기 프로세스용이라
    여기서는 PID를 열어서 같은 구조체를 읽는다.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        _PROCESS_QUERY_INFORMATION = 0x0400
        _PROCESS_VM_READ = 0x0010
        kernel32 = ctypes.windll.kernel32
        # restype을 지정하지 않으면 핸들이 32비트로 잘린다. 지금까지는 핸들 값이
        # 작아서 우연히 동작했다 — `scripts/benchmark_search.py`의 같은 코드는
        # 의사 핸들(-1) 때문에 실제로 실패하고 있었다.
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid
        )
        if not handle:
            return None
        handle = ctypes.c_void_p(handle)
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if not ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            ):
                return None
            return counters.WorkingSetSize / 1e6, counters.PeakWorkingSetSize / 1e6
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return None


def available_ram_gb() -> float | None:
    """시스템 여유 RAM(GB). 측정 조건 기록용 — 이 PC는 최소 사양이라 특히 중요하다."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status.ullAvailPhys / 2**30
    except Exception:
        return None


@dataclass
class ServerHandle:
    """기동된 llama-server 한 대."""

    port: int
    # 프로세스 기동부터 `/health` 통과까지 — T6.6의 "모델 로딩 시간" 지표.
    load_seconds: float
    pid: int

    def memory_mb(self) -> tuple[float, float] | None:
        return process_memory_mb(self.pid)


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


@contextmanager
def llama_server(
    model_path: str | Path,
    *,
    n_ctx: int = 4096,
    n_threads: int | None = None,
    startup_timeout: int = DEFAULT_STARTUP_TIMEOUT_SEC,
    extra_args: list[str] | None = None,
):
    """llama-server를 띄우고 `ServerHandle`을 넘긴다. 블록을 벗어나면 종료한다."""
    exe = find_llama_server()
    if exe is None:
        raise LlamaServerNotFoundError(
            "llama-server를 찾을 수 없습니다. "
            "`python -m scripts.setup_llamacpp`로 받거나 "
            f"환경변수 {_ENV_PATH}에 경로를 지정하세요."
        )

    model_path = Path(model_path)
    if not model_path.is_file():
        raise LlamaRuntimeError(f"모델 파일이 없습니다: {model_path}")

    port = find_free_port()
    cmd = [
        str(exe),
        "-m", str(model_path),
        "--host", "127.0.0.1",
        "--port", str(port),
        "-c", str(n_ctx),
    ]
    if n_threads is not None:
        cmd += ["-t", str(n_threads)]
    if extra_args:
        cmd += extra_args

    started = time.perf_counter()
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )

    try:
        deadline = started + startup_timeout
        while True:
            if process.poll() is not None:
                output = (process.stdout.read() if process.stdout else "") or ""
                raise LlamaServerStartupError(
                    f"llama-server가 즉시 종료했습니다(exit={process.returncode}).\n"
                    f"{output[-2000:]}"
                )
            if _health_ok(port):
                break
            if time.perf_counter() > deadline:
                raise LlamaServerStartupError(
                    f"llama-server가 {startup_timeout}초 안에 준비되지 않았습니다: {model_path.name}"
                )
            time.sleep(_HEALTH_POLL_INTERVAL_SEC)

        yield ServerHandle(
            port=port,
            load_seconds=time.perf_counter() - started,
            pid=process.pid,
        )
    finally:
        _terminate(process)


def _terminate(process: subprocess.Popen) -> None:
    """정상 종료를 먼저 시도하고, 안 되면 강제 종료한다."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)

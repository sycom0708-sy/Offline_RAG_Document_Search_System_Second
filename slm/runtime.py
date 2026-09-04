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
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

# `__file__` 기준이 아니라 `config.settings.PROJECT_ROOT`를 쓴다 — PyInstaller로
# 얼린 exe에서는 이 파일이 exe와 다른 위치(`_internal/`)에 번들되므로,
# `__file__` 기준으로 다시 계산하면 `vendor/`를 잘못 찾는다 (T9.2).
from config.settings import PROJECT_ROOT

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


def _query_process_memory_counters(pid: int):
    """`PROCESS_MEMORY_COUNTERS` 구조체를 통째로 읽는다. 실패하면 None.

    `process_memory_mb()`·`process_memory_detail_mb()`가 이 구조체에서
    서로 다른 필드를 뽑아 쓰므로 OpenProcess/GetProcessMemoryInfo 보일러
    플레이트를 여기 한 곳에만 둔다.
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
            return counters
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return None


def process_memory_mb(pid: int) -> tuple[float, float] | None:
    """다른 프로세스의 (현재, 최대) 워킹셋 MB. 측정 못 하면 None.

    T6.6의 메모리 지표는 **llama-server 쪽**을 재야 한다 — 파이썬 프로세스는
    HTTP 요청만 보내므로 자기 자신을 재면 모델 크기가 전혀 안 잡힌다.
    `scripts/benchmark_search.py`의 `_memory_mb()`가 자기 프로세스용이라
    여기서는 PID를 열어서 같은 구조체를 읽는다.

    🔴 **워킹셋은 "지금 얼마나 쓰고 있는가"의 근사치일 뿐이다** — Windows가
    당분간 안 건드린 페이지를 대기 목록(standby list)으로 옮기면 이 값이
    실제 커밋 여부와 무관하게 뚝 떨어진다(T10.58 재현 실험에서 실측: ONNX
    세션을 한 번 올린 뒤 한동안 안 건드리자 워킹셋이 1.0GB → 220MB로 계단식
    하락, 그런데도 `PeakWorkingSetSize`는 그대로였다). 진짜 누수 여부를
    가리려면 `process_memory_detail_mb()`의 private bytes(커밋 메모리)를
    같이 봐야 한다 — 그건 OS가 트리밍한다고 줄지 않는다.
    """
    counters = _query_process_memory_counters(pid)
    if counters is None:
        return None
    return counters.WorkingSetSize / 1e6, counters.PeakWorkingSetSize / 1e6


@dataclass
class ProcessMemoryDetail:
    """워킹셋(트리밍될 수 있음) + private bytes(커밋, 트리밍 안 됨) 전부."""

    working_set_mb: float
    peak_working_set_mb: float
    private_bytes_mb: float
    peak_private_bytes_mb: float


def process_memory_detail_mb(pid: int) -> ProcessMemoryDetail | None:
    """워킹셋·private bytes를 모두 담아 돌려준다 (T10.58).

    2026-08-21 사고를 나중에 Windows 이벤트 로그로 되짚을 때 쓴 "인덱싱
    python이 2.98GB→11.26GB로 부풀었다"는 수치는 Resource-Exhaustion-
    Detector가 보고한 **커밋(private bytes)** 기준이었다 — `process_memory_mb()`가
    재는 워킹셋과 다른 지표다. 워킹셋은 OS 트리밍으로 실제 누수 없이도
    떨어질 수 있어(위 docstring 참고) 재현 실험 때 이 값만 보면 "괜찮다"고
    오판할 수 있다.
    """
    counters = _query_process_memory_counters(pid)
    if counters is None:
        return None
    return ProcessMemoryDetail(
        working_set_mb=counters.WorkingSetSize / 1e6,
        peak_working_set_mb=counters.PeakWorkingSetSize / 1e6,
        private_bytes_mb=counters.PagefileUsage / 1e6,
        peak_private_bytes_mb=counters.PeakPagefileUsage / 1e6,
    )


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


# --- 고아 프로세스 방지 (T10.36) --------------------------------------
#
# `SlmService.shutdown()`은 정상 종료 경로만 지킨다. Windows에서 `Popen`으로
# 띄운 자식은 **부모가 죽어도 같이 죽지 않으므로**, 앱이 크래시하거나 강제
# 종료되면 모델을 통째로 올린 llama-server가 3~4GB를 문 채 고아로 남는다.
#
# 2026-08-21 사고의 실제 원인이 이것이었다 — 시스템 이벤트 로그(Resource-
# Exhaustion 2004)에 llama-server **3대**가 동시에 살아 3.62+3.02+2.72=9.36GB를
# 쓰고 있었고, 그중 한 대는 1시간 30분 넘게 생존했다. 여기에 인덱싱 파이썬
# 프로세스가 겹치면서 물리 메모리 15.6GB를 넘겨 PC가 멎었다. (당시 유력
# 용의선상이던 LibreOffice는 그 목록에 한 번도 오르지 않았다.)
#
# Job Object에 묶어두면 부모 프로세스가 **어떤 이유로 죽든** — 크래시,
# 작업 관리자 강제 종료, os._exit — OS가 핸들을 닫으면서 자식을 함께
# 죽인다. 정상 경로(`shutdown()`)를 대체하는 게 아니라 그 아래에 까는
# 안전망이다.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

# 프로세스 전체에서 하나만 만들어 계속 들고 있는다 — 이 핸들이 닫히는
# 순간이 곧 자식들이 죽는 순간이라, 절대 닫지 않는 것이 정상 동작이다.
_job_handle: int | None = None
_job_lock = threading.Lock()


def _ensure_job_handle() -> int | None:
    """KILL_ON_JOB_CLOSE가 걸린 Job 핸들을 만들어 캐시한다. 실패하면 None."""
    global _job_handle
    if _job_handle is not None:
        return _job_handle

    import ctypes

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_ulong),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_ulong),
            # ULONG_PTR — 포인터 크기여야 뒤 필드 정렬이 맞는다.
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_ulong),
            ("SchedulingClass", ctypes.c_ulong),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    # restype을 안 주면 64비트에서 핸들이 32비트로 잘린다
    # (`process_memory_mb()`가 같은 함정을 이미 겪었다).
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]

    # lpJobAttributes=NULL이라 **상속되지 않는 핸들**이 나온다. 이게 중요하다 —
    # 자식이 이 핸들을 물려받으면 부모가 죽어도 핸들이 안 닫혀 KILL이 안 걸린다.
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None

    info = _EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong
    ]
    ok = kernel32.SetInformationJobObject(
        ctypes.c_void_p(handle),
        _JOBOBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return None

    _job_handle = handle
    return handle


def assign_to_job(process: subprocess.Popen) -> bool:
    """자식 프로세스를 "부모가 죽으면 같이 죽는" Job에 넣는다.

    성공 여부를 돌려주되 **실패해도 예외를 던지지 않는다** — 이건 안전망이라,
    묶는 데 실패했다고 서버 기동 자체를 막으면 得보다 失이 크다.
    Windows 전용이며 다른 OS에서는 아무것도 하지 않고 False를 돌려준다.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        with _job_lock:
            handle = _ensure_job_handle()
        if handle is None:
            return False

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        # Popen이 프로세스 핸들을 계속 들고 있으므로 이 PID가 그사이 다른
        # 프로세스로 재사용될 수 없다 — PID로 열어도 안전한 구간이다.
        proc_handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, process.pid
        )
        if not proc_handle:
            return False
        try:
            kernel32.AssignProcessToJobObject.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p
            ]
            return bool(
                kernel32.AssignProcessToJobObject(
                    ctypes.c_void_p(handle), ctypes.c_void_p(proc_handle)
                )
            )
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(proc_handle))
    except Exception:
        return False


def start_server(
    model_path: str | Path,
    *,
    n_ctx: int = 4096,
    n_threads: int | None = None,
    startup_timeout: int = DEFAULT_STARTUP_TIMEOUT_SEC,
    extra_args: list[str] | None = None,
) -> tuple[ServerHandle, subprocess.Popen]:
    """llama-server를 띄우고 (핸들, 프로세스)를 돌려준다. **호출자가 종료 책임을 진다.**

    Phase 7의 `slm/service.py`는 서버를 요청 사이에 **띄워둔 채로 재사용**하므로
    컨텍스트 매니저(블록을 벗어나면 죽는다)로는 쓸 수 없다. 기동 로직은 하나만
    두고, 블록 스코프가 맞는 쪽(측정 스크립트·테스트)은 아래 `llama_server()`
    컨텍스트 매니저를 계속 쓴다.

    기동에 실패하면 프로세스를 정리하고 예외를 던진다 — 실패한 채 떠 있는
    프로세스를 호출자에게 떠넘기지 않는다.
    """
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="replace",
        # llama-server는 콘솔 프로그램이라, 콘솔 없는(--windowed) 이 앱에서
        # 그냥 띄우면 서버가 떠 있는 내내 콘솔 창이 하나 남는다. stdout/stderr를
        # 이미 버리고 있어(DEVNULL) 창을 숨겨도 잃는 진단 정보는 없다.
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # 띄우자마자 묶는다 (T10.36). 아래 준비 대기가 최대 180초라, 그사이 앱이
    # 죽으면 정확히 사고 때와 같은 고아가 생긴다 — 대기 **전에** 묶어야 한다.
    assign_to_job(process)

    try:
        deadline = started + startup_timeout
        while True:
            if process.poll() is not None:
                raise LlamaServerStartupError(
                    f"llama-server가 즉시 종료했습니다(exit={process.returncode})."
                )
            if _health_ok(port):
                break
            if time.perf_counter() > deadline:
                raise LlamaServerStartupError(
                    f"llama-server가 {startup_timeout}초 안에 준비되지 않았습니다: {model_path.name}"
                )
            time.sleep(_HEALTH_POLL_INTERVAL_SEC)
    except BaseException:
        _terminate(process)
        raise

    handle = ServerHandle(
        port=port,
        load_seconds=time.perf_counter() - started,
        pid=process.pid,
    )
    return handle, process


def stop_server(process: subprocess.Popen) -> None:
    """`start_server()`가 돌려준 프로세스를 종료한다."""
    _terminate(process)


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
    handle, process = start_server(
        model_path,
        n_ctx=n_ctx,
        n_threads=n_threads,
        startup_timeout=startup_timeout,
        extra_args=extra_args,
    )
    try:
        yield handle
    finally:
        stop_server(process)


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

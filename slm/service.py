"""llama-server 한 대를 띄워두고 재사용하는 서비스 (T7.1~T7.2 기반 인프라).

Phase 6의 측정 하네스는 `llama_server()` 컨텍스트 매니저로 충분했다 — 측정
한 판이 끝나면 서버도 같이 내리면 됐다. 앱은 다르다:

    · 요약 요청마다 서버를 새로 띄우면 매번 기동 비용(채택 모델 4.7초)이 붙는다
    · 그렇다고 앱이 떠 있는 내내 붙들고 있으면 **4.8GB를 계속 점유**한다

그래서 **첫 요청에 띄우고, 유휴 5분이면 자동으로 내린다** [사용자 확정,
2026-08-10]. 이 PC(16GB)에서 안드로이드 스튜디오 빌드와 동시에 쓰는 것이
전제라 "안 쓰는 동안 메모리를 돌려준다"가 요구사항이었다.

**스레드 안전이 필수다.** UI에서는 `SummaryWorker(QThread)`가 이 서비스를
부르고, 검색이 겹치면 두 워커가 동시에 들어온다. 잠그지 않으면 서버를 두 번
띄워 메모리를 두 배로 쓰게 된다 — 이 Phase가 피하려는 바로 그 상황이다.
"""

from __future__ import annotations

import subprocess
import threading

from config.settings import (
    DEFAULT_SLM_PROFILE,
    SLM_IDLE_TIMEOUT_SEC,
    SlmProfile,
    get_slm_profile,
)
from slm import runtime
from slm.client import LlamaClient

# 앱에서 쓰는 컨텍스트 길이.
#
# Phase 6~7 측정은 4096으로 했다. 2026-08-18에 6144로 **올렸다** — 실측에서
# 프롬프트가 3,922토큰을 먹어 답변에 174토큰밖에 안 남았고, 답이 조금만 길면
# 생성이 중간에 끊겼다(화면엔 "… 다릅니다. [ "처럼 반쪽 대괄호가 남는다).
# 줄이는 것과 달리 **늘리는 것은 발췌를 자르지 않으므로** 그때의 품질 수치는
# 그대로 유효하다 — 모델이 보는 내용이 달라지지 않고 여유만 생긴다.
# 🔴 여기서 줄이면 발췌가 잘려 회귀 비교가 성립하지 않는다.
DEFAULT_N_CTX = 6144

# 요약 1건의 최대 길이. 프롬프트가 "3문장 이내"를 요구하므로(slm/prompt.py)
# 넉넉하다. Phase 6은 256으로 측정했다.
DEFAULT_MAX_TOKENS = 256


class SlmNotInstalledError(RuntimeError):
    """모델 GGUF나 llama.cpp 바이너리가 없다 — 사용자 안내가 필요한 상태."""


class SlmService:
    """llama-server 한 대의 수명을 관리한다.

    `MainWindow`가 하나만 만들어 들고 있고, 워커 스레드들이 공유한다.
    """

    def __init__(
        self,
        profile_key: str | None = None,
        *,
        idle_timeout_sec: float = SLM_IDLE_TIMEOUT_SEC,
        n_ctx: int = DEFAULT_N_CTX,
        n_threads: int | None = None,
    ) -> None:
        self._profile: SlmProfile = get_slm_profile(profile_key or DEFAULT_SLM_PROFILE)
        self._idle_timeout_sec = idle_timeout_sec
        self._n_ctx = n_ctx
        self._n_threads = n_threads

        # 기동·종료·요청을 한 줄로 세운다. 요청 자체도 잠그는 이유는 llama-server가
        # 동시 요청을 받으면 슬롯을 나눠 쓰면서 지연이 널뛰기 때문이다 — 어차피
        # UI는 요약을 한 번에 하나만 보여준다.
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._handle: runtime.ServerHandle | None = None
        self._client: LlamaClient | None = None
        self._idle_timer: threading.Timer | None = None

    # --- 상태 조회 --------------------------------------------------

    @property
    def profile(self) -> SlmProfile:
        return self._profile

    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def is_available(self) -> bool:
        """모델과 실행 바이너리가 모두 있는가 (기동 전에 확인 가능)."""
        return self._profile.is_installed() and runtime.is_available()

    def memory_mb(self) -> tuple[float, float] | None:
        with self._lock:
            return self._handle.memory_mb() if self._handle else None

    def set_profile(self, profile_key: str) -> None:
        """모델을 바꾼다. 이미 떠 있으면 내린다 — 다음 요청에 새 모델로 올라온다."""
        with self._lock:
            if profile_key == self._profile.key:
                return
            self._profile = get_slm_profile(profile_key)
            self.shutdown()

    # --- 수명주기 --------------------------------------------------

    def ensure_ready(self) -> LlamaClient:
        """떠 있으면 즉시, 아니면 서버를 올리고 클라이언트를 돌려준다."""
        with self._lock:
            if self._client is not None and self.is_running():
                # 직전 요청이 취소돼 중단 표시가 남아 있으면 지운다 —
                # 안 지우면 다음 요청이 시작하자마자 취소로 처리된다.
                self._client.clear_abort()
                return self._client

            # 죽은 프로세스가 남아 있으면(크래시 등) 흔적을 지우고 새로 띄운다.
            self._teardown_locked()

            if not self._profile.is_installed():
                raise SlmNotInstalledError(
                    f"{self._profile.label} 모델이 없습니다. "
                    "모델 관리에서 다운로드 안내를 확인하세요."
                )
            if not runtime.is_available():
                raise SlmNotInstalledError(
                    "llama.cpp 실행 파일이 없습니다. "
                    "`python -m scripts.setup_llamacpp`로 설치하세요."
                )

            # Qwen3.5는 이 인자가 없으면 300토큰을 전부 사고에 쓰고 **빈 응답**을
            # 준다(Phase 6 실측). 프로파일에 박아둔 것을 반드시 실어 보낸다.
            extra = list(self._profile.extra_server_args) or None
            handle, process = runtime.start_server(
                self._profile.local_path,
                n_ctx=self._n_ctx,
                n_threads=self._n_threads,
                extra_args=extra,
            )
            self._handle = handle
            self._process = process
            self._client = LlamaClient(handle.port)
            return self._client

    def touch(self) -> None:
        """유휴 타이머를 다시 센다. 요청이 오갈 때마다 부른다."""
        with self._lock:
            self._cancel_timer_locked()
            if self._idle_timeout_sec > 0 and self.is_running():
                timer = threading.Timer(self._idle_timeout_sec, self._on_idle_timeout)
                timer.daemon = True  # 타이머가 앱 종료를 붙잡지 않도록
                self._idle_timer = timer
                timer.start()

    def _on_idle_timeout(self) -> None:
        """유휴 시간이 다 됐다 — 메모리를 돌려준다."""
        self.shutdown()

    def shutdown(self) -> None:
        """서버를 내린다. 이미 내려가 있으면 아무 일도 하지 않는다.

        **앱 종료 시 반드시 불러야 한다.** 안 부르면 4.8GB짜리 프로세스가
        고아로 남는다.
        """
        with self._lock:
            self._cancel_timer_locked()
            self._teardown_locked()

    def _teardown_locked(self) -> None:
        if self._process is not None:
            runtime.stop_server(self._process)
        self._process = None
        self._handle = None
        self._client = None

    def _cancel_timer_locked(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    # --- 추론 --------------------------------------------------

    def abort_active_request(self) -> None:
        """진행 중인 추론 요청을 끊는다. 서버(모델)는 내리지 않는다.

        `shutdown()`과 다르다 — 챗봇에서 다음 질문이 바로 이어지는 상황이라
        4.8GB 모델을 다시 올리는 비용을 물 이유가 없다. 요청만 접는다.

        🔴 `self._lock`을 잡지 않는다. 그 락은 `chat()`이 추론이 끝날 때까지
        (채택 모델 중앙 18.3초) 붙들고 있어서, 여기서 락을 기다리면 이 함수를
        부르는 **UI 스레드가 그 시간만큼 얼어붙는다** — 취소 기능이 정작
        취소하려던 멈춤을 만드는 셈이 된다. 참조 하나만 읽고 빠져나간다
        (참조 읽기는 원자적이라 이 용도에는 락이 필요 없다).
        """
        client = self._client
        if client is not None:
            client.abort()

    def chat(self, messages: list[dict], *, max_tokens: int = DEFAULT_MAX_TOKENS):
        """메시지 한 번을 돌리고 `Completion`을 돌려준다. 유휴 타이머를 갱신한다."""
        with self._lock:
            client = self.ensure_ready()
            try:
                return client.chat(messages, temperature=0.0, max_tokens=max_tokens)
            finally:
                # 실패했더라도 서버는 떠 있으므로 타이머를 다시 걸어야 한다 —
                # 안 그러면 실패 후 서버가 영원히 안 내려간다.
                self.touch()

    def __enter__(self) -> "SlmService":
        return self

    def __exit__(self, *_exc) -> None:
        self.shutdown()

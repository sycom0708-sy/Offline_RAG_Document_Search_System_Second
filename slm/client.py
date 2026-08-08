"""llama-server HTTP 클라이언트 (T6.2).

`requests`를 들이지 않고 표준 `urllib`만 쓴다 — `slm/download.py`,
`indexer/vector/download.py`가 이미 쓰는 방식이고, 오프라인 배포에서 의존성
하나가 곧 용량이자 위험이다(TECH 9장).

**OpenAI 호환 `/v1/chat/completions`를 쓴다.** 네이티브 `/completion`은 프롬프트
문자열을 그대로 받으므로 모델별 chat template(EXAONE·Phi·Qwen이 전부 다르다)을
이쪽에서 직접 조립해야 한다. 서버가 GGUF에 박힌 템플릿을 적용하게 두는 편이
후보 비교의 공정성 면에서도 낫다.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# 7.8B를 4스레드 CPU로 돌리면 한 응답에 분 단위가 걸릴 수 있다.
DEFAULT_TIMEOUT_SEC = 600

# 측정 재현성을 위한 고정 시드. temperature=0(그리디)이라 원칙적으로는 무의미
# 하지만, 서버가 샘플링 경로를 타는 경우까지 묶어두는 편이 안전하다.
DEFAULT_SEED = 1234


class LlamaClientError(RuntimeError):
    """llama-server 호출 실패."""


@dataclass
class Completion:
    """응답 1건 + 측정 지표."""

    text: str
    elapsed_sec: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # 서버가 돌려준 timings 원본 (있을 때만). 지표 계산 근거로 남긴다.
    timings: dict = field(default_factory=dict)

    @property
    def tokens_per_sec(self) -> float:
        if self.elapsed_sec <= 0 or not self.completion_tokens:
            return 0.0
        return self.completion_tokens / self.elapsed_sec


class LlamaClient:
    """`llama_server()` 컨텍스트가 넘겨준 포트에 붙는 클라이언트."""

    def __init__(self, port: int, *, host: str = "127.0.0.1",
                 timeout: int = DEFAULT_TIMEOUT_SEC) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise LlamaClientError(f"{path} 실패(HTTP {exc.code}): {body}") from exc
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise LlamaClientError(f"{path} 호출 실패: {exc}") from exc

    def _get(self, path: str) -> dict:
        try:
            with urllib.request.urlopen(f"{self.base_url}{path}", timeout=30) as response:
                return json.load(response)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise LlamaClientError(f"{path} 호출 실패: {exc}") from exc

    def props(self) -> dict:
        """서버·모델 메타데이터. 컨텍스트 길이와 chat template 확인용."""
        return self._get("/props")

    def apply_template(self, messages: list[dict]) -> str:
        """chat template을 적용한 **실제 프롬프트 문자열**을 돌려받는다.

        메시지를 보냈다고 모델이 받는 것은 아니다 — EXAONE-4.0 템플릿은 system
        메시지를 렌더링에서 버린다(실측). 측정 전에 규칙이 프롬프트에 실제로
        실렸는지 이걸로 확인한다. 서버가 이 엔드포인트를 모르면 빈 문자열.
        """
        try:
            return self._post("/apply-template", {"messages": messages}).get("prompt", "")
        except LlamaClientError:
            return ""

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.0,
        max_tokens: int = 512,
        seed: int = DEFAULT_SEED,
        stop: list[str] | None = None,
        extra: dict | None = None,
    ) -> Completion:
        """`messages`로 한 번 응답을 받는다. 스트리밍은 쓰지 않는다(측정만 필요)."""
        payload: dict = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "seed": seed,
            "stream": False,
        }
        if stop:
            payload["stop"] = stop
        if extra:
            payload.update(extra)

        started = time.perf_counter()
        body = self._post("/v1/chat/completions", payload)
        elapsed = time.perf_counter() - started

        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LlamaClientError(f"예상과 다른 응답 형식입니다: {str(body)[:500]}") from exc

        usage = body.get("usage") or {}
        return Completion(
            text=text.strip(),
            elapsed_sec=elapsed,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            timings=body.get("timings") or {},
        )

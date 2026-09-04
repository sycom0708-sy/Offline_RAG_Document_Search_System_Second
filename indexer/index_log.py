"""대규모 인덱싱 진단용 로그 (T10.36, T10.58).

사용자가 문서 1만 개 이상을 권장 사양 PC에서 인덱싱하다 화면이 반복해서
까맣게 변하고 결국 PC가 멎는 것을 겪었는데, 콘솔에도 UI에도 아무 기록이
남지 않아 어느 파일에서 무슨 일이 있었는지 알아낼 방법이 없었다. 이 모듈은
"다음에 같은 일이 생기면 마지막 줄만 봐도 범인 파일을 알 수 있게" 파일마다
시작·종료를 기록한다 — 원인을 지금 고치는 게 아니라 관측 지점을 심는 것이다.

T10.58 — 그 사고의 시스템 이벤트 로그를 나중에 되짚어 보니, llama-server
고아 누적(T10.36)과 별개로 인덱싱 python 프로세스 자체도 1시간 25분 동안
+8.35GB 부풀어 있었다. 그런데 당시 심어둔 체크포인트는 `soffice.bin` 개수만
남기고 정작 이 프로세스의 메모리는 한 번도 안 쟀다(사고 당시 용의자가
LibreOffice라고 봤기 때문). `current_process_memory_mb()`를 같은 체크포인트에
얹어 다음부터는 놓치지 않는다.

로그 쓰기 자체가 실패해도(디스크 꽉 참, 권한 없음 등) 인덱싱을 막으면 안
되므로 전부 조용히 무시한다.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime

from config.settings import LOGS_DIR

_LOGGER_NAME = "offline_rag.indexing"


def get_logger() -> logging.Logger:
    """인덱싱 진단 로거. 여러 번 불러도 핸들러가 중복 붙지 않는다."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        path = LOGS_DIR / f"indexing_{stamp}.log"
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        # 로그를 못 남겨도 인덱싱은 계속돼야 한다 — 조용히 포기한다.
        logger.addHandler(logging.NullHandler())
    return logger


def count_soffice_processes() -> int | None:
    """현재 떠 있는 `soffice.bin` 프로세스 수. 실패하면 None.

    LibreOffice 변환이 파일마다 새 프로세스를 띄우는데(`parser/utils/
    libreoffice.py`), 정상 종료됐다면 인덱싱이 끝난 뒤 이 수는 0이어야
    한다 — 계속 남아 쌓인다면 좀비 프로세스 누적을 의심할 근거가 된다.
    Windows 전용(`tasklist`); 다른 OS나 조회 실패 시 None을 돌려주고
    호출부가 로그를 건너뛴다.
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq soffice.bin", "/NH"],
            capture_output=True,
            timeout=10,
            check=False,
            # tasklist는 콘솔 프로그램이라, 콘솔 없는(--windowed) 이 앱에서
            # 그냥 부르면 Windows가 새 콘솔 창을 띄웠다 닫는다 — 인덱싱 중
            # 사용자에게 원인 모를 창 깜빡임으로 보인다.
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    try:
        output = result.stdout.decode("cp949", errors="replace")
    except Exception:
        return None

    if "soffice.bin" not in output.lower():
        return 0
    return len([line for line in output.splitlines() if line.strip()])


def current_process_memory_mb() -> tuple[float, float] | None:
    """이 인덱싱 프로세스 자신의 (현재, 최대) 워킹셋 MB. 실패하면 None.

    새 측정 코드가 아니다 — `slm/runtime.py`의 `process_memory_mb(pid)`는
    이미 임의 PID를 받는 범용 함수라(원래는 llama-server를 재려고 만들어짐),
    `os.getpid()`로 자기 자신을 넘기기만 하면 된다.

    🔴 워킹셋은 OS가 트리밍하면 실제 누수 없이도 줄어든다 — 체크포인트
    로그의 "요약"용으로만 쓰고, 실제 증가 여부 판단은
    `current_process_memory_detail()`의 private bytes로 한다 (T10.58).
    """
    from slm.runtime import process_memory_mb

    return process_memory_mb(os.getpid())


def current_process_memory_detail():
    """이 프로세스의 워킹셋 + private bytes(커밋) 전부. 실패하면 None.

    2026-08-21 사고를 이벤트 로그로 되짚을 때 쓴 "+8.35GB" 수치는 커밋
    (private bytes) 기준이었다 — 워킹셋은 OS 트리밍으로 실제로는 안 새는데도
    줄어들 수 있어(`slm.runtime.process_memory_mb` docstring 참고) 같은
    사고를 다음에 로그만으로 판별하려면 이 값이 있어야 한다.
    """
    from slm.runtime import process_memory_detail_mb

    return process_memory_detail_mb(os.getpid())

"""PyInstaller 진입점 (T9.1).

`python -m ui.app`와 동작은 같다 — 실제 로직은 전부 `ui.app`에 있고,
PyInstaller `Analysis(scripts=[...])`가 모듈이 아니라 스크립트 파일을
요구해서 이 얇은 래퍼가 필요하다.
"""

from __future__ import annotations

from ui.app import main

if __name__ == "__main__":
    raise SystemExit(main())

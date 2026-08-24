"""QApplication 진입점 (T4.1).

    python -m ui.app
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from config.settings import PROJECT_ROOT
from ui.main_window import MainWindow

# `PROJECT_ROOT`(config.settings)를 쓴다 — `__file__` 기준이면 PyInstaller로
# 얼린 exe에서 `font/`를 exe 옆이 아니라 번들 내부 기준으로 잘못 찾는다 (T9.2).
FONT_DIR = PROJECT_ROOT / "font"
QSS_PATH = Path(__file__).resolve().parent / "qss" / "app.qss"
# `PROJECT_ROOT`가 아니라 `__file__` 기준 — QSS_PATH와 같은 이유(코드와
# 함께 번들되는 자원, `deploy/app.spec`의 datas에 `ui/icons`로 등록돼 있다).
ICON_PATH = Path(__file__).resolve().parent / "icons" / "app.ico"


def _load_fonts() -> None:
    """나눔고딕을 상대 경로에서 런타임 로딩한다 (DESIGN §10.4 — OS 설치 금지)."""
    QFontDatabase.addApplicationFont(str(FONT_DIR / "NanumGothic.ttf"))
    QFontDatabase.addApplicationFont(str(FONT_DIR / "NanumGothicBold.ttf"))


def _set_taskbar_app_id() -> None:
    """작업표시줄이 python.exe 아이콘으로 그룹핑하는 것을 막는다.

    `python run_app.py`처럼 얼리지 않은 스크립트로 실행하면, Windows는
    AppUserModelID를 따로 지정하지 않는 한 같은 인터프리터로 뜬 모든
    스크립트를 python.exe 아이콘 하나로 묶는다 — 창 자체는
    `setWindowIcon()`으로 앱 아이콘을 걸어도 작업표시줄만 파이썬 아이콘으로
    보이는 이유. PyInstaller로 얼린 배포용 exe는 exe 파일 자체에 아이콘이
    있어 이 문제가 없다(개발 편의를 위한 조치).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ATECMobility.OfflineRAGSearch"
        )
    except (AttributeError, OSError):
        pass  # 실패해도 앱 동작에는 지장 없다 — 작업표시줄 아이콘만 원래대로 남는다


def create_app() -> QApplication:
    _set_taskbar_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    _load_fonts()

    font = QFont()
    font.setFamilies(["NanumGothic", "Malgun Gothic", "sans-serif"])  # DESIGN §10.4 폴백 스택
    font.setPointSize(10)
    app.setFont(font)

    if QSS_PATH.is_file():
        app.setStyleSheet(QSS_PATH.read_text(encoding="utf-8"))

    # 앱 아이콘을 여기 한 곳에서만 건다 — 자기 아이콘을 따로 지정하지 않은
    # 모든 최상위 창(메인 창은 물론, 모델 관리·폴더 관리 등 모든 팝업
    # QDialog)이 자동으로 물려받는다(Qt의 QApplication.windowIcon() 폴백,
    # 2026-08-22 요청 — 창마다 따로 아이콘을 걸 필요가 없다).
    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    return app


def main() -> int:
    app = create_app()

    window = MainWindow()
    window.setWindowTitle("오프라인 문서 검색")
    window.resize(1100, 720)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

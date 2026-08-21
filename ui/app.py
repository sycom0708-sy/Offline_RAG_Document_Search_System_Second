"""QApplication 진입점 (T4.1).

    python -m ui.app
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from config.settings import PROJECT_ROOT
from ui.main_window import MainWindow

# `PROJECT_ROOT`(config.settings)를 쓴다 — `__file__` 기준이면 PyInstaller로
# 얼린 exe에서 `font/`를 exe 옆이 아니라 번들 내부 기준으로 잘못 찾는다 (T9.2).
FONT_DIR = PROJECT_ROOT / "font"
QSS_PATH = Path(__file__).resolve().parent / "qss" / "app.qss"


def _load_fonts() -> None:
    """나눔고딕을 상대 경로에서 런타임 로딩한다 (DESIGN §10.4 — OS 설치 금지)."""
    QFontDatabase.addApplicationFont(str(FONT_DIR / "NanumGothic.ttf"))
    QFontDatabase.addApplicationFont(str(FONT_DIR / "NanumGothicBold.ttf"))


def create_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    _load_fonts()

    font = QFont()
    font.setFamilies(["NanumGothic", "Malgun Gothic", "sans-serif"])  # DESIGN §10.4 폴백 스택
    font.setPointSize(10)
    app.setFont(font)

    if QSS_PATH.is_file():
        app.setStyleSheet(QSS_PATH.read_text(encoding="utf-8"))

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

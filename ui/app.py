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


def create_app() -> QApplication:
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

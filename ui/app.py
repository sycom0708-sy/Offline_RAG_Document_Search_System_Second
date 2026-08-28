"""QApplication 진입점 (T4.1).

    python -m ui.app
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSharedMemory
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from config.settings import PROJECT_ROOT
from ui.main_window import MainWindow
from ui.widgets.info_dialog import show_info

# 중복 실행 방지에 쓰는 공유 메모리 키. AppUserModelID(_set_taskbar_app_id)와
# 같은 이름 공간을 쓴다.
_SINGLE_INSTANCE_KEY = "ATECMobility.OfflineRAGSearch.SingleInstanceGuard"
# 인스톨러(`deploy/installer.iss`의 `AppMutex`)가 감지하는 이름 있는 뮤텍스.
_APP_MUTEX_NAME = "ATECMobility.OfflineRAGSearch.Mutex"

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


def _create_app_mutex() -> None:
    """인스톨러가 감지할 이름 있는 Win32 뮤텍스를 만든다.

    실행 중인 앱 위에 새 버전을 설치하면 인스톨러가 아직 로드돼 있는
    `python314.dll` 등을 덮어쓰다 충돌한다(실사용 중 발견, 2026-08-28) —
    Inno Setup의 `AppMutex`는 이 이름의 뮤텍스가 있으면 설치를 시작하기
    **전에** "먼저 앱을 종료해달라"고 막아준다. `_acquire_single_instance_guard`
    (QSharedMemory)와는 별개다 — Inno Setup은 Win32 뮤텍스만 인식해서 감지용을
    따로 만든다. 핸들을 명시적으로 닫지 않아도 프로세스가 끝나면 시스템이
    자동으로 해제한다.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CreateMutexW(None, False, _APP_MUTEX_NAME)
    except (AttributeError, OSError):
        pass  # 실패해도 앱 동작에는 지장 없다 — 인스톨러의 실행 중 감지만 못 한다


def _acquire_single_instance_guard(key: str = _SINGLE_INSTANCE_KEY) -> QSharedMemory | None:
    """이미 다른 인스턴스가 떠 있으면 None, 아니면 자리를 지키는 객체를 돌려준다.

    Windows의 공유 메모리 세그먼트는 참조 카운트 기반 커널 객체라 이걸 쥔
    프로세스가 (정상 종료든 크래시든) 끝나면 시스템이 자동으로 해제한다 —
    그래서 잔류 락 파일 걱정 없이 `attach()` 성공 여부만으로 중복 실행을
    판별할 수 있다. 반환값은 호출자가 앱 수명 동안 붙들고 있어야
    가비지컬렉션으로 조기에 풀리지 않는다. `key`는 테스트가 실제 실행 중인
    앱과 충돌하지 않도록 격리된 값을 넣을 수 있게 매개변수로 뺐다.
    """
    guard = QSharedMemory(key)
    if guard.attach():
        return None
    if not guard.create(1):
        return None  # 생성마저 실패하면(권한 등) 중복 실행 방지를 포기하고 그냥 띄운다
    return guard


def create_app() -> QApplication:
    _set_taskbar_app_id()
    _create_app_mutex()
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

    guard = _acquire_single_instance_guard()
    if guard is None:
        show_info(
            "이미 실행 중입니다",
            "ATEC DocsAI가 이미 실행되고 있습니다. 작업 표시줄에서 확인해주세요.",
        )
        return 0
    app.single_instance_guard = guard  # 앱 수명 동안 붙들어 GC로 조기 해제되지 않게 한다

    window = MainWindow()
    window.setWindowTitle("ATEC DocsAI")
    window.resize(1100, 720)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

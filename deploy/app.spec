# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 스펙 (T9.1).

`--onedir` 방식으로 빌드한다 — `--onefile`은 실행할 때마다 임시 폴더에
압축을 풀어야 해서 시작이 느리고, 이 앱은 어차피 models/vendor/data처럼
exe 옆에 있어야 하는 큰 폴더들과 함께 배포되므로 onefile의 "파일 하나로
끝" 이점이 의미가 없다.

models/·vendor/·font/·data/는 여기서 다루지 않는다 — PyInstaller의
Analysis 캐시에 수 GB짜리 모델·LibreOffice를 매번 다시 넣게 하는 대신,
`deploy/build.py`가 빌드 뒤 `dist/OfflineRAGSearch/`로 직접 복사한다
(모델이 바뀔 때마다 PyInstaller를 다시 돌릴 필요가 없다).

직접 돌릴 땐 `python -m deploy.build`를 쓴다(이 스펙만 단독으로 돌리면
font/·models/·vendor/ 복사 단계가 빠진다). 사용법 (프로젝트 루트에서):
    pyinstaller deploy/app.spec --distpath dist --workpath build/pyinstaller
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

PROJECT_ROOT = Path(SPECPATH).resolve().parent

# onnxruntime·tokenizers는 컴파일된 확장(.pyd/.dll)을 표준 hook이 놓치는
# 경우가 있어 명시적으로 모은다 — T9.6 첫 실제 빌드에서 막히면 여기부터 확인.
binaries = []
binaries += collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("tokenizers")

datas = [
    (str(PROJECT_ROOT / "ui" / "qss"), "ui/qss"),
    # 창·팝업 아이콘 — ui/app.py가 런타임에 QApplication.setWindowIcon()로 읽는다.
    (str(PROJECT_ROOT / "ui" / "icons"), "ui/icons"),
]
datas += collect_data_files("tokenizers")

# kss(정규식 청킹이 기본값이라 실사용 경로에 없음, chunker.py `use_kss=True`일
# 때만 선택적으로 쓰임)와 그 무거운 의존성(scipy·networkx·pecab 등 약 15개
# 패키지)은 배포본에서 뺀다 — PLAN Phase 9가 "재평가 대상"으로 남겨둔 항목을
# 여기서 정리한다. kss로 실제 청킹을 검증하고 싶으면 `.venv`에서 직접 실행할 것.
excludes = ["kss", "scipy", "networkx", "pecab"]

a = Analysis(
    [str(PROJECT_ROOT / "run_app.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OfflineRAGSearch",
    console=False,  # PySide6 GUI 앱 — 콘솔 창을 띄우지 않는다
    # 문서+돋보기 아이콘(시안 1번, 20% 확대) — `python -m deploy.make_icon`으로 생성
    icon=str(PROJECT_ROOT / "ui" / "icons" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="OfflineRAGSearch",
)

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

# exe 시작부터 Per-Monitor-V2 DPI 인식을 명시한다 — 이게 없으면 exe는 DPI
# 비인식(unaware) 상태로 시작해 Windows가 스플래시 창을 배율만큼 확대해
# 그린다(이 PC 125%). 뒤이어 PySide6가 QApplication 생성 시 자체적으로
# Per-Monitor-V2로 전환하면 Windows가 이미 떠 있던 스플래시 창을 실제
# 픽셀 크기로 다시 계산해, 화면 중앙의 큰 스플래시가 갑자기 작아지며
# 위치까지 옮겨가는 것으로 보였다(실측 재현, 2026-09-06). 매니페스트로
# 처음부터 같은 인식 모드를 강제하면 이 전환 자체가 없어져 크기·위치가
# 고정된다 — PyInstaller 기본 매니페스트(winmanifest._DEFAULT_MANIFEST_XML)
# 에 dpiAwareness만 추가한 것이다.
_MANIFEST_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"></requestedExecutionLevel>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{e2011457-1546-43c5-a5fe-008deee3d3f0}"></supportedOS>
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"></supportedOS>
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"></supportedOS>
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"></supportedOS>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"></supportedOS>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
    </windowsSettings>
  </application>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"></assemblyIdentity>
    </dependentAssembly>
  </dependency>
</assembly>
"""

# exe 실행 직후~메인 창이 뜨기 전까지(모듈 임포트·임베더 워밍업 구간) 보여줄
# 정적 이미지. `python -m deploy.make_splash`로 생성(`ui/icons/splash.png`).
# `ui/app.py`의 `main()`이 `pyi_splash.close()`로 명시적으로 닫는다 — 안 닫으면
# 메인 창 뒤에 계속 떠 있는다. Windows/Linux 전용(macOS 미지원, 이 프로젝트와
# 무관), tkinter가 필요해 PyInstaller가 자동으로 함께 번들한다.
splash = Splash(
    str(PROJECT_ROOT / "ui" / "icons" / "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    [],
    exclude_binaries=True,
    name="OfflineRAGSearch",
    console=False,  # PySide6 GUI 앱 — 콘솔 창을 띄우지 않는다
    # 문서+돋보기 아이콘(시안 1번, 20% 확대) — `python -m deploy.make_icon`으로 생성
    icon=str(PROJECT_ROOT / "ui" / "icons" / "app.ico"),
    manifest=_MANIFEST_XML,
)

coll = COLLECT(
    exe,
    splash.binaries,
    a.binaries,
    a.datas,
    name="OfflineRAGSearch",
)

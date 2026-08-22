"""포터블 배포 폴더를 만든다 (T9.1, T9.4).

PyInstaller로 exe+런타임을 빌드한 뒤, exe 옆에 있어야 하는 나머지 조각
(font/·models/·vendor/)을 복사해 `dist/OfflineRAGSearch/`를 완성한다.
이 폴더가 그대로 포터블 배포본이다 — zip으로 압축하면 끝(TECH 9.4).
인스톨러가 필요하면 이 폴더를 `deploy/installer.iss`(Inno Setup)로
감싼다.

`data/`는 넣지 않는다 — 사용자별 인덱스·설정이 쌓이는 곳이라 배포본에
남의 흔적이 섞이면 안 된다(Phase 3 "인덱스는 이식할 수 없다").

🔴 이 폴더 이름이 `packaging`이 아니라 `deploy`인 이유: pip 생태계에
`packaging`이라는 실제 라이브러리가 이미 있다(버전 파싱 등, PyInstaller
자신도 내부적으로 쓴다) — `python -m packaging.build`처럼 리포 루트에서
실행하면 cwd가 sys.path 맨 앞에 붙어 우리 폴더가 그 라이브러리를 가려
버리고, PyInstaller 실행 자체가 깨진다.

    python -m deploy.build                 # 전체(빌드 + 복사)
    python -m deploy.build --copy-only      # PyInstaller는 이미 돌렸고 복사만 다시
    python -m deploy.build --skip-libreoffice  # LibreOffice 없이 빌드(용량 절약, 개발용)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
APP_DIR = DIST_DIR / "OfflineRAGSearch"
BUILD_WORKPATH = PROJECT_ROOT / "build" / "pyinstaller"

# exe 옆에 그대로 복사할 폴더들. LibreOffice는 사용자 확정으로 인스톨러에
# 포함한다(2026-08-21) — vendor/llamacpp(45MB)는 항상, LibreOfficePortable
# (1.5GB 실측)은 --skip-libreoffice로만 뺄 수 있다(용량이 커서 개발 중
# 반복 빌드에는 부담).
_ALWAYS_COPY = ("font",)
_VENDOR_ALWAYS = ("llamacpp",)
_VENDOR_OPTIONAL = ("LibreOfficePortable",)
# models/는 통째로 복사하지 않는다 — slm/는 사용자가 다운로드해서 채우는
# 자리라(TECH 9.3) 개발 PC에 받아둔 걸 그대로 배포본에 섞으면 안 된다.
# 항상 포함하는 건 경량 임베딩(ko-sroberta-multitask)뿐이고, KURE-v1은
# 있으면 같이 넣되(용량이 커 없을 수도 있다) 없어도 빌드를 막지 않는다.
_MODELS_ALWAYS = ("ko-sroberta-multitask",)
_MODELS_OPTIONAL = ("KURE-v1",)


def run_pyinstaller() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            str(PROJECT_ROOT / "deploy" / "app.spec"),
            "--distpath", str(DIST_DIR),
            "--workpath", str(BUILD_WORKPATH),
            "--noconfirm",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def _copy_tree(src: Path, dest: Path) -> None:
    if not src.is_dir():
        print(f"  건너뜀 (없음): {src}")
        return
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"  복사됨: {src} -> {dest}")


def _strip_libreoffice_runtime_cache(libreoffice_dir: Path) -> None:
    """LibreOffice Portable의 사용자 프로필 캐시를 뺀다 (T9.5 실측).

    `App/DefaultData/settings/user/`(첫 실행용 템플릿)와 `Data/`(PortableApps
    런처로 한 번이라도 직접 실행하면 생기는 실제 프로필)는 우리 코드가 **절대
    안 읽는다** — `parser/utils/libreoffice.py`의 `convert()`가 매 변환마다
    `-env:UserInstallation=<임시 프로필>`을 넘겨 항상 새 프로필을 쓰기 때문에
    (동시 실행 시 프로필 충돌을 피하려는 T1.9 설계), PortableApps 런처를 거치지
    않고 `soffice.exe`를 직접 호출하는 이 앱에서 두 폴더는 순수 캐시다.

    🔴 **처음엔 몰랐다가 실제 인스톨러 설치 테스트에서 발견했다** — 이 폴더
    안에 확장자 레지스트리가 만드는 극도로 깊은 임시 경로(`.../
    PackageRegistryBackend/lu....tmp/da/content/...xhp` 등, 실측 223자)가
    있어 Windows MAX_PATH(260자)를 넘겨 설치가 **에러 메시지 없이 조용히
    롤백**됐다(Inno Setup 설치 로그에 "Uninstallation process succeeded"만
    남고 "Installation ... succeeded"는 아예 없었다 — LongPathsEnabled=1로
    이미 켜져 있는 PC에서도 재현됨, Inno Setup 자신이 긴 경로를 지원하지
    않는 것으로 보임). 캐시를 빼면 이 문제 자체가 사라지고, 부수로 배포
    용량도 미세하게(~6MB) 줄어든다.
    """
    for relative in ("App/DefaultData/settings/user", "Data"):
        target = libreoffice_dir / Path(*relative.split("/"))
        if target.is_dir():
            shutil.rmtree(target)
            print(f"  LibreOffice 런타임 캐시 제거: {target}")


def assemble(*, skip_libreoffice: bool) -> None:
    if not APP_DIR.is_dir():
        raise SystemExit(f"PyInstaller 결과물이 없습니다: {APP_DIR} (먼저 빌드하세요)")

    print("font/·vendor/·models/ 복사 중...")
    for name in _ALWAYS_COPY:
        _copy_tree(PROJECT_ROOT / name, APP_DIR / name)

    for name in _VENDOR_ALWAYS:
        _copy_tree(PROJECT_ROOT / "vendor" / name, APP_DIR / "vendor" / name)

    vendor_optional = () if skip_libreoffice else _VENDOR_OPTIONAL
    for name in vendor_optional:
        _copy_tree(PROJECT_ROOT / "vendor" / name, APP_DIR / "vendor" / name)
        if name == "LibreOfficePortable":
            _strip_libreoffice_runtime_cache(APP_DIR / "vendor" / name)

    for name in (*_MODELS_ALWAYS, *_MODELS_OPTIONAL):
        _copy_tree(PROJECT_ROOT / "models" / name, APP_DIR / "models" / name)

    # data/는 의도적으로 안 만든다 — 앱이 첫 실행 시 알아서 만든다
    # (config.settings의 각 *_DIR이 필요할 때 mkdir(parents=True)).

    print(f"\n완료: {APP_DIR}")
    print("이 폴더를 그대로 zip 압축하면 포터블 배포본입니다 (TECH 9.4).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy-only", action="store_true", help="PyInstaller는 건너뛰고 폴더 복사만 다시 한다")
    parser.add_argument("--skip-libreoffice", action="store_true", help="LibreOffice 포터블(1.5GB)을 빼고 빌드한다")
    args = parser.parse_args()

    if not args.copy_only:
        print("PyInstaller 빌드 중 (수 분 걸릴 수 있습니다)...")
        run_pyinstaller()

    assemble(skip_libreoffice=args.skip_libreoffice)


if __name__ == "__main__":
    main()

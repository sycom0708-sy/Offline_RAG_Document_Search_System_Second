"""llama.cpp 사전 빌드 바이너리 준비 (T6.2).

    python -m scripts.setup_llamacpp            # 고정 버전 설치
    python -m scripts.setup_llamacpp --tag latest
    python -m scripts.setup_llamacpp --check    # 설치 여부만 확인

`llama-cpp-python`은 **Python 3.14용 사전 빌드 휠이 없어** 소스 컴파일에
CMake+MSVC가 필요하고, 이는 PRD 4장 "관리자 권한 불필요"와 충돌한다. 대신
공식 릴리스의 Windows CPU x64 바이너리(약 18MB)를 `vendor/llamacpp/`에 풀고
서브프로세스로 호출한다 — LibreOffice(`soffice`)를 부르는 방식과 같다.

인터넷이 되는 PC에서 1회 실행하고, 오프라인 환경에는 `vendor/` 폴더째 옮긴다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from slm.download import download_file
from slm.runtime import VENDOR_DIR, find_llama_server

_REPO = "ggml-org/llama.cpp"
_API_BASE = f"https://api.github.com/repos/{_REPO}/releases"
_DOWNLOAD_BASE = f"https://github.com/{_REPO}/releases/download"

# 측정에 실제로 쓴 버전을 고정해 둔다. 벤치마크는 재현 가능해야 하는데
# `latest`는 며칠 만에 바뀌므로 기본값으로 쓸 수 없다.
DEFAULT_TAG = "b10306"

# 어떤 빌드를 받을지 — CPU 전용 x64. GPU 빌드는 최소 사양(GPU 없음) 기준에
# 어긋나고, CUDA 런타임까지 끌고 와 용량이 몇 배가 된다.
_ASSET_SUFFIX = "bin-win-cpu-x64.zip"

# 릴리스에 함께 들어 있는 실행 파일이 많지만 이 프로젝트가 쓰는 것은 서버뿐이다.
# 설치가 제대로 됐는지 판정할 때만 쓴다.
_REQUIRED_EXE = "llama-server.exe"

_MARKER = "INSTALLED_TAG.txt"


class LlamaSetupError(RuntimeError):
    """바이너리 준비 실패."""


def _resolve_tag(tag: str) -> str:
    """`latest`면 실제 태그로 바꾼다. 그 외에는 그대로 쓴다."""
    if tag != "latest":
        return tag
    try:
        with urllib.request.urlopen(f"{_API_BASE}/latest", timeout=30) as response:
            return json.load(response)["tag_name"]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        raise LlamaSetupError(
            f"최신 릴리스 태그를 확인하지 못했습니다: {exc}\n"
            f"--tag {DEFAULT_TAG} 처럼 태그를 직접 지정할 수 있습니다."
        ) from exc


def _asset_url(tag: str) -> str:
    """`tag` 릴리스의 CPU x64 zip 주소.

    릴리스 API로 자산 목록을 확인하되, 실패해도(레이트 리밋 등) 이름 규칙으로
    만든 주소로 계속 진행한다 — 규칙은 오랫동안 바뀌지 않았고, 틀리면 어차피
    다운로드 단계에서 404로 드러난다.
    """
    fallback = f"{_DOWNLOAD_BASE}/{tag}/llama-{tag}-{_ASSET_SUFFIX}"
    try:
        with urllib.request.urlopen(f"{_API_BASE}/tags/{tag}", timeout=30) as response:
            assets = json.load(response).get("assets", [])
    except (urllib.error.URLError, OSError, ValueError):
        return fallback

    for asset in assets:
        if asset.get("name", "").endswith(_ASSET_SUFFIX):
            return asset["browser_download_url"]
    return fallback


def installed_tag() -> str | None:
    """설치된 버전 태그. 바이너리가 없으면 None."""
    marker = VENDOR_DIR / _MARKER
    if find_llama_server() is None or not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def _extract(archive: Path, dest: Path) -> None:
    """zip을 `dest`에 푼다. 경로 탈출(zip slip)은 거른다."""
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            target = (dest / member).resolve()
            if not target.is_relative_to(resolved_dest):
                raise LlamaSetupError(f"압축 파일에 비정상 경로가 있습니다: {member}")
        zf.extractall(dest)

    if os.name != "nt":
        for path in dest.rglob("llama-*"):
            if path.is_file() and not path.suffix:
                path.chmod(path.stat().st_mode | 0o111)


def setup(tag: str = DEFAULT_TAG, *, force: bool = False, quiet: bool = False) -> Path:
    """바이너리를 준비하고 `llama-server` 경로를 반환한다."""
    if platform.system() != "Windows":
        raise LlamaSetupError(
            f"이 스크립트는 Windows CPU x64 빌드만 받습니다(현재: {platform.system()}). "
            "다른 OS에서는 직접 빌드한 뒤 환경변수 LLAMA_SERVER_PATH를 지정하세요."
        )

    current = installed_tag()
    if current and not force:
        exe = find_llama_server()
        if not quiet:
            print(f"이미 설치됨: {exe} (버전 {current})")
        return exe  # type: ignore[return-value]

    tag = _resolve_tag(tag)
    url = _asset_url(tag)
    archive = VENDOR_DIR / f"llama-{tag}-{_ASSET_SUFFIX}"

    if not quiet:
        print(f"내려받는 중: llama.cpp {tag}")
        print(f"  {url}")
    download_file(url, archive, quiet=quiet)

    # 이전 설치가 남아 있으면 섞이지 않게 지운다 — 릴리스마다 DLL 구성이 다르다.
    if force:
        for child in VENDOR_DIR.iterdir():
            if child != archive:
                shutil.rmtree(child) if child.is_dir() else child.unlink()

    if not quiet:
        print(f"압축 해제: {VENDOR_DIR}")
    _extract(archive, VENDOR_DIR)
    archive.unlink(missing_ok=True)

    exe = find_llama_server()
    if exe is None:
        raise LlamaSetupError(
            f"압축은 풀렸지만 {_REQUIRED_EXE}를 찾지 못했습니다: {VENDOR_DIR}\n"
            "릴리스의 압축 구조가 바뀌었을 수 있습니다."
        )
    (VENDOR_DIR / _MARKER).write_text(tag, encoding="utf-8")

    if not quiet:
        print(f"완료: {exe}")
    return exe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.setup_llamacpp")
    parser.add_argument("--tag", default=DEFAULT_TAG,
                        help=f"릴리스 태그 또는 latest (기본: {DEFAULT_TAG})")
    parser.add_argument("--force", action="store_true", help="이미 있어도 다시 받는다")
    parser.add_argument("--check", action="store_true", help="설치 여부만 확인한다")
    args = parser.parse_args(argv)

    if args.check:
        exe = find_llama_server()
        if exe is None:
            print("llama-server: 미설치")
            return 1
        print(f"llama-server: {exe} (버전 {installed_tag() or '알 수 없음'})")
        return 0

    try:
        setup(args.tag, force=args.force)
    except LlamaSetupError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

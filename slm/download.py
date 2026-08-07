"""sLM(GGUF) 다운로드 (T6.1).

`indexer/vector/download.py`와 같은 방식이다 — 인터넷이 되는 PC에서 1회
받아두고, 오프라인 환경에는 `models/` 폴더째 옮긴다(PRD 6장). `requests` 등
새 의존성을 들이지 않고 표준 `urllib`만 쓴다.

임베딩 모델(111MB)과 달리 GGUF는 건당 1~5GB라 **중단된 다운로드를 이어받는
기능**이 필요하다 — 전부 다시 받으면 대가가 너무 크다.

    python -m slm.download                    # 후보 전체
    python -m slm.download exaone-4.0-1.2b    # 하나만
    python -m slm.download --list
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

from config.settings import SLM_CANDIDATES, SLM_ORDER, SlmProfile, get_slm_profile

_HF_BASE = "https://huggingface.co/{repo_id}/resolve/main/{path}"
_BLOCK = 1024 * 1024  # 1MB — GGUF는 크므로 블록도 크게

# HF가 404/LFS 안내 HTML을 200으로 돌려주는 경우가 있어 크기로 한 번 더 거른다.
_MIN_GGUF_BYTES = 100 * 1024 * 1024


class SlmDownloadError(RuntimeError):
    """GGUF 파일을 받지 못했다."""


def _remote_size(url: str) -> int | None:
    """Content-Length를 미리 확인한다. 실패하면 None(이어받기 판단만 못 할 뿐)."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def download_file(url: str, dest: Path, *, quiet: bool = False, resume: bool = True) -> Path:
    """`url`을 `dest`로 받는다. 중단된 `.part`가 있으면 이어받는다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    total = _remote_size(url)
    offset = part.stat().st_size if (resume and part.is_file()) else 0

    if total is not None and offset == total:
        part.replace(dest)  # 받다가 딱 끝나고 이동만 못 한 경우
        return dest
    if total is not None and offset > total:
        offset = 0  # 원격 파일이 바뀐 듯하다 — 처음부터
        part.unlink(missing_ok=True)

    request = urllib.request.Request(url)
    mode = "wb"
    if offset:
        request.add_header("Range", f"bytes={offset}-")
        mode = "ab"

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            # 서버가 Range를 무시하고 200으로 전체를 주면 처음부터 다시 써야 한다.
            if offset and response.status != 206:
                offset = 0
                mode = "wb"
            downloaded = offset
            with open(part, mode) as fp:
                while True:
                    block = response.read(_BLOCK)
                    if not block:
                        break
                    fp.write(block)
                    downloaded += len(block)
                    if not quiet:
                        _print_progress(dest.name, downloaded, total)
    except (urllib.error.URLError, OSError) as exc:
        raise SlmDownloadError(
            f"다운로드 실패: {url}\n원인: {exc}\n"
            f"이미 받은 부분은 {part.name}에 남아 있어 다시 실행하면 이어받습니다."
        ) from exc

    if not quiet:
        print(file=sys.stderr)
    part.replace(dest)
    return dest


def _print_progress(name: str, done: int, total: int | None) -> None:
    if total:
        pct = done / total * 100
        print(f"\r  {name}: {done/1e9:5.2f} / {total/1e9:.2f} GB ({pct:5.1f}%)",
              end="", file=sys.stderr, flush=True)
    else:
        print(f"\r  {name}: {done/1e9:5.2f} GB", end="", file=sys.stderr, flush=True)


def download_slm(profile: SlmProfile, *, force: bool = False, quiet: bool = False) -> Path:
    """후보 하나의 GGUF를 받고 로컬 경로를 반환한다."""
    if profile.is_installed() and not force:
        if not quiet:
            print(f"이미 설치됨: {profile.local_path}")
        return profile.local_path

    url = _HF_BASE.format(repo_id=profile.repo_id, path=profile.gguf_file)
    if not quiet:
        print(f"내려받는 중: {profile.label} ({profile.size_gb:.2f} GB)")
        print(f"  {url}")

    download_file(url, profile.local_path, quiet=quiet)
    _verify(profile)
    if not quiet:
        print(f"완료: {profile.local_path}")
    return profile.local_path


def _verify(profile: SlmProfile) -> None:
    path = profile.local_path
    if not path.is_file():
        raise SlmDownloadError(f"GGUF 파일이 없습니다: {path}")
    size = path.stat().st_size
    if size < _MIN_GGUF_BYTES:
        raise SlmDownloadError(
            f"GGUF 파일이 비정상적으로 작습니다({size:,} bytes): {path}\n"
            "레포에 해당 파일이 없거나 경로가 바뀌었을 수 있습니다."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m slm.download")
    parser.add_argument("keys", nargs="*", choices=SLM_ORDER, default=[],
                        help="받을 후보 키 (생략하면 전체)")
    parser.add_argument("--list", action="store_true", help="후보 목록만 보여준다")
    parser.add_argument("--force", action="store_true", help="이미 있어도 다시 받는다")
    args = parser.parse_args(argv)

    if args.list:
        total = sum(p.size_gb for p in SLM_CANDIDATES)
        for p in SLM_CANDIDATES:
            mark = "설치됨" if p.is_installed() else "미설치"
            print(f"  {p.key:<18} {p.size_gb:>5.2f} GB  [{mark}]  {p.label}")
            if p.note:
                print(f"  {'':<18} {'':>5}      {p.note}")
        print(f"\n  합계 {total:.2f} GB")
        return 0

    targets = [get_slm_profile(k) for k in args.keys] if args.keys else list(SLM_CANDIDATES)
    for profile in targets:
        try:
            download_slm(profile, force=args.force)
        except SlmDownloadError as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

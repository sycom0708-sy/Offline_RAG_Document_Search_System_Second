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
import hashlib
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from config.settings import SLM_CANDIDATES, SLM_ORDER, SlmProfile, get_slm_profile

_HF_BASE = "https://huggingface.co/{repo_id}/resolve/main/{path}"
_BLOCK = 1024 * 1024  # 1MB — GGUF는 크므로 블록도 크게

# HF가 404/LFS 안내 HTML을 200으로 돌려주는 경우가 있어 크기로 한 번 더 거른다.
_MIN_GGUF_BYTES = 100 * 1024 * 1024


class SlmDownloadError(RuntimeError):
    """GGUF 파일을 받지 못했다."""


class SlmDownloadCancelled(SlmDownloadError):
    """사용자가 다운로드 도중 취소했다 — 실패가 아니라 중단이다.

    `.part` 파일은 그대로 남겨 다음 시도가 이어받을 수 있게 한다(모델 관리
    다이얼로그의 "닫아도 이어받을 수 있습니다" 안내와 짝을 이룬다).
    """


def _remote_size(url: str) -> int | None:
    """Content-Length를 미리 확인한다. 실패하면 None(이어받기 판단만 못 할 뿐)."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def download_file(
    url: str,
    dest: Path,
    *,
    quiet: bool = False,
    resume: bool = True,
    on_progress: Callable[[int, int | None], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """`url`을 `dest`로 받는다. 중단된 `.part`가 있으면 이어받는다.

    `on_progress(받은 바이트, 전체 바이트 또는 None)`을 넘기면 CLI 출력 대신
    이 콜백으로 진행률을 알린다 — UI가 진행률 바를 그리는 용도(모델 관리
    다이얼로그의 다운로드 버튼).

    `cancel_event`가 세팅되면 다음 블록을 받기 전에 `SlmDownloadCancelled`를
    던지고 멈춘다. 이미 받은 바이트는 `.part`에 그대로 남아 다음 호출이
    이어받는다 — 취소는 삭제가 아니다.
    """
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
                    if cancel_event is not None and cancel_event.is_set():
                        raise SlmDownloadCancelled(f"사용자가 다운로드를 취소했습니다: {dest.name}")
                    block = response.read(_BLOCK)
                    if not block:
                        break
                    fp.write(block)
                    downloaded += len(block)
                    if on_progress is not None:
                        on_progress(downloaded, total)
                    elif not quiet:
                        _print_progress(dest.name, downloaded, total)
    except SlmDownloadCancelled:
        raise
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
    # GGUF는 GB 단위지만 이 함수는 18MB짜리 llama.cpp 바이너리도 받는다
    # (`scripts/setup_llamacpp.py`) — 단위를 크기에 맞춘다.
    scale, unit = (1e9, "GB") if (total or done) >= 1e9 else (1e6, "MB")
    if total:
        pct = done / total * 100
        print(f"\r  {name}: {done/scale:6.2f} / {total/scale:.2f} {unit} ({pct:5.1f}%)",
              end="", file=sys.stderr, flush=True)
    else:
        print(f"\r  {name}: {done/scale:6.2f} {unit}", end="", file=sys.stderr, flush=True)


def download_slm(
    profile: SlmProfile,
    *,
    force: bool = False,
    quiet: bool = False,
    on_progress: Callable[[int, int | None], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """후보 하나의 GGUF를 받고 로컬 경로를 반환한다."""
    if profile.is_installed() and not force:
        if not quiet:
            print(f"이미 설치됨: {profile.local_path}")
        return profile.local_path

    url = _HF_BASE.format(repo_id=profile.repo_id, path=profile.gguf_file)
    if not quiet:
        print(f"내려받는 중: {profile.label} ({profile.size_gb:.2f} GB)")
        print(f"  {url}")

    download_file(url, profile.local_path, quiet=quiet, on_progress=on_progress, cancel_event=cancel_event)
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


def file_sha256(path: Path, *, chunk_bytes: int = 8 * 1024 * 1024, progress=None) -> str:
    """파일의 SHA256을 계산한다 (TECH 9.3 "새로고침(파일 확인)" 검증용).

    GB 단위 파일을 통째로 읽으므로 **UI 스레드에서 부르면 안 된다.**
    `progress(read_bytes, total_bytes)` 콜백으로 진행률을 알릴 수 있다.
    """
    digest = hashlib.sha256()
    total = path.stat().st_size
    read = 0
    with open(path, "rb") as fp:
        while True:
            block = fp.read(chunk_bytes)
            if not block:
                break
            digest.update(block)
            read += len(block)
            if progress is not None:
                progress(read, total)
    return digest.hexdigest()


def _verified_cache_path() -> Path:
    from config.settings import PROJECT_ROOT

    return PROJECT_ROOT / "data" / "slm_verified.json"


def load_verified_marker(profile: SlmProfile) -> bool:
    """이 파일이 **이미 검증된 그대로인지** 돌려준다.

    체크섬 검증은 GB 단위 파일을 통째로 읽는다. 같은 바이트를 새로고침할
    때마다 다시 읽는 것은 낭비이고, 사용자에게는 버튼이 매번 수십 초 도는
    것으로 보인다. 크기와 mtime이 그대로면 내용도 그대로라고 본다 — 이
    수준의 보수성은 "사용자가 파일을 잘못 넣었는지" 잡는 목적에 충분하다.
    """
    path = profile.local_path
    if not path.is_file():
        return False
    try:
        data = json.loads(_verified_cache_path().read_text(encoding="utf-8"))
        entry = data.get(profile.key) or {}
        stat = path.stat()
        return (
            entry.get("size") == stat.st_size
            and entry.get("mtime") == _rounded_mtime(stat.st_mtime)
            and entry.get("sha256") == profile.sha256
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _rounded_mtime(mtime: float) -> float:
    """mtime을 소수점 아래까지 그대로 쓰면 파일시스템별 정밀도 차이로 어긋난다."""
    return round(mtime, 3)


def save_verified_marker(profile: SlmProfile) -> None:
    """검증 통과를 기록한다. 실패해도 조용히 넘어간다 — 캐시일 뿐이다."""
    path = profile.local_path
    if not path.is_file():
        return
    cache = _verified_cache_path()
    try:
        data = {}
        if cache.is_file():
            data = json.loads(cache.read_text(encoding="utf-8"))
        stat = path.stat()
        data[profile.key] = {
            "size": stat.st_size,
            "mtime": _rounded_mtime(stat.st_mtime),
            "sha256": profile.sha256,
        }
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, json.JSONDecodeError, TypeError):
        pass


def verify_installed(profile: SlmProfile, *, check_hash: bool = True, progress=None) -> str | None:
    """설치된 GGUF를 검증한다. 정상이면 None, 문제가 있으면 사유 문자열.

    체크섬을 아는 모델만 해시를 대조한다 — 이 저장소가 실제로 받아본 적 없는
    후보에는 기록된 값이 없다(`SlmProfile.sha256` 주석 참고). 그 경우 크기만
    본다.
    """
    path = profile.local_path
    if not path.is_file():
        return f"파일이 없습니다: {path}"

    size = path.stat().st_size
    if profile.size_bytes and size != profile.size_bytes:
        return (
            f"파일 크기가 다릅니다 (예상 {profile.size_bytes:,} / 실제 {size:,} bytes). "
            "다운로드가 중단됐거나 다른 파일일 수 있습니다."
        )
    if size < _MIN_GGUF_BYTES:
        return f"파일이 비정상적으로 작습니다({size:,} bytes)."

    if check_hash and profile.sha256:
        actual = file_sha256(path, progress=progress)
        if actual.lower() != profile.sha256.lower():
            return (
                "체크섬이 일치하지 않습니다. 파일이 손상됐거나 다른 버전입니다.\n"
                f"  예상 {profile.sha256}\n  실제 {actual}"
            )
    return None


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

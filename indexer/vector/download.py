"""임베딩 모델 다운로드 (T3.1).

**인터넷이 있는 PC에서 1회 실행**하는 준비 단계다. 실제 사용 환경은 완전
오프라인이므로(PRD 6장), 받아둔 `models/` 폴더를 프로그램과 함께 배포한다.

레포 전체를 받지 않고 추론에 필요한 2개 파일(int8 ONNX, tokenizer.json)만
선별해 받는다 — `jhgan/ko-sroberta-multitask` 레포 전체는 fp32 가중치·
TF·OpenVINO 사본까지 포함해 1.5GB가 넘지만, 실제 필요한 것은 111MB뿐이다.

    python -m indexer.vector.download
    python -m indexer.vector.download --profile KURE-v1
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from config.settings import PROFILE_ORDER, ModelProfile, get_profile

_HF_BASE = "https://huggingface.co/{repo_id}/resolve/main/{path}"
_MIN_ONNX_BYTES = 1_000_000  # 받다 만 파일을 정상으로 오인하지 않기 위한 하한선


class ModelDownloadError(RuntimeError):
    """모델 파일을 받지 못했다."""


def _download(url: str, dest: Path, on_progress=None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(tmp, "wb") as fp:
                while True:
                    block = response.read(1024 * 256)
                    if not block:
                        break
                    fp.write(block)
                    downloaded += len(block)
                    if on_progress is not None:
                        on_progress(downloaded, total)
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise ModelDownloadError(
            f"다운로드 실패: {url}\n원인: {exc}\n"
            "인터넷이 되는 PC에서 받은 뒤 models/ 폴더를 통째로 옮겨도 됩니다."
        ) from exc

    # 부분 파일이 최종 경로에 남지 않도록 다 받은 뒤에만 이동한다.
    tmp.replace(dest)


def download_profile(profile: ModelProfile, force: bool = False, quiet: bool = False) -> Path:
    """프로파일에 필요한 파일을 내려받고 로컬 디렉터리를 반환한다."""
    if profile.is_installed() and not force:
        if not quiet:
            print(f"이미 설치됨: {profile.local_dir}")
        return profile.local_dir

    for repo_path, local_name in profile.files:
        url = _HF_BASE.format(repo_id=profile.repo_id, path=repo_path)
        dest = profile.local_dir / local_name

        def on_progress(done: int, total: int, _name=local_name) -> None:
            if quiet:
                return
            if total:
                pct = done / total * 100
                print(f"\r  {_name}: {done/1e6:6.1f} / {total/1e6:.1f} MB ({pct:5.1f}%)",
                      end="", file=sys.stderr, flush=True)
            else:
                print(f"\r  {_name}: {done/1e6:6.1f} MB", end="", file=sys.stderr, flush=True)

        if not quiet:
            print(f"내려받는 중: {profile.repo_id}/{repo_path}")
        _download(url, dest, on_progress)
        if not quiet:
            print(file=sys.stderr)

    _verify(profile)
    if not quiet:
        print(f"완료: {profile.local_dir}")
    return profile.local_dir


def _verify(profile: ModelProfile) -> None:
    if not profile.onnx_path.is_file():
        raise ModelDownloadError(f"ONNX 파일이 없습니다: {profile.onnx_path}")
    if not profile.tokenizer_path.is_file():
        raise ModelDownloadError(f"토크나이저 파일이 없습니다: {profile.tokenizer_path}")

    size = profile.onnx_path.stat().st_size
    if size < _MIN_ONNX_BYTES:
        # HF가 404/LFS 안내 HTML을 200으로 돌려주는 경우가 있어 크기로 한 번 더 거른다.
        raise ModelDownloadError(
            f"ONNX 파일이 비정상적으로 작습니다({size:,} bytes): {profile.onnx_path}\n"
            "레포에 해당 파일이 없거나 경로가 바뀌었을 수 있습니다."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m indexer.vector.download")
    parser.add_argument("--profile", default=None, choices=PROFILE_ORDER)
    parser.add_argument("--force", action="store_true", help="이미 있어도 다시 받는다")
    args = parser.parse_args(argv)

    profile = get_profile(args.profile)
    try:
        download_profile(profile, force=args.force)
    except ModelDownloadError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

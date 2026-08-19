"""흩어진 `.assets` 폴더를 지운다 — 중앙화(Phase 11-D) 이후의 일회성 정리.

    python -m scripts.clean_assets <대상폴더>              # 무엇이 지워질지만 본다 (기본, 안전)
    python -m scripts.clean_assets <대상폴더> --yes         # 실제로 지운다
    python -m scripts.clean_assets <대상폴더> --db 경로     # 다른 인덱스 DB로 안전성 확인

Phase 11-D 전에는 파서가 이미지를 문서 폴더 옆 `<문서폴더>/.assets/<파일명>/`에
저장했다 — 문서가 서브폴더 곳곳에 있으니 `.assets`도 곳곳에 생겼고, 게다가
**사용자의 원본 문서 폴더**를 건드리는 것이라 지우려면 트리 전체를 뒤져야
했다. 지금은 `data/assets/<doc_id>/`(프로젝트 `data/` 안, 이미 `.gitignore`
전체 제외)로 모이지만, 이미 흩어진 옛 폴더는 코드가 스스로 지워주지 않는다 —
이 스크립트가 한 번 걷어낸다. 앞으로는 다시 생기지 않는다(중앙화가 코드
경로 자체를 바꿨다).

**안전장치**: 인덱스 DB의 이미지 청크가 여전히 옛 `.assets` 경로를 가리키고
있으면 지우지 않는다. 그 경로를 참조 중이라는 것은 아직 새 코드로
재인덱싱을 하지 않았다는 뜻이라, 그대로 지우면 이미지 카드가 깨진다 —
먼저 문서 관리 페이지의 `인덱스 업데이트`(또는 `python -m indexer.cli index`)
를 돌려 DB가 새 위치를 가리키게 한 뒤 이 스크립트를 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data" / "index.sqlite3"

LEGACY_ASSET_DIR_NAME = ".assets"


def find_legacy_asset_dirs(root: Path) -> list[Path]:
    """`root` 아래에서 `.assets`라는 이름의 디렉터리를 전부 찾는다.

    `indexer.scanner.scan_folder()`는 정반대 목적(이런 폴더를 **건너뛴다**)이라
    재사용하지 않는다 — 여기서는 정확히 그 폴더 자체를 찾아야 한다.
    """
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        if LEGACY_ASSET_DIR_NAME in dirnames:
            found.append(Path(dirpath) / LEGACY_ASSET_DIR_NAME)
            # 찾은 `.assets` 안까지 내려가 봐야 더 나올 게 없다 — 자기 자신을
            # 순회 대상에서 뺀다(불필요한 탐색 축소).
            dirnames.remove(LEGACY_ASSET_DIR_NAME)
    return found


def dir_stats(path: Path) -> tuple[int, int]:
    """(파일 수, 총 바이트) — 지우기 전 사용자에게 규모를 보여주기 위함."""
    count = 0
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            count += 1
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                pass
    return count, total


def find_lingering_references(conn: sqlite3.Connection, legacy_dirs: list[Path]) -> list[str]:
    """아직 옛 `.assets` 경로를 가리키는 이미지 청크의 경로 목록.

    비어 있지 않으면 아직 재인덱싱이 안 된 것이다 — 지우면 그 청크의 이미지
    카드가 깨진다. Windows는 대소문자를 구분하지 않으므로 `os.path.normcase`로
    접어 비교한다(Phase 8 `_prune_stale_documents()`와 같은 이유).
    """
    normalized_dirs = [os.path.normcase(os.path.normpath(str(d))) + os.sep for d in legacy_dirs]

    offending: list[str] = []
    for (image_json,) in conn.execute("SELECT image_json FROM chunks WHERE image_json IS NOT NULL"):
        try:
            image_path = json.loads(image_json)["image_path"]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        candidate = os.path.normcase(os.path.normpath(image_path))
        if any(candidate.startswith(prefix) for prefix in normalized_dirs):
            offending.append(image_path)
    return offending


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.clean_assets")
    parser.add_argument("folder", help="흩어진 .assets를 찾을 대상 문서 폴더")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"안전성 확인에 쓸 인덱스 DB (기본: {DEFAULT_DB})")
    parser.add_argument("--yes", action="store_true", help="실제로 지운다 (기본은 무엇이 지워질지만 본다)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="DB가 옛 경로를 아직 참조 중이어도 강행한다 (이미지 카드가 깨질 수 있다)",
    )
    args = parser.parse_args(argv)

    root = Path(args.folder)
    if not root.is_dir():
        print(f"대상 폴더가 아닙니다: {root}", file=sys.stderr)
        return 1

    legacy_dirs = find_legacy_asset_dirs(root)
    if not legacy_dirs:
        print(f"{root} 아래에 흩어진 .assets 폴더가 없습니다. 정리할 것이 없습니다.")
        return 0

    total_files = 0
    total_bytes = 0
    print(f"발견된 .assets 폴더 {len(legacy_dirs)}개:")
    for path in legacy_dirs:
        count, size = dir_stats(path)
        total_files += count
        total_bytes += size
        print(f"  {path}  ({count:,}개 파일, {_format_size(size)})")
    print(f"합계: 파일 {total_files:,}개, {_format_size(total_bytes)}")

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"\n인덱스 DB를 찾을 수 없습니다: {db_path} — 안전성을 확인할 수 없어 중단합니다.", file=sys.stderr)
        print("(경로가 다르면 --db로 지정하세요)", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        offending = find_lingering_references(conn, legacy_dirs)
    finally:
        conn.close()

    if offending and not args.force:
        print(
            f"\n🔴 {len(offending)}개 이미지 청크가 아직 옛 위치를 가리키고 있습니다 "
            "— 재인덱싱이 안 됐습니다.",
            file=sys.stderr,
        )
        for path in offending[:5]:
            print(f"  {path}", file=sys.stderr)
        if len(offending) > 5:
            print(f"  ... 외 {len(offending) - 5}건", file=sys.stderr)
        print(
            "\n먼저 문서 관리 페이지의 `인덱스 업데이트`(또는 "
            "`python -m indexer.cli index <폴더>`)를 돌려 DB가 새 위치를 "
            "가리키게 한 뒤 다시 실행하세요. 그래도 강행하려면 --force.",
            file=sys.stderr,
        )
        return 1

    if not args.yes:
        print("\n무엇이 지워질지만 확인했습니다 (dry-run). 실제로 지우려면 --yes를 추가하세요.")
        return 0

    for path in legacy_dirs:
        shutil.rmtree(path, ignore_errors=True)
    print(f"\n{len(legacy_dirs)}개 폴더를 지웠습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

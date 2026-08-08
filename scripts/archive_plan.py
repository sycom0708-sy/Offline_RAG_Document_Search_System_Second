"""plan 모드 계획 원문을 저장소 아카이브로 옮긴다.

    python -m scripts.archive_plan --list          # 후보 나열 + 출처 판정
    python -m scripts.archive_plan --all           # 통과한 것 전부 아카이브
    python -m scripts.archive_plan <파일>          # 하나만
    python -m scripts.archive_plan <파일> --force  # 출처 경고를 무시하고 강행

plan 파일(`~/.claude/plans/*.md`)은 저장소 밖 **PC 로컬**에 있고 파일명이
무작위라, 그대로 두면 다른 PC로 옮기거나 버전관리를 할 수 없다. 그래서
Phase 완료 시 원문을 `PHASE_오프라인RAG시스템_단계별_구현계획서.md`에 옮긴다.

**이 폴더는 전 프로젝트 공용이다.** 이 PC에는 같은 주제(오프라인 RAG)의 다른
프로젝트가 있어 계획서가 섞여 쌓인다. 제목이나 주제로는 구분되지 않는다 —
둘 다 "표 청크", "임베딩 128토큰" 같은 말을 쓰기 때문이다. 실제로 구분되는
것은 **구조**다(`indexing/` vs `indexer/`, `content_original` vs `content`).
그래서 이 스크립트는 계획서가 참조하는 **파일 경로·코드 심볼이 이 저장소에
실제로 있는지** 대조해 남의 것으로 보이면 경고하고 멈춘다.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = PROJECT_ROOT / "PHASE_오프라인RAG시스템_단계별_구현계획서.md"

_ENV_PLANS_DIR = "CLAUDE_PLANS_DIR"
DEFAULT_PLANS_DIR = Path.home() / ".claude" / "plans"

# 대조에서 제외할 디렉터리 — 서드파티 코드까지 뒤지면 아무 심볼이나 걸린다.
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache",
              "models", "vendor", "data", "node_modules", ".assets"}
# **`.md`는 넣지 않는다.** 아카이브 문서 자체가 계획서 원문을 담고 있어서,
# 한 번 --force로 남의 계획서를 넣으면 그 안의 심볼이 말뭉치에 섞이고 이후
# 같은 프로젝트의 계획서가 전부 통과해버린다(자기 오염).
_SCAN_SUFFIXES = {".py", ".toml", ".json", ".qss"}

# 백틱 안의 토큰만 본다. 본문 산문에서 단어를 긁으면 오탐이 너무 많다.
_BACKTICKED = re.compile(r"`([^`\n]{2,80})`")
_PATH_LIKE = re.compile(r"^[\w./\\-]+\.(py|md|json|toml|qss|txt)$")
_SYMBOL_LIKE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\(\))?$")

# 어느 프로젝트에나 있는 흔한 이름은 판별에 쓸모가 없다.
_TOO_COMMON = {
    "main", "run", "test", "self", "None", "True", "False", "int", "str",
    "list", "dict", "path", "Path", "README.md", "setup.py", "__init__.py",
    "pyproject.toml", "requirements.txt", "conftest.py",
}

# 참조가 이보다 적으면 판정 자체를 신뢰할 수 없다.
MIN_REFERENCES = 3
# 이 비율 미만이면 다른 프로젝트로 본다.
MATCH_THRESHOLD = 0.5
# 경로가 이만큼 있으면 **경로만으로** 판정한다. 경로 존재 여부는 파일 시스템에
# 물어보므로 텍스트로 오염되지 않는다 — 심볼 대조보다 신뢰할 수 있다.
MIN_PATH_REFERENCES = 3


class ArchiveError(RuntimeError):
    """아카이브를 진행할 수 없다."""


def plans_dir(override: str | None = None) -> Path:
    if override:
        return Path(override)
    return Path(os.environ.get(_ENV_PLANS_DIR) or DEFAULT_PLANS_DIR)


def plan_title(text: str) -> str:
    """계획서의 첫 `# ` 제목. 아카이브 중복 판정에 쓴다."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def extract_references(text: str) -> tuple[set[str], set[str]]:
    """계획서가 언급한 (파일 경로, 코드 심볼)을 뽑는다."""
    paths: set[str] = set()
    symbols: set[str] = set()

    for token in _BACKTICKED.findall(text):
        token = token.strip()
        if token in _TOO_COMMON:
            continue
        if _PATH_LIKE.match(token):
            paths.add(token.replace("\\", "/"))
        elif _SYMBOL_LIKE.match(token):
            name = token.removesuffix("()")
            # 한두 글자짜리는 어디에나 걸린다.
            if len(name) >= 4 and name not in _TOO_COMMON:
                symbols.add(name)
    return paths, symbols


def _repo_corpus() -> str:
    """저장소 소스를 한 덩어리로 읽는다. 심볼 존재 확인용."""
    chunks = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if Path(name).suffix in _SCAN_SUFFIXES:
                try:
                    chunks.append((Path(root) / name).read_text(
                        encoding="utf-8", errors="ignore"))
                except OSError:
                    continue
    return "\n".join(chunks)


class Verdict:
    """출처 판정 결과.

    경로가 충분하면 **경로만으로** 판정한다(`basis`가 그 사실을 남긴다).
    심볼 대조는 경로가 부족할 때만 쓴다 — 말뭉치가 텍스트라 오염될 수 있다.
    """

    def __init__(self, path_hits: list[str], path_misses: list[str],
                 symbol_hits: list[str], symbol_misses: list[str]) -> None:
        self.path_hits = path_hits
        self.path_misses = path_misses
        self.symbol_hits = symbol_hits
        self.symbol_misses = symbol_misses

    @property
    def path_total(self) -> int:
        return len(self.path_hits) + len(self.path_misses)

    @property
    def uses_paths_only(self) -> bool:
        return self.path_total >= MIN_PATH_REFERENCES

    @property
    def matched(self) -> list[str]:
        return self.path_hits if self.uses_paths_only else self.path_hits + self.symbol_hits

    @property
    def missing(self) -> list[str]:
        return self.path_misses if self.uses_paths_only else self.path_misses + self.symbol_misses

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.missing)

    @property
    def ratio(self) -> float:
        return len(self.matched) / self.total if self.total else 0.0

    @property
    def inconclusive(self) -> bool:
        return self.total < MIN_REFERENCES

    @property
    def belongs_here(self) -> bool:
        return not self.inconclusive and self.ratio >= MATCH_THRESHOLD

    def describe(self) -> str:
        if self.inconclusive:
            return f"판정 불가 (대조 가능한 참조 {self.total}개 — {MIN_REFERENCES}개 미만)"
        basis = "파일 경로" if self.uses_paths_only else "경로+심볼"
        mark = "이 프로젝트" if self.belongs_here else "다른 프로젝트로 보임"
        return f"{mark} — {basis} {len(self.matched)}/{self.total} 일치 ({self.ratio:.0%})"


def check_provenance(text: str, corpus: str | None = None) -> Verdict:
    """계획서가 이 저장소 것인지 참조 대조로 판정한다."""
    paths, symbols = extract_references(text)

    path_hits, path_misses = [], []
    for path in sorted(paths):
        (path_hits if (PROJECT_ROOT / path).exists() else path_misses).append(path)

    # 경로만으로 판정이 서면 말뭉치를 읽지 않는다(느리기도 하다).
    if len(paths) >= MIN_PATH_REFERENCES:
        return Verdict(path_hits, path_misses, [], [])

    corpus = _repo_corpus() if corpus is None else corpus
    symbol_hits, symbol_misses = [], []
    for symbol in sorted(symbols):
        (symbol_hits if symbol in corpus else symbol_misses).append(symbol)
    return Verdict(path_hits, path_misses, symbol_hits, symbol_misses)


def already_archived(title: str, archive_text: str) -> bool:
    return bool(title) and f"# {title}" in archive_text


def _provenance_header(plan_path: Path) -> str:
    stamp = datetime.fromtimestamp(plan_path.stat().st_mtime)
    return (
        f"<!-- 출처: {plan_path.name} · {platform.node()} · "
        f"작성 {stamp:%Y-%m-%d %H:%M} · 아카이브 {datetime.now():%Y-%m-%d %H:%M} -->"
    )


def archive(plan_path: Path, *, force: bool = False, corpus: str | None = None) -> str:
    """계획서 원문을 아카이브 문서 맨 위(머리말 다음)에 끼워 넣는다."""
    if not plan_path.is_file():
        raise ArchiveError(f"계획 파일이 없습니다: {plan_path}")
    if not ARCHIVE_PATH.is_file():
        raise ArchiveError(f"아카이브 문서가 없습니다: {ARCHIVE_PATH}")

    text = plan_path.read_text(encoding="utf-8").strip()
    archive_text = ARCHIVE_PATH.read_text(encoding="utf-8")
    title = plan_title(text)

    if already_archived(title, archive_text):
        raise ArchiveError(f"이미 아카이브돼 있습니다: {title}")

    verdict = check_provenance(text, corpus)
    if not verdict.belongs_here and not force:
        raise ArchiveError(
            f"{verdict.describe()}\n"
            f"  이 저장소에 없는 참조: {', '.join(verdict.missing[:8]) or '(없음)'}\n"
            "이 PC에는 같은 주제의 다른 프로젝트가 있습니다. 정말 이 저장소 것이면 "
            "--force로 강행하세요."
        )

    lines = archive_text.splitlines()
    # 머리말 다음의 첫 구분선 아래에 넣는다 — 최신이 위로 쌓이는 구조.
    try:
        insert_at = next(i for i, line in enumerate(lines) if line.strip() == "---") + 1
    except StopIteration:
        raise ArchiveError(
            f"아카이브 문서에서 머리말 구분선(---)을 찾지 못했습니다: {ARCHIVE_PATH}"
        ) from None

    block = ["", _provenance_header(plan_path), "", text, "", "---"]
    ARCHIVE_PATH.write_text(
        "\n".join(lines[:insert_at] + block + lines[insert_at:]) + "\n",
        encoding="utf-8",
    )
    return title


def _iter_plans(directory: Path):
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)


def list_plans(directory: Path) -> int:
    plans = _iter_plans(directory)
    if not plans:
        print(f"계획 파일이 없습니다: {directory}")
        return 0

    archive_text = ARCHIVE_PATH.read_text(encoding="utf-8") if ARCHIVE_PATH.is_file() else ""
    corpus = _repo_corpus()

    print(f"{directory} — {len(plans)}개\n")
    for plan in plans:
        text = plan.read_text(encoding="utf-8", errors="ignore")
        title = plan_title(text) or "(제목 없음)"
        stamp = datetime.fromtimestamp(plan.stat().st_mtime)
        verdict = check_provenance(text, corpus)

        if already_archived(title, archive_text):
            status = "아카이브됨"
        elif verdict.belongs_here:
            status = "아카이브 필요"
        else:
            status = "건너뜀"

        print(f"[{status}] {plan.name}  ({stamp:%Y-%m-%d %H:%M})")
        print(f"    제목: {title}")
        print(f"    판정: {verdict.describe()}")
        if verdict.missing and not verdict.belongs_here:
            print(f"    없는 참조: {', '.join(verdict.missing[:6])}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.archive_plan")
    parser.add_argument("plan", nargs="?", help="아카이브할 계획 파일 경로")
    parser.add_argument("--list", action="store_true", help="후보와 출처 판정만 본다")
    parser.add_argument("--all", action="store_true", help="판정을 통과한 것을 전부 아카이브")
    parser.add_argument("--force", action="store_true", help="출처 경고를 무시한다")
    parser.add_argument("--plans-dir", default=None,
                        help=f"기본값: {DEFAULT_PLANS_DIR} (환경변수 {_ENV_PLANS_DIR})")
    args = parser.parse_args(argv)

    directory = plans_dir(args.plans_dir)

    if args.list:
        return list_plans(directory)

    if args.all:
        corpus = _repo_corpus()
        archived = 0
        for plan in _iter_plans(directory):
            try:
                title = archive(plan, force=args.force, corpus=corpus)
            except ArchiveError as exc:
                print(f"건너뜀 {plan.name}: {str(exc).splitlines()[0]}", file=sys.stderr)
                continue
            print(f"아카이브: {title}  ({plan.name})")
            archived += 1
        print(f"\n{archived}건 아카이브했습니다." if archived else "\n아카이브할 것이 없습니다.")
        return 0

    if not args.plan:
        parser.error("계획 파일 경로를 주거나 --list / --all 을 쓰세요")

    try:
        title = archive(Path(args.plan), force=args.force)
    except ArchiveError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print(f"아카이브: {title}\n  → {ARCHIVE_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

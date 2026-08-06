"""임시 CLI — UI(Phase 4) 완성 전까지 인덱싱·검색을 눈으로 확인하기 위한 도구 (T2.9).

    python -m indexer.cli index <폴더>
    python -m indexer.cli search <질의> [--case-sensitive] [--exact-word] [--types text,table]
"""

from __future__ import annotations

import argparse
import sys

from indexer.fts5.schema import connect
from indexer.fts5.search import search
from indexer.pipeline import index_folder

DEFAULT_DB = "index.sqlite3"


def _cmd_index(args: argparse.Namespace) -> int:
    conn = connect(args.db)

    def on_progress(done: int, total: int, path) -> None:
        bar_width = 30
        filled = int(bar_width * done / total) if total else bar_width
        bar = "#" * filled + "-" * (bar_width - filled)
        name = path.name[:40].ljust(40)
        print(f"\r[{bar}] {done}/{total} {name}", end="", file=sys.stderr, flush=True)

    failures = index_folder(conn, args.folder, on_progress=on_progress)
    print(file=sys.stderr)

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"인덱싱 완료: 문서 {doc_count}개, 청크 {chunk_count}개 -> {args.db}")

    if failures:
        print(f"\n실패 {len(failures)}건:")
        for path, message in failures:
            print(f"  - {path}: {message}")

    conn.close()
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    results = search(
        conn,
        args.query,
        case_sensitive=args.case_sensitive,
        exact_word=args.exact_word,
        types=args.types.split(",") if args.types else None,
        limit=args.limit,
    )

    if not results:
        print("검색 결과가 없습니다.")
        conn.close()
        return 0

    for i, r in enumerate(results, start=1):
        location = f"p.{r.page_or_slide}" if r.page_or_slide is not None else "-"
        excerpt = r.content[:120].replace("\n", " ")
        print(f"[{i}] {r.file_name} ({r.type.value}, {location}, score={r.score:.3f})")
        print(f"    {excerpt}")

    conn.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m indexer.cli")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"인덱스 DB 경로 (기본: {DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="폴더를 스캔해 인덱싱한다")
    p_index.add_argument("folder", help="인덱싱할 대상 폴더")
    p_index.set_defaults(func=_cmd_index)

    p_search = sub.add_parser("search", help="키워드로 검색한다")
    p_search.add_argument("query", help="검색어")
    p_search.add_argument("--case-sensitive", action="store_true", help="대/소문자 구분")
    p_search.add_argument(
        "--exact-word", action="store_true", help="완전 일치 단어만 검색 (기본: 접두 매칭)"
    )
    p_search.add_argument("--types", help="쉼표로 구분된 청크 타입 필터 (예: text,table)")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.set_defaults(func=_cmd_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

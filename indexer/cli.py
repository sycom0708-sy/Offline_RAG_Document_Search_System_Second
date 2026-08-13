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

    report = index_folder(conn, args.folder, on_progress=on_progress, embed=not args.no_embed)
    print(file=sys.stderr)

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    vector_count = conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
    print(
        f"인덱싱 완료: 문서 {doc_count}개, 청크 {chunk_count}개, "
        f"벡터 {vector_count}개, 건너뜀 {report.skipped}개 -> {args.db}"
    )

    for warning in report.warnings:
        print(f"\n[안내] {warning}")

    if report.failures:
        print(f"\n실패 {len(report.failures)}건:")
        for path, message in report.failures:
            print(f"  - {path}: {message}")

    conn.close()
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    conn = connect(args.db)
    types = args.types.split(",") if args.types else None

    if args.hybrid or args.compare:
        from search.hybrid_search import hybrid_search

        hybrid = hybrid_search(
            conn,
            args.query,
            case_sensitive=args.case_sensitive,
            exact_word=args.exact_word,
            types=types,
            limit=args.limit,
        )
    if args.hybrid and not args.compare:
        _print_hybrid(hybrid)
        conn.close()
        return 0

    results = search(
        conn,
        args.query,
        case_sensitive=args.case_sensitive,
        exact_word=args.exact_word,
        types=types,
        limit=args.limit,
    )

    if args.compare:
        print("=== 키워드 단독 (BM25) ===")
        _print_keyword(results)
        print("\n=== 하이브리드 (벡터 재순위) ===")
        _print_hybrid(hybrid)
        conn.close()
        return 0

    _print_keyword(results)
    conn.close()
    return 0


def _location(page_or_slide) -> str:
    return f"p.{page_or_slide}" if page_or_slide is not None else "-"


def _print_keyword(results) -> None:
    if not results:
        print("검색 결과가 없습니다.")
        return
    for i, r in enumerate(results, start=1):
        excerpt = r.content[:120].replace("\n", " ")
        print(f"[{i}] {r.file_name} ({r.type.value}, {_location(r.page_or_slide)}, bm25={r.score:.3f})")
        print(f"    {excerpt}")


def _print_hybrid(results) -> None:
    if not results:
        print("검색 결과가 없습니다.")
        return
    for i, h in enumerate(results, start=1):
        excerpt = h.content[:120].replace("\n", " ")
        if h.similarity is None:
            score = "유사도 없음(벡터 미생성)"
        else:
            score = f"유사도={h.similarity:+.3f}"
            if h.is_low_relevance:
                score += " [관련성 낮음]"
        print(f"[{i}] {h.file_name} ({h.type.value}, {_location(h.page_or_slide)}, {score})")
        print(f"    {excerpt}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m indexer.cli")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"인덱스 DB 경로 (기본: {DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="폴더를 스캔해 인덱싱한다")
    p_index.add_argument("folder", help="인덱싱할 대상 폴더")
    p_index.add_argument(
        "--no-embed", action="store_true", help="벡터 임베딩 없이 키워드 인덱싱만 수행"
    )
    p_index.set_defaults(func=_cmd_index)

    p_search = sub.add_parser("search", help="키워드로 검색한다")
    p_search.add_argument("query", help="검색어")
    p_search.add_argument("--case-sensitive", action="store_true", help="대/소문자 구분")
    p_search.add_argument(
        "--exact-word", action="store_true", help="완전 일치 단어만 검색 (기본: 접두 매칭)"
    )
    p_search.add_argument("--types", help="쉼표로 구분된 청크 타입 필터 (예: text,table)")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--hybrid", action="store_true", help="벡터 재순위를 적용해 검색")
    p_search.add_argument(
        "--compare", action="store_true", help="키워드 단독과 하이브리드 결과를 나란히 출력"
    )
    p_search.set_defaults(func=_cmd_search)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""검색 응답 속도·메모리 벤치마크 (T3.8).

    python -m scripts.benchmark_search --db index.sqlite3

TECH 기준은 최소 사양(8GB RAM, GPU 없음)이다. 개발 PC에서 돌린 값은 참고치일
뿐이므로, 출력에 실행 환경을 함께 남긴다.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import time

from config.settings import get_profile
from indexer.fts5.schema import connect
from indexer.fts5.search import search as keyword_search
from indexer.vector.embedder import Embedder
from search.hybrid_search import hybrid_search

DEFAULT_QUERIES = (
    "계약서 검토 기준",
    "리눅스에서 프린터 설정하는 명령어",
    "최소 사양 메모리 요구사항",
    "오프라인 문서 검색 시스템",
)


def _time_it(fn, repeat: int) -> tuple[float, float]:
    """(중앙값 ms, 최댓값 ms). 첫 호출은 워밍업으로 버린다."""
    fn()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), max(samples)


def _memory_mb() -> float | None:
    """현재 프로세스 메모리(MB). 측정 수단이 없으면 None."""
    try:
        import ctypes
        import ctypes.wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if not ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return None
        return counters.WorkingSetSize / 1e6
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.benchmark_search")
    parser.add_argument("--db", default="index.sqlite3")
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--queries", nargs="*", default=list(DEFAULT_QUERIES))
    args = parser.parse_args(argv)

    conn = connect(args.db)
    profile = get_profile()

    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    vector_count = conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]

    print("=" * 68)
    print(f"실행 환경 : {platform.processor() or platform.machine()} / Python {platform.python_version()}")
    print(f"            (최소 사양 8GB 실기가 아니면 참고치로만 볼 것)")
    print(f"인덱스    : 문서 {doc_count}개, 청크 {chunk_count}개, 벡터 {vector_count}개")
    print(f"모델      : {profile.key} ({profile.dim}차원)")
    print("=" * 68)

    baseline = _memory_mb()

    embedder = Embedder(profile)
    start = time.perf_counter()
    embedder.encode_one("워밍업")
    print(f"\n모델 최초 로딩: {(time.perf_counter() - start) * 1000:.0f} ms (1회성)")

    texts = [r[0] for r in conn.execute("SELECT content FROM chunks LIMIT 64")]
    if texts:
        start = time.perf_counter()
        embedder.encode(texts)
        elapsed = time.perf_counter() - start
        print(f"임베딩 처리량 : {len(texts)/elapsed:.0f} 청크/초 ({len(texts)}개 기준)")

    print(f"\n{'질의':38} {'키워드':>12} {'하이브리드':>12}")
    print("-" * 68)
    for query in args.queries:
        kw_median, _ = _time_it(lambda q=query: keyword_search(conn, q, limit=20), args.repeat)
        hy_median, _ = _time_it(
            lambda q=query: hybrid_search(conn, q, embedder=embedder, limit=20), args.repeat
        )
        label = query if len(query) <= 36 else query[:35] + "…"
        print(f"{label:38} {kw_median:9.1f}ms {hy_median:9.1f}ms")

    peak = _memory_mb()
    if baseline is not None and peak is not None:
        print(f"\n메모리: 시작 {baseline:.0f}MB → 종료 {peak:.0f}MB (증가 {peak - baseline:+.0f}MB)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

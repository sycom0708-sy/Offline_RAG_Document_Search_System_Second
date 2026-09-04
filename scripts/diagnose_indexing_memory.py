"""대량 인덱싱 중 python 프로세스 메모리 증가 재현 실험 (T10.58).

2026-08-21 사고에서 llama-server 고아 누적(T10.36, Job Object로 이미 막음)과
별개로, 인덱싱 python 프로세스 자체가 1시간 25분 동안 2.98GB → 11.33GB로
+8.35GB 불었다. 실제 1만 개 문서를 다시 돌려 재현하는 대신, 소수의 실제
`.doc`/`.xls` 샘플을 **파일명만 바꿔 대량으로 복제**한다 — `doc_id`가
절대경로 해시라 내용이 같아도 각각 별개 문서로 취급돼 파싱·임베딩 경로가
그대로 N번 돈다(누적형 결함이면 300~500개 규모에서도 추세가 드러난다).

사용법:

    python -m scripts.diagnose_indexing_memory --source "../exdoc/3. 코칭" --count 300 --profile KURE-v1

작동 방식:
  1. `--source` 폴더의 대상 확장자(기본 `.doc`/`.xls`, `--ext`로 변경 가능)
     파일을 순환하며 `--count`개로 복제한다(스캔 시 건너뛰는 `~$` 잠금
     파일은 자동 제외). 이미지를 파서가 계속 들고 있는지(후보②)를 재려면
     `--ext .docx,.pptx,.hwp`처럼 이미지가 든 실제 문서로 바꿔 돌린다 —
     레거시 변환(LibreOffice)이 끝난 뒤엔 순정 파서에 위임하므로 이미지
     추출 경로 자체는 원본이 legacy든 modern이든 동일하다.
  2. 격리된 임시 폴더 + 임시 DB에 `index_folder()`를 그대로 돌린다(실제
     인덱싱 경로 그대로 — 새 코드 경로를 안 만든다). 파싱 단계는
     `on_progress`(파일 단위)로, 임베딩 단계는 `on_stage`(배치 단위)로
     각각 촘촘히 샘플링한다 — 2026-08-21 사고의 "+8.35GB"에 해당하는
     구간이 어느 단계인지 가르는 것이 이 실험의 핵심이다.
  3. `current_process_memory_detail()`로 워킹셋과 private bytes(커밋)를
     **둘 다** 남긴다. 🔴 워킹셋만 보면 안 된다 — Windows가 한동안 안
     건드린 페이지를 대기 목록으로 옮기면(OS 트리밍) 실제 누수가 없어도
     워킹셋이 뚝 떨어진다(이 스크립트로 실측: ONNX 세션을 올린 뒤 한동안
     추가 할당이 없자 워킹셋이 1.0GB→220MB로 계단식 하락, 그런데도
     PeakWorkingSetSize는 그대로였다). private bytes(커밋)는 OS가
     트리밍한다고 줄지 않으므로 진짜 증가 여부는 이 값으로 판단한다.
  4. 단계별로 전반부/후반부 평균 private bytes를 비교해 "계속 느는지
     (누수)" "일정 수준에서 멈추는지(캐시성 증가)"를 가른다.

`--tracemalloc`을 주면 파이썬 힙에서 유지되는 객체를 스냅샷으로 비교해
어느 코드가 메모리를 쥐고 있는지도 함께 남긴다(ONNX 런타임 내부 아레나
같은 네이티브 확장 메모리는 tracemalloc에 안 잡힌다는 점에 주의 — 그런
경우엔 파이썬 힙은 안 늘어도 private bytes만 느는 결과가 나온다).
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

from config.settings import get_profile
from indexer.fts5.schema import connect
from indexer.index_log import current_process_memory_detail
from indexer.pipeline import STAGE_EMBEDDING, index_folder

_DEFAULT_EXTS = (".doc", ".xls")

Row = tuple[str, int, int, float, float, float, float, float]
# (stage, done, total, elapsed_sec, working_set_mb, peak_working_set_mb,
#  private_bytes_mb, peak_private_bytes_mb)


def _collect_source_files(source: Path, exts: tuple[str, ...]) -> list[Path]:
    files = [
        p
        for p in source.rglob("*")
        if p.is_file()
        and p.suffix.lower() in exts
        and not p.name.startswith("~$")
    ]
    if not files:
        raise SystemExit(f"'{source}'에 {exts} 샘플이 없습니다.")
    return files


def _duplicate(source_files: list[Path], count: int, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for i in range(count):
        template = source_files[i % len(source_files)]
        target = dest / f"{i:04d}_{template.name}"
        shutil.copy2(template, target)
        created.append(target)
    return created


def _print_trend(label: str, rows: list[Row]) -> None:
    """전반부/후반부 평균 private bytes를 비교해 누수 패턴인지 가른다."""
    if len(rows) < 10:
        print(f"[{label}] 표본이 너무 적어 추세를 판단할 수 없습니다 ({len(rows)}건).")
        return

    half = len(rows) // 2
    first_half, second_half = rows[:half], rows[half:]
    first_start, first_end = first_half[0][6], first_half[-1][6]
    second_start, second_end = second_half[0][6], second_half[-1][6]
    first_delta = first_end - first_start
    second_delta = second_end - second_start
    total_growth = rows[-1][6] - rows[0][6]

    print(f"\n[{label}] private bytes(커밋) 추이 — {len(rows)}개 표본")
    print(f"  전반부({len(first_half)}건): {first_start:.1f} → {first_end:.1f}MB (Δ{first_delta:+.1f})")
    print(f"  후반부({len(second_half)}건): {second_start:.1f} → {second_end:.1f}MB (Δ{second_delta:+.1f})")
    print(f"  전체 증가량: {total_growth:+.1f}MB ({total_growth / len(rows):+.3f}MB/샘플)")
    if second_delta > max(first_delta, 0) * 0.5 + 1.0:
        print("  → 후반부에도 비슷한(또는 더 큰) 기울기로 계속 증가 — 누수 패턴에 가깝다.")
    else:
        print("  → 후반부 증가폭이 뚜렷이 줄었다 — 어딘가에서 안정화(캐시성 증가)되는 패턴에 가깝다.")


def main(argv: list[str] | None = None) -> int:
    # Windows 콘솔 기본 코드페이지(cp949)로는 이 스크립트의 한글 출력(특히
    # '—'·'→' 같은 기호)이 깨지거나 UnicodeEncodeError로 죽는다 — 실제로
    # 겪은 문제라 방어적으로 UTF-8로 강제한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="python -m scripts.diagnose_indexing_memory")
    parser.add_argument("--source", required=True, help="샘플이 있는 폴더")
    parser.add_argument("--count", type=int, default=300, help="복제해 만들 파일 수")
    parser.add_argument(
        "--ext", default=",".join(_DEFAULT_EXTS),
        help="복제 대상 확장자, 쉼표로 구분 (기본: .doc,.xls). 이미지 보유 가설(후보②) 검증엔 .docx,.pptx,.hwp 등을 지정",
    )
    parser.add_argument("--no-embed", action="store_true", help="임베딩 단계를 건너뛴다(파싱 단계만 관찰)")
    parser.add_argument("--profile", default=None, help="임베딩 프로파일 키 (ko-sroberta-multitask/KURE-v1). 기본값은 설정의 활성 프로파일")
    parser.add_argument("--tracemalloc", action="store_true", help="파이썬 힙 스냅샷 비교도 남긴다")
    parser.add_argument("--out", default=None, help="CSV 출력 경로 (기본: work-dir/memory_trace.csv)")
    parser.add_argument("--keep", action="store_true", help="종료 후 임시 폴더를 지우지 않는다")
    args = parser.parse_args(argv)

    exts = tuple(e.strip().lower() for e in args.ext.split(",") if e.strip())
    source = Path(args.source).resolve()
    source_files = _collect_source_files(source, exts)
    print(f"원본 샘플 {len(source_files)}개 발견: {[p.name for p in source_files]}")

    work_dir = Path(tempfile.mkdtemp(prefix="t1058_repro_"))
    docs_dir = work_dir / "docs"
    db_path = work_dir / "index.sqlite3"
    out_csv = Path(args.out) if args.out else work_dir / "memory_trace.csv"

    print(f"작업 폴더: {work_dir}")
    print(f"{args.count}개로 복제 중...")
    _duplicate(source_files, args.count, docs_dir)

    if args.tracemalloc:
        tracemalloc.start(15)
        snap_start = tracemalloc.take_snapshot()

    conn = connect(str(db_path))

    rows: list[Row] = []
    started = time.monotonic()

    def _sample(stage: str, done: int, total: int) -> Row:
        detail = current_process_memory_detail()
        elapsed = time.monotonic() - started
        if detail is None:
            row: Row = (stage, done, total, elapsed, float("nan"), float("nan"), float("nan"), float("nan"))
        else:
            row = (
                stage, done, total, elapsed,
                detail.working_set_mb, detail.peak_working_set_mb,
                detail.private_bytes_mb, detail.peak_private_bytes_mb,
            )
        rows.append(row)
        return row

    def on_progress(done: int, total: int, path: Path) -> None:
        _, _, _, elapsed, working_set, _, private_bytes, _ = _sample("파싱", done, total)
        if done % 25 == 0 or done == total:
            print(f"[파싱 {done}/{total}] {elapsed:6.1f}s  워킹셋 {working_set:8.1f}MB  커밋 {private_bytes:8.1f}MB")

    def on_stage(stage: str, done: int, total: int) -> None:
        # 파싱 단계는 on_progress가 이미 파일 단위로 재고 있다 — 여기서는
        # 임베딩 단계만 추가로 샘플링한다(2026-08-21 사고의 "+8.35GB"가
        # 파싱·임베딩 중 어느 쪽인지가 이 실험의 핵심 질문이다).
        if stage != STAGE_EMBEDDING or total == 0:
            return
        _, _, _, elapsed, working_set, _, private_bytes, _ = _sample("임베딩", done, total)
        if done % 160 == 0 or done == total:
            print(f"[임베딩 {done}/{total}] {elapsed:6.1f}s  워킹셋 {working_set:8.1f}MB  커밋 {private_bytes:8.1f}MB")

    profile = get_profile(args.profile) if args.profile else None
    report = index_folder(
        conn, docs_dir,
        on_progress=on_progress, on_stage=on_stage,
        embed=not args.no_embed, profile=profile,
    )
    conn.close()

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "stage", "done", "total", "elapsed_sec",
            "working_set_mb", "peak_working_set_mb",
            "private_bytes_mb", "peak_private_bytes_mb",
        ])
        writer.writerows(rows)
    print(f"\nCSV 저장: {out_csv}")

    print(f"\n인덱싱 결과: 색인 {report.indexed} · 실패 {len(report.failures)} · 임베딩 {report.embedded}")
    if report.failures:
        for path, reason in report.failures[:5]:
            print(f"  실패: {path.name} — {reason}")

    parsing_rows = [r for r in rows if r[0] == "파싱" and r[6] == r[6]]  # NaN 제외
    embedding_rows = [r for r in rows if r[0] == "임베딩" and r[6] == r[6]]
    _print_trend("파싱 단계", parsing_rows)
    _print_trend("임베딩 단계", embedding_rows)

    if args.tracemalloc:
        snap_end = tracemalloc.take_snapshot()
        diff = snap_end.compare_to(snap_start, "lineno")
        print("\n파이썬 힙 증가 상위 20개 (tracemalloc, 네이티브 확장 메모리는 안 잡힘):")
        for stat in diff[:20]:
            print(f"  {stat}")
        tracemalloc.stop()

    if args.keep:
        print(f"\n--keep 지정됨: {work_dir} 유지")
    else:
        shutil.rmtree(work_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())

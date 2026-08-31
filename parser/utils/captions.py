"""레거시 `.doc`→LibreOffice 변환 시 깨지는 캡션 번호 재계산 (T10.52).

LibreOffice가 워드 캡션 필드(`SEQ`+`STYLEREF \\s`)의 "장이 바뀌면 리셋" 스코프를
지원하지 못해, `[표 4-1]`이어야 할 캡션이 `[표 4-4]`처럼 문서 전체를 관통하는
전역 카운터 값으로 굳어 저장된다(조사 기록: `T10_52_레거시_doc_캡션_번호_오류_조사기록.md`).

이 값을 챕터별로 재계산해 되돌리되(조사 문서의 "옵션 B"), **이미 올바르게
챕터별로 리셋돼 있는 값**(정상 작동한 필드, 또는 배포 시 고정 텍스트로 굳힌
캡션)은 손대지 않는다 — 살아있는데 깨진 전역 카운터는 물리적 등장 순서대로
`1, 2, 3, ...`으로만 증가할 수 있으므로, 라벨(표/그림 등)별로 그 패턴이 아니면
(번호에 빈틈이 있거나 순서가 어긋나면) 안전하게 원본을 그대로 둔다.
"""

from __future__ import annotations

import re

_CAPTION_RE = re.compile(
    r"^\[(?P<label>[^\[\]\d]+?)(?P<sep>\s*)(?P<major>\d+)-(?P<minor>\d+)\]"
)

# "표차례"(자동 생성 목차, `TOC \h \z \c "표"` 필드) 항목은 본문 캡션과 똑같은
# `[표 N-M] 제목` 모양이지만 **탭 문자 + 쪽 번호**가 끝에 붙는다(python-docx가
# 오른쪽 정렬 tab-stop을 리터럴 탭으로 돌려준다) — 실측: `[표 4-1] 배터리 상태
# 표\t8`처럼 캡션 뒤에 탭과 쪽 번호가 온다. 목차는 이미 챕터별로 정상
# 리셋돼 있어(조사 문서 §3.3) 본문 캡션과 같은 라벨로 섞으면 "빈틈 없는 전역
# 1,2,3,..." 판정이 오염된다 — 목차 없이 순수 본문 캡션만 보면 전역
# 카운터로 깨져 있는데, 정상인 목차 항목이 뒤섞이면 두 그룹을 합친 수열이
# 어느 쪽 패턴도 아니게 돼 안전장치가 "손대지 않음"으로 잘못 판단해 버린다
# (실측: 실제 레거시 doc 문서 검증에서 두 그룹을 섞은 채로 돌리자 캡션이
# 하나도 안 고쳐졌다). 관측 대상에서 아예 뺀다.
_TOC_PAGE_SUFFIX_RE = re.compile(r"\t\s*\d+\s*$")


def recompute_legacy_captions(paragraphs: list[tuple[int, bool, str]]) -> dict[int, str]:
    """(문단 순번, 챕터 경계 여부, 원문) 목록에서 캡션 보정값을 계산한다.

    `is_boundary`는 파서가 이미 "새 절이 시작됐다"고 판단하는 것과 **같은 신호**
    (`_is_heading()` 또는 글꼴 크기 폴백)를 그대로 받는다 — 별도 챕터 판별 로직을
    두면 본문 흐름의 `heading` 갱신과 서로 다른 챕터 경계를 볼 위험이 있다.

    반환값은 `{문단 순번: 보정된 전체 텍스트}`이며, 라벨별로 독립 판정한다 —
    안전 조건을 통과 못 한 라벨은 원본 그대로 두고 통과한 라벨만 고친다.
    """
    chapter_idx = 0
    counters: dict[str, int] = {}
    # label -> [(문단 순번, 원문, 원본 minor, 원본 번호, 새 번호), ...]
    observed: dict[str, list[tuple[int, str, int, str, str]]] = {}

    for paragraph_index, is_boundary, text in paragraphs:
        if is_boundary:
            chapter_idx += 1
            counters = {}
        match = _CAPTION_RE.match(text)
        if match is None:
            continue
        if _TOC_PAGE_SUFFIX_RE.search(text):
            continue  # 표차례 항목 — 본문 캡션이 아니다
        label = match.group("label").strip()
        orig_number = f"{match.group('major')}-{match.group('minor')}"
        counters[label] = counters.get(label, 0) + 1
        new_number = f"{chapter_idx}-{counters[label]}"
        observed.setdefault(label, []).append(
            (paragraph_index, text, int(match.group("minor")), orig_number, new_number)
        )

    fixes: dict[int, str] = {}
    for entries in observed.values():
        if len(entries) < 2:
            # 라벨당 한 번만 등장하면 "빈틈 없이 연속"이라는 조건이 항상 저절로
            # 참이 돼(길이 1) 실제로는 아무 근거가 안 된다 — 판단 재료가 없는
            # 채로 고치면 위험하므로 건드리지 않는다.
            continue
        minors = [minor for _, _, minor, _, _ in entries]
        if minors != list(range(1, len(minors) + 1)):
            # 이미 챕터별로 리셋되고 있다 — 깨진 전역 카운터의 특징(빈틈 없는
            # 1,2,3,... 연속)이 아니므로, 정상 작동 중이거나 고정 텍스트로 굳은
            # 값이다. 손대지 않는다.
            continue
        for paragraph_index, text, _minor, orig_number, new_number in entries:
            if new_number == orig_number:
                continue
            fixes[paragraph_index] = _CAPTION_RE.sub(
                lambda m, n=new_number: f"[{m.group('label')}{m.group('sep')}{n}]",
                text,
                count=1,
            )
    return fixes

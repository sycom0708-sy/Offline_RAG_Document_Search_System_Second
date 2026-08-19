"""제목(heading) 판별 공용 로직 (T10.31에서 PDF용으로 만든 것을 T10.32에서 공유화).

서식이 있는 문서는 제목을 **글꼴 크기**에 남긴다. 텍스트 패턴(`1-1.` 같은 번호)으로
찾지 않는 이유는 T10.31에서 실측으로 기각됐기 때문이다 — 문서마다 갈렸고(날짜
`2021. 04`를 제목으로 오인), PDF는 페이지 전체가 줄바꿈 없는 한 덩어리로 추출돼
"짧은 줄"이라는 단서 자체가 없었다.

같은 원리를 세 파서가 쓰지만 문서 구조가 달라 진입점이 둘로 갈린다.

- **페이지 단위**(PDF): 페이지마다 `pick_largest_line()`으로 그 페이지의 제목을 뽑는다.
- **흐름 단위**(HWP·HWPX): 페이지 경계가 없으므로 docx처럼 **지나온 제목을 계속
  들고 간다**. 문서 전체의 본문 크기를 먼저 정하고(`body_size_of()`), 그보다 뚜렷이
  큰 문단을 만날 때마다 현재 제목을 갈아 끼운다(`is_heading_size()`).

docx·pptx는 글꼴 크기를 볼 필요가 없다 — 스타일(Heading/Title)과 제목 플레이스홀더가
문서에 이미 명시돼 있다. 다만 길이 상한(`clean_heading()`)은 네 포맷이 함께 쓴다.
"""

from __future__ import annotations

from collections import Counter

# 제목으로 인정할 최소 글꼴 배율 (T10.31) — 본문 크기의 이 배 이상이어야 제목으로
# 본다. [제안] 실측 기준: AICA 안내서는 20pt/14pt = 1.43배로 뚜렷하고, 글꼴이 균일한
# 문서는 이 문턱에 걸려 제목 없음(빈 문자열)이 된다.
HEADING_SIZE_RATIO = 1.2

# 이보다 길면 제목이 아니라 본문으로 본다 (자르지 않고 **버린다**) — 글꼴이 큰
# 본문에서 문단 전체가 제목으로 올라오는 것을 막는다.
#
# 40자는 실측으로 정했다: 이 코퍼스에서 뽑힌 제목 102개의 길이 중앙값은 12자,
# 정상 제목 중 가장 긴 것이 28자("4.3.1.9 싞호 사용 유무 및 활성화 방법 설정")인
# 반면 유일한 오탐은 54자짜리 본문 문단이었다 — 둘 사이가 넉넉히 벌어져 있다.
MAX_HEADING_CHARS = 40


def clean_heading(text: str) -> str:
    """제목 후보를 다듬는다. 너무 길면 **자르지 않고 버린다**.

    자르면 본문 앞부분이 제목으로 둔갑하므로 절단이 아니라 폐기여야 한다.
    """
    heading = text.strip()
    if not heading or len(heading) > MAX_HEADING_CHARS:
        return ""
    return heading


def pick_largest_line(sized_lines: list[tuple[float, str]]) -> str:
    """(글꼴 크기, 텍스트) 목록에서 **가장 큰 글꼴의 첫 줄**을 제목으로 고른다.

    페이지·슬라이드처럼 범위가 이미 좁혀진 단위에 쓴다. 본문과 크기 차이가
    뚜렷할 때만(`HEADING_SIZE_RATIO`) 제목으로 본다 — 글꼴이 균일한 문서에서
    아무 줄이나 제목으로 올리면 노이즈만 된다.
    """
    lines = [(size, text) for size, text in sized_lines if text.strip()]
    if not lines:
        return ""

    largest = max(size for size, _ in lines)
    body_sizes = [size for size, _ in lines if size < largest]
    if not body_sizes:
        return ""  # 전체가 같은 크기 — 제목을 가릴 근거가 없다

    if largest < max(body_sizes) * HEADING_SIZE_RATIO:
        return ""  # 본문과 충분히 구분되지 않는다

    # 가장 큰 글꼴의 **첫 줄만** 쓴다. 같은 크기의 줄을 전부 이어 붙였더니
    # 목차 페이지에서 항목이 통째로 붙어 나왔다(실측: DTG 문서에서
    # "4.3.5.1 NAK ... 31 5. 참고 ...").
    return clean_heading(next(text for size, text in lines if size == largest))


def body_size_of(sized_lines: list[tuple[float, str]]) -> float:
    """문서의 **본문** 글꼴 크기를 정한다 (흐름 단위 문서용).

    최빈값을 쓰되 **글자 수로 가중**한다. 제목·머리말처럼 짧은 줄이 개수로는
    많아도 분량은 본문이 압도적이라, 단순 개수 최빈값은 제목 크기를 본문으로
    오인할 수 있다.
    """
    weights: Counter[float] = Counter()
    for size, text in sized_lines:
        body = text.strip()
        if body:
            weights[size] += len(body)
    if not weights:
        return 0.0
    return weights.most_common(1)[0][0]


def is_heading_size(size: float, body_size: float) -> bool:
    """이 크기를 제목으로 볼 것인가 (흐름 단위 문서용).

    PDF와 **같은 배율 문턱**을 쓴다. 흐름 단위 문서는 문서 전체의 본문 크기를 먼저
    알고 시작하니 "본문보다 크면 제목"으로 더 느슨하게 잡을 수도 있지만, 그렇게
    바꿔서 실제로 좋아지는 문서를 이 코퍼스에서 찾지 못했다 — 유일한 후보였던
    리눅스마스터 기출문제(hwp)는 절 제목(`1과목 : 리눅스 운영 및 관리`)이 **표 안에**
    들어 있어 애초에 문단 제목 후보가 아니었다(표는 별도 청크로 빠진다, Phase 1).
    근거 없이 느슨하게 하면 오탐만 늘어난다.
    """
    return body_size > 0 and size >= body_size * HEADING_SIZE_RATIO

"""2단계 하이브리드 검색 — FTS5 후보 → 벡터 재순위 (T3.5, T3.6).

TECH 5.1의 설계를 그대로 따른다:

    1단계  키워드 필터(BM25, FTS5)로 후보군을 즉시 좁힌다
    2단계  그 후보 **안에서만** 코사인 유사도를 직접 계산해 재순위한다

2단계 정렬은 유사도만 보지 않는다 — **검색어를 더 많이 포함한 청크가 먼저**
온다(`_rerank` 참고, 2026-08-11). 유사도만으로 세우면 사용자가 입력한 단어를
전부 담은 청크가 한 단어만 담은 청크에 밀리는 일이 실제로 있었다.

ANN(근사 최근접 탐색)을 쓰지 않는 것이 핵심이다. 전체 벡터를 뒤지지 않고 이미
좁혀진 후보만 보므로, 인덱스 구조 없이 내적 한 번이면 끝난다(벡터는 저장할 때
L2 정규화해두었다).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from config.settings import (
    DEFAULT_CANDIDATE_LIMIT,
    SIMILARITY_THRESHOLD,
    ModelProfile,
    get_profile,
)
from indexer.fts5.search import (
    SearchResult,
    query_term_variants,
    search as keyword_search,
)
from indexer.vector.store import fetch_vectors


@dataclass
class HybridResult:
    """키워드 결과에 유사도 정보를 얹은 것."""

    result: SearchResult
    similarity: float | None  # 벡터가 없으면 None
    is_low_relevance: bool
    # 이 청크 본문·캡션에 실제로 들어있는 검색어 수 / 전체 검색어 수.
    # 재순위 정렬 키이자, 화면에서 "왜 이 순서인가"를 설명할 근거다.
    matched_terms: int = 0
    total_terms: int = 0

    @property
    def is_full_match(self) -> bool:
        """검색어를 하나도 빠짐없이 포함하는가."""
        return self.total_terms > 0 and self.matched_terms == self.total_terms

    @property
    def is_filename_only_match(self) -> bool:
        """검색어가 파일명에만 걸리고 본문·캡션·heading엔 하나도 없는 경우 (T10.6).

        `chunks_fts`가 file_name도 함께 색인해 FTS5가 행 단위로 매치하다 보니,
        파일명에만 있는 단어를 검색해도 그 파일의 모든 청크가 결과에 낀다.
        `matched_terms`는 파일명을 세지 않으므로(`count_matched_terms` 참고),
        0이면 정확히 이 상황이다.
        """
        return self.total_terms > 0 and self.matched_terms == 0

    # 자주 쓰는 필드는 그대로 꺼내 쓸 수 있게 열어둔다.
    @property
    def chunk_id(self) -> str:
        return self.result.chunk_id

    @property
    def file_name(self) -> str:
        return self.result.file_name

    @property
    def content(self) -> str:
        return self.result.content

    @property
    def heading(self) -> str:
        """이 청크가 속한 절의 제목 (T10.31). 없으면 빈 문자열."""
        return self.result.heading

    @property
    def type(self):
        return self.result.type

    @property
    def page_or_slide(self) -> int | None:
        return self.result.page_or_slide

    @property
    def bm25_score(self) -> float:
        return self.result.score


def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    embedder=None,
    profile: ModelProfile | None = None,
    case_sensitive: bool = False,
    exact_word: bool = False,
    types: Sequence[str] | None = None,
    limit: int = 20,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[HybridResult]:
    """키워드로 좁힌 뒤 벡터로 재순위한 결과를 반환한다.

    임베딩을 쓸 수 없으면(모델 미설치 등) 키워드 결과를 그대로 돌려준다 —
    검색이 실패하는 것보다 낫다. 이때 `similarity`는 None이다.
    """
    # 1단계는 후보군 축소가 목적이므로(TECH 5.1) 재현율을 우선한다.
    # AND로 묶으면 자연어 질문("계약서 검토 기준이 뭐였지")처럼 문서에 없는
    # 단어가 섞인 질의에서 0건이 되어, 재순위할 후보 자체가 사라진다.
    candidates = keyword_search(
        conn,
        query,
        case_sensitive=case_sensitive,
        exact_word=exact_word,
        types=types,
        limit=candidate_limit,
        require_all=False,
    )
    if not candidates:
        return []

    # FTS5와 같은 변형 규칙으로 검색어를 풀어둔다 — 재순위에서 "실제로 몇 개나
    # 포함하는가"를 셀 때 쓴다.
    term_variants = query_term_variants(query)

    profile = profile or get_profile()
    query_vector = _embed_query(query, embedder, profile)
    if query_vector is None:
        # 임베딩을 못 써도 "검색어를 다 포함한 결과 먼저"는 지킨다.
        ranked = [
            _to_result(r, None, False, term_variants, case_sensitive)
            for r in candidates
        ]
        def _sort_key(h: HybridResult):
            rank, gap = _sequence_match(h.result, term_variants, case_sensitive=case_sensitive)
            return (-h.matched_terms, -rank, gap, -len(h.content))

        ranked.sort(key=_sort_key)  # 안정 정렬 — 동점은 BM25 순서 유지
        return ranked[:limit]

    vectors = fetch_vectors(conn, [r.chunk_id for r in candidates], profile.key)
    scored = _rerank(
        candidates, vectors, query_vector, threshold, term_variants, case_sensitive
    )
    return scored[:limit]


def _embed_query(query: str, embedder, profile: ModelProfile) -> np.ndarray | None:
    try:
        if embedder is None:
            from indexer.vector.embedder import Embedder

            embedder = Embedder(profile)
        return embedder.encode_one(query)
    except Exception:
        # 모델이 없거나 추론에 실패해도 키워드 검색 결과는 살린다.
        return None


def count_matched_terms(
    result: SearchResult,
    term_variants: list[tuple[str, ...]],
    *,
    case_sensitive: bool = False,
) -> int:
    """청크가 실제로 포함한 검색어 수를 센다.

    **파일명은 세지 않는다.** `chunks_fts`는 `file_name`도 색인하므로(Phase 2
    설계, 본문의 2배 가중치) 파일명만 걸린 청크도 결과에 낀다 — 실측: "코치
    윤리규정 준수" 질의에서 파일명까지 세면 상위 10건이 전부 3/3 동점이 되어
    순위가 하나도 안 바뀐다(T10.6이 지적한 것과 같은 뿌리).

    반대로 **표의 `caption`은 센다.** 캡션은 `content`에 들어있지 않은데(실측
    확인) 파일 경로가 아니라 엄연한 문서 내용이라, 빼면 표 청크가 부당하게
    낮게 매겨진다.

    **`heading`(절 제목)도 센다** — T10.32의 표 내부 분할 이후 표 청크가
    자기 자신만의 좁은 절 제목을 갖게 됐는데, 그 표의 본문은 순수 데이터라
    제목 단어를 담고 있지 않은 경우가 많다(실측: "업무개시 수신 응답"으로
    찾는 절의 표 본문엔 STX/OP CODE 같은 필드값뿐이라 그 문구가 전혀 없다).
    heading을 안 세면 정작 그 절의 표가 순위에서 완전히 밀려난다. FTS5
    색인에는 넣지 않으므로(T10.31 유지, 1단계 후보 선정에는 영향 없음)
    T10.6 같은 광범위한 오탐 재발 위험은 없다 — 이미 선정된 후보 안에서
    정렬 순서에만 쓰인다.
    """
    haystack = f"{result.content}\n{result.caption}\n{result.heading}"
    if not case_sensitive:
        haystack = haystack.lower()

    matched = 0
    for forms in term_variants:
        if not case_sensitive:
            forms = tuple(form.lower() for form in forms)
        if any(form in haystack for form in forms):
            matched += 1
    return matched


def _sequence_match(
    result: SearchResult,
    term_variants: list[tuple[str, ...]],
    *,
    case_sensitive: bool = False,
) -> tuple[int, int]:
    """검색어들이 등장 순서대로(원문 순서와 같은 순서로) 나타나는 정도.

    "KAC 준수서약서" 같은 다단어 질의에서, 두 단어가 딱 붙어 있는 결과가
    "준수서약서 ... KAC"처럼 순서가 뒤바뀐 결과보다 먼저 나와야 한다는
    요청으로 추가됐다(2026-08-20). `matched_terms`(포함 개수)만으로는 이
    둘을 구분하지 못한다 — 둘 다 2/2다.

    Returns `(rank, gap)`:
        rank 2, gap 0   : 정확한 구문 일치 ("KAC 준수서약서")
        rank 1, gap N   : 순서만 일치 — 첫 검색어 시작부터 마지막 검색어
                          끝까지의 글자 수(N)가 간격. 작을수록 두 단어가
                          가깝다는 뜻이라 우선한다.
        rank 0, gap 0   : 순서가 없거나(뒤바뀜) 검색어가 하나뿐임

    🔴 **간격을 안 보면 표 청크가 부당하게 이긴다.** 순서만 보고 있었을 때,
    "KAC 교육 준수서약서"(간격 몇 글자)가 수백~천 자짜리 표 안에서 "KAC"와
    "준수서약서"가 우연히 멀리 떨어져 순서만 맞는 경우(예: "⑥ 코치 추천서 |
    KAC이상 … ⑦ 교육준수서약서 | …")와 똑같이 "순서 일치"로 묶여, 그다음
    기준인 본문 길이(T10.11)에서 표가 길다는 이유로 이겼다(실측: "KAC
    준수서약서" 질의에서 실제로 발생). 간격을 순서 일치도와 본문 길이 사이에
    끼워 넣어 두 단어가 실제로 가까운 결과가 먼저 오도록 한다.

    🔴 **검색어 중 문서에 없는 단어가 하나라도 있으면 통째로 판정을 포기하지
    않는다.** "KAC 준수서약서 찾아줘"처럼 사용자가 동사("찾아줘")를 붙여
    입력하는 게 실사용에서 흔한데, 그 단어는 어느 문서에도 없다. 모든
    검색어의 위치를 다 요구했다면 "찾아줘"가 없다는 이유만으로 KAC·준수서약서
    가 실제로 붙어 있는 결과조차 순서 정보를 잃는다(실사용에서 실제로 겪음
    — 붙어 있는 결과가 그대로였다). **실제로 매치된 검색어만 추려 그 안에서만
    순서를 본다** — `count_matched_terms`가 "몇 개나 포함하는가"를 셀 때와
    같은 존재 판정(위치 무관, `haystack`에 한 번이라도 나오는가)을 먼저 하고,
    거기서 살아남은 항목끼리만 순서·간격을 계산한다.
    """
    haystack = f"{result.content}\n{result.caption}\n{result.heading}"
    if not case_sensitive:
        haystack = haystack.lower()

    def _forms(variants: tuple[str, ...]) -> tuple[str, ...]:
        return variants if case_sensitive else tuple(v.lower() for v in variants)

    # 문서에 실제로 없는 검색어(예: "찾아줘")는 순서 판정에서 아예 제외한다 —
    # matched_terms와 같은 "존재하는가"만 보는 기준으로 먼저 거른다.
    present = [v for v in term_variants if any(f in haystack for f in _forms(v))]
    if len(present) < 2:
        return 0, 0

    phrase = " ".join(_forms(v)[0] for v in present)
    if phrase in haystack:
        return 2, 0

    pos = 0
    first_start: int | None = None
    last_end = 0
    for variants in present:
        best_idx: int | None = None
        best_len = 0
        for form in _forms(variants):
            idx = haystack.find(form, pos)
            if idx != -1 and (best_idx is None or idx < best_idx):
                best_idx = idx
                best_len = len(form)
        if best_idx is None:
            # 존재는 하지만 이전에 매치된 위치 이후로는 없다 — 순서가 깨진 것.
            return 0, 0
        if first_start is None:
            first_start = best_idx
        last_end = best_idx + best_len
        pos = last_end
    return 1, last_end - first_start


def _to_result(
    result: SearchResult,
    similarity: float | None,
    is_low_relevance: bool,
    term_variants: list[tuple[str, ...]],
    case_sensitive: bool,
) -> HybridResult:
    return HybridResult(
        result,
        similarity,
        is_low_relevance,
        count_matched_terms(result, term_variants, case_sensitive=case_sensitive),
        len(term_variants),
    )


def _rerank(
    candidates: list[SearchResult],
    vectors: dict[str, np.ndarray],
    query_vector: np.ndarray,
    threshold: float,
    term_variants: list[tuple[str, ...]],
    case_sensitive: bool,
) -> list[HybridResult]:
    """모든 후보를 **하나의** 순서로 재순위한다 — 벡터 유무로 그룹을 나누지 않는다.

    정렬 키는 **(관련성 낮음 여부, 일치 개수 ↓, 순서 일치도 ↓, 순서 간격 ↑,
    본문 길이 ↓, 유사도 ↓)** 순이다 [2026-08-11, 본문 길이는 2026-08-14 추가,
    벡터 없는 청크의 취급은 2026-08-19 수정, 순서 일치도·간격은 2026-08-20
    추가].

    - **순서 일치도**: "KAC 준수서약서"처럼 여러 단어를 검색하면, 두 단어가
      검색어 순서 그대로(붙어 있으면 더 좋고, 중간에 다른 단어가 껴도 순서만
      맞으면) 나타나는 결과를 "준수서약서 ... KAC"처럼 순서가 뒤바뀐 결과보다
      먼저 보여준다(`_sequence_match` 참고). 일치 개수가 같을 때만 갈리는
      기준이라 "일치 개수가 유사도보다 우선한다"는 기존 원칙을 안 건드린다.
    - **순서 간격**: 순서 일치 단계(rank 1)가 동점일 때, 두 단어가 원문에서
      실제로 가까이 있는 결과를 우선한다. 간격만 안 보면 수백~천 자짜리 표
      안에서 두 단어가 우연히 멀리 떨어져 순서만 맞는 결과가, 진짜로 근접해서
      일치하는 결과보다 본문 길이 기준으로 먼저 올라오는 문제가 있었다(실측:
      "KAC 준수서약서" 질의).

    - **일치 개수가 유사도보다 우선한다**: 사용자가 입력한 단어를 다 담은 청크가
      먼저 보여야 한다. 실측으로 `rpm 패키지 삭제 옵션` 질의에서 4개를 전부
      담은 청크가 1개만 담은 청크(유사도가 더 높다)에 밀려 2위였다.
    - **일치 개수가 같으면 본문이 더 자세한(긴) 결과를 먼저 보여준다**: 같은
      개수의 검색어를 담고 있어도 본문이 짧으면 정보량이 적을 가능성이 높다
      — 유사도보다 앞세운다[사용자 확정].
    - **다만 "관련성 낮음"은 그보다 앞선다**: 흐림 처리된 카드가 1위에 오면
      고장처럼 보인다(DESIGN §5.6의 흐림은 "이건 약한 결과"라는 신호다).
      정상 결과를 먼저 보여주고, 흐림 그룹 안에서 다시 같은 규칙을 적용한다.
    - 🔴 **완전 일치(`is_full_match`)는 "관련성 낮음" 판정에서 예외다.**
      검색어를 하나도 빠짐없이 담은 청크가 유사도만으로 흐림 처리되며 순위
      밖으로 밀리는 실사용 사례가 있었다 — T10.32(표 내부 분할)로 표
      청크가 자기 절 제목만 좁게 담게 됐는데, 표 본문 자체는 순수 데이터라
      제목 문구와 의미적으로 안 닮아 유사도가 낮다("업무개시 수신 응답"
      질의에서 그 절의 표가 매치 6/6인데도 유사도 0.36 < 임계값 0.5로
      흐림 처리됨). "일치 개수가 유사도보다 우선한다"는 원칙을 정렬 키
      순서만이 아니라 "관련성 낮음" 판정 자체에도 관철한다.

    벡터가 없다고 결과에서 빼지는 않는다 — 키워드로 걸린 문서를 임베딩 누락
    때문에 잃으면 사용자는 "분명 있는데 안 나온다"를 겪게 된다.

    🔴 **벡터 없는 청크를 "관련성 낮음"으로 단정하지 않는다.** 예전엔 벡터
    유무로 두 그룹을 따로 정렬해 이어 붙였는데("있음" 그룹 전체 → "없음" 그룹
    전체), 이러면 벡터가 없을 뿐 검색어를 전부 담은 청크가 벡터는 있지만
    실제로 흐림 처리된("관련성 낮음") 청크보다도 아래로 밀린다 — 실사용에서
    실제로 겪었다("AICA 취득 절차" 질의에서 3단어를 모두 담은 청크가, 1단어만
    우연히 걸린 흐림 카드보다 아래에 나왔다. 원인은 그 청크가 아직 권장 모드
    벡터를 못 받은 것뿐이었다). 벡터가 없으면 `is_low_relevance=False`로 두고
    (판단할 근거가 없으니 불리하게 취급하지 않는다) 나머지 후보와 **하나의
    정렬**로 섞는다 — 판단이 확실한 "관련성 낮음"만 항상 맨 아래로 간다.
    """
    ranked: list[HybridResult] = []

    for result in candidates:
        matched = count_matched_terms(result, term_variants, case_sensitive=case_sensitive)
        is_full_match = len(term_variants) > 0 and matched == len(term_variants)

        vector = vectors.get(result.chunk_id)
        if vector is None or vector.shape[0] != query_vector.shape[0]:
            # 차원이 다르면 다른 모델로 만든 벡터다 — 비교 자체가 성립하지 않는다.
            ranked.append(HybridResult(result, None, False, matched, len(term_variants)))
            continue

        # 양쪽 다 L2 정규화되어 있으므로 내적이 곧 코사인 유사도다.
        similarity = float(np.dot(query_vector, vector))
        is_low_relevance = similarity < threshold and not is_full_match
        ranked.append(
            HybridResult(result, similarity, is_low_relevance, matched, len(term_variants))
        )

    def _sort_key(h: HybridResult):
        rank, gap = _sequence_match(h.result, term_variants, case_sensitive=case_sensitive)
        return (
            h.is_low_relevance,
            -h.matched_terms,
            -rank,
            gap,
            -len(h.content),
            -(h.similarity if h.similarity is not None else 0.0),
        )

    ranked.sort(key=_sort_key)
    return ranked


def compare_with_keyword_only(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
    **kwargs,
) -> tuple[list[SearchResult], list[HybridResult]]:
    """같은 질의를 키워드 단독 / 하이브리드로 각각 검색해 돌려준다.

    Phase 3 DoD("키워드 단독 대비 상위 관련도가 개선되는지")를 눈으로 확인하기
    위한 것이다.
    """
    keyword_only = keyword_search(conn, query, limit=limit)
    hybrid = hybrid_search(conn, query, limit=limit, **kwargs)
    return keyword_only, hybrid

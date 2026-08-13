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
        """검색어가 파일명에만 걸리고 본문·캡션엔 하나도 없는 경우 (T10.6).

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
        ranked.sort(key=lambda h: -h.matched_terms)  # 안정 정렬 — 동점은 BM25 순서 유지
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
    """
    haystack = f"{result.content}\n{result.caption}"
    if not case_sensitive:
        haystack = haystack.lower()

    matched = 0
    for forms in term_variants:
        if not case_sensitive:
            forms = tuple(form.lower() for form in forms)
        if any(form in haystack for form in forms):
            matched += 1
    return matched


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
    """벡터가 있는 후보는 재순위하고, 없는 후보는 뒤에 붙인다.

    정렬 키는 **(관련성 낮음 여부, 일치 개수 ↓, 유사도 ↓)** 순이다 [2026-08-11].

    - **일치 개수가 유사도보다 우선한다**: 사용자가 입력한 단어를 다 담은 청크가
      먼저 보여야 한다. 실측으로 `rpm 패키지 삭제 옵션` 질의에서 4개를 전부
      담은 청크가 1개만 담은 청크(유사도가 더 높다)에 밀려 2위였다.
    - **다만 "관련성 낮음"은 그보다 앞선다**: 흐림 처리된 카드가 1위에 오면
      고장처럼 보인다(DESIGN §5.6의 흐림은 "이건 약한 결과"라는 신호다).
      정상 결과를 먼저 보여주고, 흐림 그룹 안에서 다시 같은 규칙을 적용한다.

    벡터가 없다고 결과에서 빼지는 않는다 — 키워드로 걸린 문서를 임베딩 누락
    때문에 잃으면 사용자는 "분명 있는데 안 나온다"를 겪게 된다.
    """
    with_vector: list[HybridResult] = []
    without_vector: list[HybridResult] = []

    for result in candidates:
        vector = vectors.get(result.chunk_id)
        if vector is None or vector.shape[0] != query_vector.shape[0]:
            # 차원이 다르면 다른 모델로 만든 벡터다 — 비교 자체가 성립하지 않는다.
            without_vector.append(
                _to_result(result, None, False, term_variants, case_sensitive)
            )
            continue

        # 양쪽 다 L2 정규화되어 있으므로 내적이 곧 코사인 유사도다.
        similarity = float(np.dot(query_vector, vector))
        with_vector.append(
            _to_result(
                result, similarity, similarity < threshold, term_variants, case_sensitive
            )
        )

    with_vector.sort(
        key=lambda h: (h.is_low_relevance, -h.matched_terms, -h.similarity)
    )
    # 벡터 없는 쪽은 유사도가 없으니 일치 개수만으로 정렬한다(동점은 BM25 순서).
    without_vector.sort(key=lambda h: -h.matched_terms)
    return [*with_vector, *without_vector]


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

"""2단계 하이브리드 검색 — FTS5 후보 → 벡터 재순위 (T3.5, T3.6).

TECH 5.1의 설계를 그대로 따른다:

    1단계  키워드 필터(BM25, FTS5)로 후보군을 즉시 좁힌다
    2단계  그 후보 **안에서만** 코사인 유사도를 직접 계산해 재순위한다

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
from indexer.fts5.search import SearchResult, search as keyword_search
from indexer.vector.store import fetch_vectors


@dataclass
class HybridResult:
    """키워드 결과에 유사도 정보를 얹은 것."""

    result: SearchResult
    similarity: float | None  # 벡터가 없으면 None
    is_low_relevance: bool

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

    profile = profile or get_profile()
    query_vector = _embed_query(query, embedder, profile)
    if query_vector is None:
        return [HybridResult(r, None, False) for r in candidates[:limit]]

    vectors = fetch_vectors(conn, [r.chunk_id for r in candidates], profile.key)
    scored = _rerank(candidates, vectors, query_vector, threshold)
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


def _rerank(
    candidates: list[SearchResult],
    vectors: dict[str, np.ndarray],
    query_vector: np.ndarray,
    threshold: float,
) -> list[HybridResult]:
    """벡터가 있는 후보는 유사도로, 없는 후보는 BM25 순서로 정렬한다.

    벡터가 없다고 결과에서 빼지는 않는다 — 키워드로 걸린 문서를 임베딩 누락
    때문에 잃으면 사용자는 "분명 있는데 안 나온다"를 겪게 된다. 대신 유사도로
    정렬된 결과 **뒤에** 붙인다.
    """
    with_vector: list[HybridResult] = []
    without_vector: list[HybridResult] = []

    for rank, result in enumerate(candidates):
        vector = vectors.get(result.chunk_id)
        if vector is None or vector.shape[0] != query_vector.shape[0]:
            # 차원이 다르면 다른 모델로 만든 벡터다 — 비교 자체가 성립하지 않는다.
            without_vector.append(HybridResult(result, None, False))
            continue

        # 양쪽 다 L2 정규화되어 있으므로 내적이 곧 코사인 유사도다.
        similarity = float(np.dot(query_vector, vector))
        with_vector.append(
            HybridResult(result, similarity, similarity < threshold)
        )

    with_vector.sort(key=lambda h: h.similarity, reverse=True)
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

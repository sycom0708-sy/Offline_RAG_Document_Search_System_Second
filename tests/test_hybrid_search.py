"""하이브리드 검색 테스트 (T3.5, T3.6).

Phase 3 DoD("키워드 단독 대비 상위 관련도 개선")를 확인하는 것이 핵심이다.
"""

from __future__ import annotations

import numpy as np
import pytest

from config.settings import SIMILARITY_THRESHOLD
from indexer.fts5.schema import connect
from indexer.fts5.search import build_match_query, search as keyword_search
from indexer.fts5.store import store_document
from indexer.vector.store import embed_missing, store_vectors
from parser.schema import Chunk, ChunkType, ParsedDocument
from search.hybrid_search import hybrid_search

SENTENCES = [
    "계약 담당자는 매월 말일까지 실적을 보고한다",
    "계약서 검토 시 기준이 되는 조항은 손해배상, 계약 해지, 지급 조건 세 가지다",
    "계약 관련 문의는 총무팀으로 접수한다",
    "계약 체결 전 반드시 법무 검토를 거쳐야 한다",
    "주차 계약 갱신은 연 1회 진행한다",
]


def _build_db(conn, count_tokens=None):
    document = ParsedDocument(doc_id="d1", file_path="x", file_name="사규.docx", title="사규")
    document.chunks = [
        Chunk(
            chunk_id=f"c{i}",
            doc_id="d1",
            file_path="x",
            file_name="사규.docx",
            type=ChunkType.TEXT,
            page_or_slide=i + 1,
            content=text,
        )
        for i, text in enumerate(SENTENCES)
    ]
    store_document(conn, document, count_tokens=count_tokens)


@pytest.fixture
def db():
    conn = connect(":memory:")
    _build_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def embedded_db(embedder):
    conn = connect(":memory:")
    _build_db(conn, count_tokens=embedder.count_tokens)
    embed_missing(conn, embedder)
    yield conn
    conn.close()


# --- 쿼리 빌더 (모델 불필요) ------------------------------------------


def test_korean_particle_is_stripped_into_alternative():
    """'계약서를'로 검색해도 문서의 '계약서'가 잡혀야 한다."""
    assert build_match_query(["계약서를"], exact_word=True) == '("계약서를" OR "계약서")'


def test_term_without_suffix_has_no_alternative():
    assert build_match_query(["계약서"], exact_word=True) == '"계약서"'


def test_short_stem_is_not_stripped():
    """'도로'에서 '로'를 떼면 '도'가 되어 오탐이 된다."""
    assert build_match_query(["도로"], exact_word=True) == '"도로"'


def test_require_all_toggles_joiner():
    assert " AND " in build_match_query(["가나", "다라"], exact_word=True, require_all=True)
    assert " OR " in build_match_query(["가나", "다라"], exact_word=True, require_all=False)


def test_natural_language_query_falls_back_to_or(db):
    """AND로 0건이면 OR로 완화해 빈 화면을 피한다 (DESIGN §3.1 placeholder)."""
    assert keyword_search(db, "계약서 검토 기준이 뭐였지", fallback_to_any=False) == []
    assert keyword_search(db, "계약서 검토 기준이 뭐였지") != []


def test_single_term_query_does_not_need_fallback(db):
    assert keyword_search(db, "계약") != []


def test_and_precision_kept_when_it_matches(db):
    """AND로 결과가 나오면 굳이 OR로 넓히지 않는다."""
    results = keyword_search(db, "계약 해지")
    assert [r.chunk_id for r in results] == ["c1"]


# --- 하이브리드 (모델 필요) -------------------------------------------


def test_hybrid_ranks_relevant_chunk_first(embedded_db, embedder):
    """DoD: 자연어 질의에서 정답 청크가 1위로 와야 한다."""
    results = hybrid_search(embedded_db, "계약서 검토 기준이 뭐였지", embedder=embedder)

    assert results
    assert results[0].chunk_id == "c1"
    assert results[0].similarity > SIMILARITY_THRESHOLD


def test_hybrid_beats_keyword_only_on_natural_language(embedded_db, embedder):
    """키워드 단독은 AND 폴백에 기대지만, 하이브리드는 의미로 정렬한다."""
    query = "계약할 때 법무 확인이 필요한가"
    hybrid = hybrid_search(embedded_db, query, embedder=embedder)

    assert hybrid[0].chunk_id == "c3"  # "계약 체결 전 반드시 법무 검토"


def test_results_sorted_by_similarity_descending(embedded_db, embedder):
    results = hybrid_search(embedded_db, "계약 검토", embedder=embedder)
    scored = [r.similarity for r in results if r.similarity is not None]
    assert scored == sorted(scored, reverse=True)


def test_low_relevance_flagged_below_threshold(embedded_db, embedder):
    results = hybrid_search(embedded_db, "계약", embedder=embedder)
    for r in results:
        if r.similarity is not None:
            assert r.is_low_relevance == (r.similarity < SIMILARITY_THRESHOLD)


def test_chunks_without_vectors_are_kept_not_dropped(embedded_db, embedder):
    """키워드로 걸린 결과를 임베딩 누락 때문에 잃으면 안 된다."""
    embedded_db.execute("DELETE FROM chunk_vectors WHERE chunk_id = 'c1'")
    embedded_db.commit()

    results = hybrid_search(embedded_db, "계약", embedder=embedder)
    ids = [r.chunk_id for r in results]

    assert "c1" in ids
    missing = next(r for r in results if r.chunk_id == "c1")
    assert missing.similarity is None
    assert ids[-1] == "c1"  # 유사도 있는 결과 뒤로 밀린다


def test_dimension_mismatch_is_handled_safely(embedded_db, embedder):
    """다른 차원의 벡터가 섞여도 예외 없이 처리돼야 한다."""
    wrong = np.ones((1, 4), dtype=np.float32)
    store_vectors(embedded_db, ["c0"], wrong, embedder.profile.key)

    results = hybrid_search(embedded_db, "계약", embedder=embedder)
    mismatched = next(r for r in results if r.chunk_id == "c0")
    assert mismatched.similarity is None


def test_empty_query_returns_empty(embedded_db, embedder):
    assert hybrid_search(embedded_db, "   ", embedder=embedder) == []


def test_limit_is_respected(embedded_db, embedder):
    assert len(hybrid_search(embedded_db, "계약", embedder=embedder, limit=2)) == 2


def test_type_filter_applies(embedded_db, embedder):
    assert hybrid_search(embedded_db, "계약", embedder=embedder, types=["table"]) == []


def test_falls_back_to_keyword_when_embedding_unavailable(db):
    """모델이 없으면 키워드 결과라도 돌려줘야 한다 — 검색이 죽으면 안 된다."""
    from dataclasses import replace

    from config.settings import LIGHT

    results = hybrid_search(db, "계약", profile=replace(LIGHT, key="없는-모델"))

    assert results
    assert all(r.similarity is None for r in results)

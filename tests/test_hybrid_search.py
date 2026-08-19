"""하이브리드 검색 테스트 (T3.5, T3.6).

Phase 3 DoD("키워드 단독 대비 상위 관련도 개선")를 확인하는 것이 핵심이다.
"""

from __future__ import annotations

import numpy as np
import pytest

from config.settings import SIMILARITY_THRESHOLD
from indexer.fts5.schema import connect
from indexer.fts5.search import (
    SearchResult,
    build_match_query,
    query_term_variants,
    search as keyword_search,
)
from indexer.fts5.store import store_document
from indexer.vector.store import embed_missing, store_vectors
from parser.schema import Chunk, ChunkType, ParsedDocument
from search.hybrid_search import _rerank, count_matched_terms, hybrid_search

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


# --- 재순위 정렬 규칙 (유사도를 직접 지정 — 모델 불필요) -----------------
#
# 실제 임베딩으로는 "전체 일치인데 유사도가 더 낮은" 상황을 의도적으로 만들
# 수 없다(픽스처에서 마침 둘 다 1위가 돼 규칙을 검증하지 못했다). 유사도를
# 직접 지정해 정렬 규칙만 분리 검증한다 — Phase 7에서 쓴 것과 같은 방식.


def _result(chunk_id: str, content: str, caption: str = "") -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        doc_id="d1",
        file_path="x",
        file_name="사규.docx",
        type=ChunkType.TEXT,
        page_or_slide=1,
        content=content,
        caption=caption,
        score=-1.0,
    )


def _unit_vector_with_dot(target: float) -> np.ndarray:
    """질의 벡터 [1, 0]과의 내적이 정확히 `target`이 되는 단위 벡터."""
    return np.array([target, np.sqrt(1.0 - target**2)], dtype=np.float32)


def test_more_matched_terms_beats_higher_similarity():
    """🔴 이번 변경의 핵심 — 유사도가 더 높아도 일치 개수가 적으면 뒤로 간다.

    실측 배경: `rpm 패키지 삭제 옵션` 질의에서 4개를 전부 담은 청크가 1개만
    담은 청크(유사도 0.671 vs 0.582)에 밀려 2위였다.
    """
    query_vector = np.array([1.0, 0.0], dtype=np.float32)
    partial = _result("c_partial", "패키지 이야기만 한다")
    full = _result("c_full", "패키지 삭제 옵션을 설명한다")

    ranked = _rerank(
        [partial, full],  # BM25 순서상 부분 일치가 앞
        {"c_partial": _unit_vector_with_dot(0.9), "c_full": _unit_vector_with_dot(0.6)},
        query_vector,
        SIMILARITY_THRESHOLD,
        query_term_variants("패키지 삭제 옵션"),
        case_sensitive=False,
    )

    assert [h.chunk_id for h in ranked] == ["c_full", "c_partial"]
    assert ranked[0].similarity < ranked[1].similarity  # 유사도는 오히려 낮다


def test_low_relevance_full_match_stays_below_normal_partial_match():
    """전체 일치라도 흐림 처리된 카드가 정상 카드 위로 오면 고장처럼 보인다."""
    query_vector = np.array([1.0, 0.0], dtype=np.float32)
    dim_full = _result("c_dim", "패키지 삭제 옵션을 설명한다")
    normal_partial = _result("c_normal", "패키지 이야기만 한다")

    ranked = _rerank(
        [dim_full, normal_partial],
        {
            "c_dim": _unit_vector_with_dot(0.3),  # 임계값 0.5 미만 → 흐림
            "c_normal": _unit_vector_with_dot(0.8),
        },
        query_vector,
        SIMILARITY_THRESHOLD,
        query_term_variants("패키지 삭제 옵션"),
        case_sensitive=False,
    )

    assert [h.chunk_id for h in ranked] == ["c_normal", "c_dim"]
    assert ranked[1].is_full_match and ranked[1].is_low_relevance


def test_korean_particle_variant_counts_as_a_match():
    """검색어에 조사가 붙어도 FTS5와 같은 규칙으로 어간을 매칭해야 한다."""
    result = _result("c1", "계약서 검토가 필요하다")

    matched = count_matched_terms(result, query_term_variants("계약서를 검토"))

    assert matched == 2


def test_match_counting_respects_case_sensitivity():
    result = _result("c1", "API 문서를 확인한다")

    assert count_matched_terms(result, query_term_variants("api")) == 1
    assert count_matched_terms(result, query_term_variants("api"), case_sensitive=True) == 0


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


def test_results_sorted_by_similarity_within_same_match_count_and_length(embedded_db, embedder):
    """유사도 내림차순은 **같은 일치 개수·같은 본문 길이 안에서만** 성립한다
    (2026-08-11 규칙, 2026-08-14 본문 길이 추가)."""
    results = hybrid_search(embedded_db, "계약 검토", embedder=embedder)

    by_bucket: dict[tuple[bool, int, int], list[float]] = {}
    for r in results:
        if r.similarity is None:
            continue
        by_bucket.setdefault(
            (r.is_low_relevance, r.matched_terms, len(r.content)), []
        ).append(r.similarity)

    for scored in by_bucket.values():
        assert scored == sorted(scored, reverse=True)


def test_longer_content_ranks_first_within_same_match_count():
    """같은 일치 개수라면 본문이 더 긴(자세한) 결과가 유사도보다 앞서야 한다
    (2026-08-14, 사용자 요청)."""
    from search.hybrid_search import HybridResult, _rerank

    short_result = SearchResult(
        chunk_id="short", doc_id="d1", file_path="x", file_name="x.txt",
        type=ChunkType.TEXT, page_or_slide=None, content="계약 검토", caption="", score=-1.0,
    )
    long_result = SearchResult(
        chunk_id="long", doc_id="d1", file_path="x", file_name="x.txt",
        type=ChunkType.TEXT, page_or_slide=None,
        content="계약 검토는 매우 자세한 절차를 거쳐 진행되며 여러 담당자의 확인을 필요로 한다",
        caption="", score=-1.0,
    )
    term_variants = query_term_variants("계약 검토")

    # 둘 다 일치 개수는 동일(2/2)하지만, 짧은 쪽이 유사도가 더 높게 설계한다 —
    # 유사도만으로 정렬했다면 짧은 쪽이 1위여야 한다.
    query_vector = np.array([1.0, 0.0], dtype=np.float32)
    short_vector = np.array([1.0, 0.0], dtype=np.float32)  # 유사도 1.0
    long_vector = np.array([0.9, 0.436], dtype=np.float32)  # 유사도 0.9

    results = _rerank(
        [short_result, long_result],
        {"short": short_vector, "long": long_vector},
        query_vector,
        threshold=0.0,
        term_variants=term_variants,
        case_sensitive=False,
    )

    assert [r.chunk_id for r in results] == ["long", "short"]


def test_full_keyword_match_ranks_first(embedded_db, embedder):
    results = hybrid_search(embedded_db, "손해배상 지급", embedder=embedder)

    assert results[0].chunk_id == "c1"  # 두 단어를 모두 담은 유일한 청크
    assert results[0].is_full_match


def test_match_count_is_ordered_descending_within_normal_group(embedded_db, embedder):
    """3/4가 2/4보다 아래로 묻히지 않아야 한다 — 전체 일치뿐 아니라 개수 순 다단 정렬."""
    results = hybrid_search(embedded_db, "계약서 손해배상 지급 조건", embedder=embedder)
    normal = [r.matched_terms for r in results if r.similarity is not None and not r.is_low_relevance]

    assert normal == sorted(normal, reverse=True)


def test_low_relevance_stays_below_normal_even_when_fully_matched(embedded_db, embedder):
    """전체 일치라도 흐림 처리된 카드가 정상 카드 위로 올라오면 고장처럼 보인다."""
    results = hybrid_search(embedded_db, "계약", embedder=embedder)
    flags = [r.is_low_relevance for r in results if r.similarity is not None]

    assert flags == sorted(flags)  # False(정상)가 전부 앞


def test_file_name_match_does_not_count_toward_match_score(embedded_db, embedder):
    """파일명은 세지 않는다 — 세면 같은 파일 청크가 전부 동점이 돼 순위가 안 바뀐다(T10.6과 같은 뿌리)."""
    # 픽스처의 모든 청크가 file_name="사규.docx"를 공유한다.
    results = hybrid_search(embedded_db, "사규 손해배상", embedder=embedder)

    for r in results:
        # "사규"는 파일명에만 있으므로 본문에 손해배상만 있는 청크는 1/2여야 한다.
        expected = sum(
            term in r.result.content for term in ("사규", "손해배상")
        )
        assert r.matched_terms == expected


def test_table_caption_counts_toward_match_score(embedded_db, embedder):
    """캡션은 content에 안 들어있지만 파일 경로가 아니라 문서 내용이다 — 세야 한다."""
    from search.hybrid_search import count_matched_terms
    from indexer.fts5.search import query_term_variants

    result = next(iter(keyword_search(embedded_db, "계약", limit=1)))
    result.caption = "손해배상 기준표"

    matched = count_matched_terms(result, query_term_variants("손해배상"))

    assert matched == 1


def test_low_relevance_flagged_below_threshold(embedded_db, embedder):
    results = hybrid_search(embedded_db, "계약", embedder=embedder)
    for r in results:
        if r.similarity is not None:
            assert r.is_low_relevance == (r.similarity < SIMILARITY_THRESHOLD)


def test_chunks_without_vectors_are_kept_not_dropped(embedded_db, embedder):
    """키워드로 걸린 결과를 임베딩 누락 때문에 잃으면 안 된다.

    🔴 "항상 맨 뒤로 밀린다"는 예전엔 맞았지만 이젠 아니다(2026-08-19) —
    벡터가 없다는 이유만으로 최하위 취급하면, 검색어를 전부 담은 청크가
    (벡터는 있지만) 흐림 처리된 청크보다도 아래로 밀리는 실사용 버그가 났다
    (`test_missing_vector_does_not_rank_below_a_low_relevance_match` 참고).
    이 테스트는 "관련성 낮음으로 단정하지 않는다"만 확인한다 — 순서 자체는
    다른 후보들과 똑같이 일치 개수·본문 길이로 매겨진다.
    """
    embedded_db.execute("DELETE FROM chunk_vectors WHERE chunk_id = 'c1'")
    embedded_db.commit()

    results = hybrid_search(embedded_db, "계약", embedder=embedder)
    ids = [r.chunk_id for r in results]

    assert "c1" in ids
    missing = next(r for r in results if r.chunk_id == "c1")
    assert missing.similarity is None
    assert missing.is_low_relevance is False  # 판단 근거가 없을 뿐 "낮다"고 단정하지 않는다


def test_missing_vector_does_not_rank_below_a_low_relevance_match():
    """🔴 실사용에서 재현된 버그 — 벡터가 없다는 이유만으로 확실히 관련성
    낮은(흐림 처리된) 결과보다도 아래로 밀리면 안 된다.

    "AICA 취득 절차" 질의에서, 세 단어를 전부 담은 청크가 KURE-v1 벡터를
    아직 못 받아 유사도를 못 재는 사이, 딱 한 단어("절차")만 우연히 걸리고
    유사도도 낮아 흐림 처리된 다른 문서가 그 위에 나왔다 — 벡터 유무로
    그룹을 나눠 이어 붙이던 예전 정렬(있음 그룹 전부 → 없음 그룹 전부)의
    부작용이었다.
    """
    query_vector = np.array([1.0, 0.0], dtype=np.float32)
    full_match_no_vector = _result("c_no_vector", "패키지 삭제 옵션을 설명한다")
    weak_match_low_relevance = _result("c_low", "패키지 이야기만 한다")

    ranked = _rerank(
        [weak_match_low_relevance, full_match_no_vector],  # BM25 순서상 약한 쪽이 앞
        {"c_low": _unit_vector_with_dot(0.3)},  # c_no_vector는 벡터 자체가 없다
        query_vector,
        SIMILARITY_THRESHOLD,
        query_term_variants("패키지 삭제 옵션"),
        case_sensitive=False,
    )

    assert [h.chunk_id for h in ranked] == ["c_no_vector", "c_low"]
    assert ranked[1].is_low_relevance is True


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


class TestIsFilenameOnlyMatch:
    """T10.6: matched_terms==0(본문·캡션에 검색어가 하나도 없음)이 정확히
    "파일명에만 걸린 결과"를 뜻하는지 확인한다."""

    def _result(self) -> SearchResult:
        return SearchResult(
            chunk_id="c1", doc_id="d1", file_path="x", file_name="김성용.docx",
            type=ChunkType.TEXT, page_or_slide=None, content="본문 내용", caption="", score=-1.0,
        )

    def test_zero_matched_with_terms_is_filename_only(self):
        from search.hybrid_search import HybridResult

        result = HybridResult(self._result(), 0.7, False, matched_terms=0, total_terms=1)
        assert result.is_filename_only_match is True

    def test_nonzero_matched_is_not_filename_only(self):
        from search.hybrid_search import HybridResult

        result = HybridResult(self._result(), 0.7, False, matched_terms=1, total_terms=1)
        assert result.is_filename_only_match is False

    def test_no_terms_at_all_is_not_filename_only(self):
        """total_terms==0(빈 질의 등)은 "매치 없음"과 다르다 — 오탐 방지."""
        from search.hybrid_search import HybridResult

        result = HybridResult(self._result(), 0.7, False, matched_terms=0, total_terms=0)
        assert result.is_filename_only_match is False

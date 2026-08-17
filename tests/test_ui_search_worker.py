"""SearchWorker — 실사용에서 드러난 프로파일 배선 버그 회귀 테스트.

🔴 `SearchWorker`가 `hybrid_search()`를 부를 때 `embedder=`만 넘기고
`profile=`은 넘기지 않았다. `hybrid_search`는 `profile`이 없으면 내부적으로
`get_profile()`(기본 경량)로 벡터를 조회하는데, 권장 모드(HEAVY)에서는 이게
`embedder`가 실제로 쓴 프로파일과 어긋난다 — 벡터는 있는데 차원이 안 맞는
것으로 처리돼 모든 결과의 `similarity`가 `None`이 되고, AI 요약 1단계가
"관련 문서를 찾을 수 없습니다"로 전부 막힌다.

이 파일이 생기기 전까지 `SearchWorker`를 단독으로 검증하는 테스트가 하나도
없었다 — `MainWindow` 통합 테스트는 전부 경량(기본값) 프로파일로만 돌아
`profile=` 누락이 우연히 가려졌다.
"""

from __future__ import annotations

from indexer.fts5.schema import connect
from indexer.fts5.store import store_document
from indexer.vector.store import embed_missing
from parser.schema import Chunk, ChunkType, ParsedDocument
from ui.search_worker import SearchWorker


def _build_db(path, count_tokens=None):
    conn = connect(path)
    document = ParsedDocument(doc_id="d1", file_path="x", file_name="사규.docx", title="사규")
    document.chunks = [
        Chunk(
            chunk_id="c1",
            doc_id="d1",
            file_path="x",
            file_name="사규.docx",
            type=ChunkType.TEXT,
            page_or_slide=1,
            content="계약서 검토 시 기준이 되는 조항은 손해배상과 계약 해지 조건이다",
        ),
    ]
    store_document(conn, document, count_tokens=count_tokens)
    conn.close()


def test_heavy_embedder_yields_real_similarity_not_none(tmp_path, heavy_embedder):
    """이게 원래 깨져 있었다 — HEAVY 벡터가 있어도 similarity가 None으로 나왔다."""
    db_path = tmp_path / "index.sqlite3"
    _build_db(db_path, count_tokens=heavy_embedder.count_tokens)

    conn = connect(db_path)
    embed_missing(conn, heavy_embedder)
    conn.close()

    worker = SearchWorker(db_path, "계약서 손해배상", request_id=1, embedder=heavy_embedder)
    results = worker._search()

    assert results
    assert results[0].similarity is not None
    assert results[0].chunk_id == "c1"


def test_light_embedder_still_works(tmp_path, embedder):
    """🔴 회귀 방지 — 경량(기본값) 경로는 계속 정상이어야 한다."""
    db_path = tmp_path / "index.sqlite3"
    _build_db(db_path, count_tokens=embedder.count_tokens)

    conn = connect(db_path)
    embed_missing(conn, embedder)
    conn.close()

    worker = SearchWorker(db_path, "계약서 손해배상", request_id=1, embedder=embedder)
    results = worker._search()

    assert results
    assert results[0].similarity is not None


def test_no_embedder_falls_back_to_keyword_search_without_crashing(tmp_path):
    """워밍업 전이라 embedder=None인 경우 — 크래시 없이 키워드 결과라도 나와야 한다."""
    db_path = tmp_path / "index.sqlite3"
    _build_db(db_path)

    worker = SearchWorker(db_path, "계약서 손해배상", request_id=1, embedder=None)
    results = worker._search()

    assert results
    assert results[0].chunk_id == "c1"


# --- 대명사 후속 질문 폴백 검색 (T10.18) -------------------------------------


class TestFallbackQuery:
    def test_empty_result_retries_with_fallback_query_prepended(self, tmp_path):
        """"그건 얼마야?" 단독으론 0건이지만, 직전 질문("계약서")을 붙이면
        찾아야 한다."""
        db_path = tmp_path / "index.sqlite3"
        _build_db(db_path)

        worker = SearchWorker(
            db_path, "그건 얼마야?", request_id=2, embedder=None, fallback_query="계약서"
        )
        results = worker._search()

        assert results
        assert results[0].chunk_id == "c1"

    def test_non_empty_result_ignores_fallback_query(self, tmp_path):
        """이번 메시지만으로 이미 결과가 있으면 폴백을 시도하지 않는다 —
        무관한 이전 질문이 섞여 결과가 오염되면 안 된다."""
        db_path = tmp_path / "index.sqlite3"
        _build_db(db_path)

        worker = SearchWorker(
            db_path, "계약서 손해배상", request_id=2, embedder=None, fallback_query="전혀 다른 검색어"
        )
        results = worker._search()

        assert results
        assert results[0].chunk_id == "c1"

    def test_no_fallback_query_still_returns_empty(self, tmp_path):
        """폴백이 없으면(예: 챗봇 첫 턴) 기존처럼 0건 그대로 반환돼야 한다."""
        db_path = tmp_path / "index.sqlite3"
        _build_db(db_path)

        worker = SearchWorker(db_path, "전혀관련없는외계어", request_id=1, embedder=None)
        results = worker._search()

        assert results == []

    def test_fallback_that_also_finds_nothing_returns_empty(self, tmp_path):
        db_path = tmp_path / "index.sqlite3"
        _build_db(db_path)

        worker = SearchWorker(
            db_path,
            "전혀관련없는외계어",
            request_id=2,
            embedder=None,
            fallback_query="역시관련없는단어",
        )
        results = worker._search()

        assert results == []

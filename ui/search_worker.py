"""검색 워커 — hybrid_search를 UI 블로킹 없이 실행 (PLAN §4-B ②).

sqlite3 커넥션은 만든 스레드에서만 안전하다(`IndexingThread`와 동일 원칙이라
자기 스레드 안에서 새로 연다). `Embedder`는 앱 시작 시 한 번 만든 인스턴스를
공유한다 — onnxruntime의 `InferenceSession.run()`은 다중 스레드 동시 호출을
지원하므로 안전하다(최초 로딩 651ms를 검색마다 물지 않기 위함, Phase 3 실측).

형식 필터(파일 확장자)는 `hybrid_search`의 `types` 파라미터와 다른 축이다
— 그건 청크 타입(text/table/image)을 거른다. 그래서 후보를 넉넉히 받아
확장자로 후처리 필터링한다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from indexer.fts5.schema import connect
from search.hybrid_search import HybridResult, hybrid_search

DISPLAY_LIMIT = 20
CANDIDATE_LIMIT_WHEN_FILTERING = 100


class SearchWorker(QThread):
    """`request_id`를 함께 실어 보낸다 — 늦게 도착한 결과를 호출자가 버릴 수 있게."""

    succeeded = Signal(int, list)  # (request_id, list[HybridResult])
    failed = Signal(int, str)  # (request_id, 에러 메시지)

    def __init__(
        self,
        db_path,
        query: str,
        request_id: int,
        embedder=None,
        case_sensitive: bool = False,
        exact_word: bool = False,
        extensions: set[str] | None = None,
        fallback_query: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._db_path = db_path
        self._query = query
        self._request_id = request_id
        self._embedder = embedder
        self._case_sensitive = case_sensitive
        self._exact_word = exact_word
        self._extensions = extensions
        # T10.18(챗봇 전용) — "그건 얼마야?" 같은 대명사만 있는 후속 질문은
        # 이번 메시지만으론 검색어가 빈약해 0건이 나오기 쉽다. 0건일 때만
        # 직전 질문 텍스트를 덧붙여 한 번 더 검색한다 — LLM 없이 기존
        # hybrid_search()를 재사용하므로 지연·안전장치 어느 쪽도 안 건드린다.
        # 일반 검색 모드는 이 값을 안 넘기므로(항상 None) 영향이 없다.
        self._fallback_query = fallback_query

    def run(self) -> None:
        try:
            results = self._search()
        except Exception as exc:  # noqa: BLE001 — 워커 스레드 예외를 신호로 안전하게 전달
            self.failed.emit(self._request_id, str(exc))
            return
        self.succeeded.emit(self._request_id, results)

    def _search(self) -> list[HybridResult]:
        conn = connect(self._db_path)
        try:
            results = self._run_query(conn, self._query)
            if not results and self._fallback_query:
                combined = f"{self._fallback_query} {self._query}".strip()
                results = self._run_query(conn, combined)
        finally:
            conn.close()

        if self._extensions:
            results = [
                r for r in results if Path(r.file_name).suffix.lower() in self._extensions
            ][:DISPLAY_LIMIT]

        return results

    def _run_query(self, conn, query: str) -> list[HybridResult]:
        fetch_limit = CANDIDATE_LIMIT_WHEN_FILTERING if self._extensions else DISPLAY_LIMIT
        return hybrid_search(
            conn,
            query,
            embedder=self._embedder,
            # 🔴 반드시 embedder가 실제로 쓴 프로파일과 같이 넘겨야 한다.
            # 안 넘기면 hybrid_search가 내부적으로 get_profile()(기본 LIGHT)로
            # 벡터를 조회해, 권장 모드(HEAVY)에서 만든 벡터를 못 찾는다 —
            # 차원이 안 맞는 것으로 처리돼 모든 결과의 similarity가 None이
            # 되고, AI 요약 1단계가 "관련 문서를 찾을 수 없습니다"로 전부
            # 막힌다(실사용에서 발견, 2026-08-11).
            profile=self._embedder.profile if self._embedder is not None else None,
            case_sensitive=self._case_sensitive,
            exact_word=self._exact_word,
            limit=fetch_limit,
        )

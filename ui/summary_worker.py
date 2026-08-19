"""AI 요약 워커 — sLM 추론을 UI 블로킹 없이 실행 (T7.5).

`ui/search_worker.py`와 같은 구조다: `QThread` + `request_id`로 늦게 도착한
결과를 버린다. 검색(10~30ms)과 달리 요약은 **중앙 18.3초**(채택 모델 실측)라
블로킹하면 앱이 사실상 멈춘 것처럼 보이므로 워커가 더더욱 필요하다.

첫 요청에는 서버 기동(4.7초)이 앞에 붙는다. 그동안 카드가 "준비 중"을 보여줄
수 있도록 `started_loading` 신호를 따로 낸다 — 20초 넘게 아무 표시가 없으면
사용자는 멈춘 줄 안다.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from slm.summarize import Summary, summarize


class SummaryWorker(QThread):
    """검색 결과를 근거로 요약 1건을 생성한다."""

    # (request_id, Summary) — Summary는 dataclass라 list가 아닌 object로 보낸다
    succeeded = Signal(int, object)
    failed = Signal(int, str)
    # (request_id,) 모델을 새로 올려야 해서 시간이 더 걸린다는 사전 통지
    started_loading = Signal(int)

    def __init__(
        self,
        question: str,
        results: list,
        service,
        request_id: int,
        parent=None,
        history: list | None = None,
        db_path=None,
    ) -> None:
        super().__init__(parent)
        self._question = question
        self._results = results
        self._service = service
        self._request_id = request_id
        # T10.17: 같은 챗봇 대화의 이전 턴(HistoryTurn 목록) — 검색 카드 단위
        # 요약(T10.14)은 대화가 아니라서 항상 빈 리스트로 넘어온다.
        self._history = history or []
        # T10.25: 표 발췌에 "이 표가 무엇에 관한 것인지"(바로 앞 문단 제목)를
        # 얹기 위한 인덱스 경로. 없으면 제목 없이 표 구조만 넣는다.
        self._db_path = db_path
        self._cancelled = False

    def cancel(self) -> None:
        """이 턴의 답변 생성을 접는다. 다른 스레드(UI)에서 부른다.

        결과만 버리면 부족하다 — `SlmService`가 요청을 한 줄로 세우기 때문에,
        버려질 추론이 계속 돌면 **다음 질문의 답변이 그만큼 늦어진다**. 그래서
        서버 쪽 생성까지 실제로 끊는다.
        """
        self._cancelled = True
        try:
            self._service.abort_active_request()
        except Exception:  # noqa: BLE001 — 취소는 실패해도 조용히 넘어간다
            pass

    def _open_heading_lookup(self):
        """앞 문단 제목을 찾아주는 (조회 함수, 연결) 쌍을 만든다 (T10.25).

        sqlite 연결은 스레드마다 따로 열어야 하므로 **워커 스레드 안에서**
        만든다(`run()`에서만 부른다). 연결 하나를 발췌 몇 건이 나눠 쓴다.
        인덱스를 못 열어도 요약 자체는 계속돼야 하므로 조용히 포기한다 —
        제목은 어디까지나 보조 정보다.
        """
        if self._db_path is None:
            return None, None
        try:
            from indexer.fts5.schema import connect
            from search.chunk_neighbors import heading_before

            conn = connect(self._db_path)
        except Exception:  # noqa: BLE001
            return None, None

        return (lambda result: heading_before(conn, result.chunk_id)), conn

    def run(self) -> None:
        heading_for, conn = self._open_heading_lookup()
        try:
            # 서버가 아직 안 떠 있으면 이번 요청은 기동 시간을 물게 된다.
            if not self._service.is_running() and not self._cancelled:
                self.started_loading.emit(self._request_id)
            summary: Summary = summarize(
                self._question,
                self._results,
                self._service,
                history=self._history,
                heading_for=heading_for,
            )
        except Exception as exc:  # noqa: BLE001 — 워커 예외를 신호로 안전하게 전달
            # 취소로 인한 예외(LlamaClientAborted)는 실패가 아니다 — 조용히 끝낸다.
            if not self._cancelled:
                self.failed.emit(self._request_id, str(exc))
            return
        finally:
            if conn is not None:
                conn.close()
        if not self._cancelled:
            self.succeeded.emit(self._request_id, summary)

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
    ) -> None:
        super().__init__(parent)
        self._question = question
        self._results = results
        self._service = service
        self._request_id = request_id
        # T10.17: 같은 챗봇 대화의 이전 턴(HistoryTurn 목록) — 검색 카드 단위
        # 요약(T10.14)은 대화가 아니라서 항상 빈 리스트로 넘어온다.
        self._history = history or []

    def run(self) -> None:
        try:
            # 서버가 아직 안 떠 있으면 이번 요청은 기동 시간을 물게 된다.
            if not self._service.is_running():
                self.started_loading.emit(self._request_id)
            summary: Summary = summarize(
                self._question, self._results, self._service, history=self._history
            )
        except Exception as exc:  # noqa: BLE001 — 워커 예외를 신호로 안전하게 전달
            self.failed.emit(self._request_id, str(exc))
            return
        self.succeeded.emit(self._request_id, summary)

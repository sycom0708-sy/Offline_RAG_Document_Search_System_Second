"""결과 카드 — 텍스트 카드 (T4.12~T4.14).

DESIGN §5.1(공통 프레임) / §5.3(텍스트 카드) / §5.6(관련성 낮음) / §8(확정
3·4: 이미지·관련성낮음 카드에도 "원문 열기" 병기)를 반영한다. 표/이미지
카드는 `table_card.py`/`image_card.py` — 헤더 구성은 `card_common.py`를
공유한다(Phase 5).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from search.hybrid_search import HybridResult
from search.office_link import plan_open
from ui.highlight import highlighted_excerpt
from ui.widgets.card_common import (
    NearbySection,
    SummarySection,
    apply_low_relevance_style,
    build_card_header,
    build_heading_label,
    start_open_source_file,
)


class ResultCard(QFrame):
    open_failed = Signal(str)  # 원문 열기 실패 시 사유
    # 카드 단위 AI 요약 요청(T10.14) — (SummarySection, 이 카드의 결과).
    # MainWindow가 SummaryWorker를 만들어 SummarySection에 직결한다.
    summarize_requested = Signal(object, object)
    # "근처 내용 더보기" 요청(T10.21) — (NearbySection, 이 카드의 chunk_id).
    nearby_requested = Signal(object, str)

    def __init__(
        self,
        hybrid_result: HybridResult,
        query: str,
        case_sensitive: bool = False,
        exact_word: bool = False,
        show_summary: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ResultCard")
        self._result = hybrid_result

        header, open_button = build_card_header(hybrid_result)
        self._open_button = open_button
        open_button.clicked.connect(self._open_source)

        body_label = QLabel()
        body_label.setObjectName("ResultCardBody")
        body_label.setWordWrap(True)
        body_label.setTextFormat(Qt.TextFormat.RichText)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_label.setText(
            highlighted_excerpt(hybrid_result.content, query, case_sensitive, exact_word)
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)  # DESIGN §10.5 카드 내부 여백
        layout.setSpacing(8)
        layout.addLayout(header)
        heading_label = build_heading_label(hybrid_result)
        if heading_label is not None:
            layout.addWidget(heading_label)
        layout.addWidget(body_label)

        self.summary_section: SummarySection | None = None
        if show_summary:
            self.summary_section = SummarySection()
            self.summary_section.requested.connect(self._request_summary)
            layout.addWidget(self.summary_section)

        self.nearby_section = NearbySection(hybrid_result.result.chunk_id)
        self.nearby_section.requested.connect(self._request_nearby)
        layout.addWidget(self.nearby_section)

        apply_low_relevance_style(self, hybrid_result)

    def _request_summary(self) -> None:
        self.summarize_requested.emit(self.summary_section, self._result)

    def _request_nearby(self, chunk_id: str) -> None:
        self.nearby_requested.emit(self.nearby_section, chunk_id)

    def _open_source(self) -> None:
        path = Path(self._result.result.file_path)
        if not path.is_file():
            self.open_failed.emit(f"파일을 찾을 수 없습니다: {path}")
            return
        plan = plan_open(self._result)
        self._open_worker = start_open_source_file(
            str(path), plan, self._open_button, self.open_failed.emit
        )

"""표 카드 (T5.2, DESIGN §5.4).

원본 표 구조를 평문으로 뭉개지 않고 `QTableWidget`으로 그대로 렌더링한다.
`ResultList`가 이미 세로 스크롤을 담당하므로, 표 내부에는 스크롤바를
두지 않고 실제 콘텐츠 높이에 맞춰 고정한다 — 스크롤 영역 안에 또 스크롤
영역이 있으면 마우스 휠 동작이 헷갈린다.

그리드 조립·TSV 변환은 `card_common.py`의 공유 함수다 — 챗봇 즉시 발췌
(`chat_panel.py`)도 표 청크가 top-1일 때 같은 함수로 렌더링한다(2026-08-14).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QTableWidget, QVBoxLayout

from search.hybrid_search import HybridResult
from search.office_link import plan_open
from ui.widgets.card_common import (
    NearbySection,
    SummarySection,
    apply_low_relevance_style,
    build_card_header,
    build_table_grid,
    fix_table_grid_height,
    parse_table_data,
    start_open_source_file,
    table_to_tsv,
)


class TableCard(QFrame):
    open_failed = Signal(str)
    summarize_requested = Signal(object, object)  # T10.14, ResultCard와 동일
    nearby_requested = Signal(object, str)  # T10.21, ResultCard와 동일

    def __init__(self, hybrid_result: HybridResult, show_summary: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultCard")
        self._result = hybrid_result
        self._table_data = parse_table_data(hybrid_result.result)

        copy_button = QPushButton("⧉ 표 복사")
        copy_button.setObjectName("ResultCardCopyButton")
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.clicked.connect(self._copy_tsv)

        header, open_button = build_card_header(hybrid_result, extra_buttons=[copy_button])
        self._open_button = open_button
        open_button.clicked.connect(self._open_source)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addLayout(header)

        self._grid: QTableWidget | None = None
        if self._table_data is not None:
            self._grid = build_table_grid(self._table_data)
            layout.addWidget(self._grid)
        else:
            fallback = QLabel("표 데이터를 불러올 수 없습니다.")
            fallback.setObjectName("ResultCardBody")
            layout.addWidget(fallback)
            copy_button.setEnabled(False)

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

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 이벤트 핸들러 네이밍
        super().showEvent(event)
        # 부모 창에 실제로 붙어 화면에 표시되기 전에는 헤더 높이가 확정되지
        # 않는다(DPI 스케일링이 적용된 실제 화면과 연결되기 전이라 폰트
        # 메트릭이 더 작게 잡힘 — 실측 확인, 10px 정도 차이가 났다). 생성
        # 시점 계산은 최선 추정치일 뿐이고, 실제로 보여질 때 다시 맞춘다.
        if self._grid is not None and self._table_data is not None:
            fix_table_grid_height(self._grid, bool(self._table_data.header_row))

    def _copy_tsv(self) -> None:
        if self._table_data is None:
            return
        QGuiApplication.clipboard().setText(table_to_tsv(self._table_data))

    def _open_source(self) -> None:
        path = Path(self._result.result.file_path)
        if not path.is_file():
            self.open_failed.emit(f"파일을 찾을 수 없습니다: {path}")
            return
        plan = plan_open(self._result)
        self._open_worker = start_open_source_file(
            str(path), plan, self._open_button, self.open_failed.emit
        )

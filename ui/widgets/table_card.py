"""표 카드 (T5.2, DESIGN §5.4).

원본 표 구조를 평문으로 뭉개지 않고 `QTableWidget`으로 그대로 렌더링한다.
`ResultList`가 이미 세로 스크롤을 담당하므로, 표 내부에는 스크롤바를
두지 않고 실제 콘텐츠 높이에 맞춰 고정한다 — 스크롤 영역 안에 또 스크롤
영역이 있으면 마우스 휠 동작이 헷갈린다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from parser.schema import TableData
from search.hybrid_search import HybridResult
from search.office_link import plan_open
from ui.widgets.card_common import (
    apply_low_relevance_style,
    build_card_header,
    parse_table_data,
    start_open_source_file,
)


class TableCard(QFrame):
    open_failed = Signal(str)

    def __init__(self, hybrid_result: HybridResult, parent=None) -> None:
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
            self._grid = _build_grid(self._table_data)
            layout.addWidget(self._grid)
        else:
            fallback = QLabel("표 데이터를 불러올 수 없습니다.")
            fallback.setObjectName("ResultCardBody")
            layout.addWidget(fallback)
            copy_button.setEnabled(False)

        apply_low_relevance_style(self, hybrid_result)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 이벤트 핸들러 네이밍
        super().showEvent(event)
        # 부모 창에 실제로 붙어 화면에 표시되기 전에는 헤더 높이가 확정되지
        # 않는다(DPI 스케일링이 적용된 실제 화면과 연결되기 전이라 폰트
        # 메트릭이 더 작게 잡힘 — 실측 확인, 10px 정도 차이가 났다). 생성
        # 시점 계산은 최선 추정치일 뿐이고, 실제로 보여질 때 다시 맞춘다.
        if self._grid is not None and self._table_data is not None:
            _fix_grid_height(self._grid, bool(self._table_data.header_row))

    def _copy_tsv(self) -> None:
        if self._table_data is None:
            return
        QGuiApplication.clipboard().setText(_to_tsv(self._table_data))

    def _open_source(self) -> None:
        path = Path(self._result.result.file_path)
        if not path.is_file():
            self.open_failed.emit(f"파일을 찾을 수 없습니다: {path}")
            return
        plan = plan_open(self._result)
        self._open_worker = start_open_source_file(
            str(path), plan, self._open_button, self.open_failed.emit
        )


def _to_tsv(table: TableData) -> str:
    """DESIGN §5.4 제안 — TSV(탭 구분)로 복사하면 Excel·한글에서 셀 구조가 유지된다."""
    lines = []
    if table.header_row:
        lines.append("\t".join(table.header_row))
    for row in table.rows:
        lines.append("\t".join(row))
    return "\n".join(lines)


def _pad(row: list[str], width: int) -> list[str]:
    return row + [""] * (width - len(row))


def _build_grid(table: TableData) -> QTableWidget:
    n_cols = max(
        (len(r) for r in ([table.header_row] if table.header_row else []) + table.rows),
        default=0,
    )

    grid = QTableWidget(len(table.rows), n_cols)
    grid.setObjectName("TableCardGrid")
    # QSS의 font-size는 위젯이 화면에 붙어 폴리시될 때 적용돼 resizeRowsToContents()
    # 시점엔 아직 반영 전이다 — 기본 폰트 기준으로 행 높이를 계산해두면 실제
    # 렌더링(더 큰 QSS 폰트)에서 마지막 행이 잘린다(실측 확인). 계산 전에 직접 지정한다.
    font = grid.font()
    font.setPixelSize(13)
    grid.setFont(font)
    grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    grid.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    grid.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    grid.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    grid.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    grid.verticalHeader().setVisible(False)

    if table.header_row:
        grid.setHorizontalHeaderLabels(_pad(table.header_row, n_cols))
    else:
        # T5.2 헤더 없는 표 처리 — TableData.from_rows()가 1행짜리 표는
        # header_row를 비워 둔다(데이터 소실 방지). 음영 헤더 없이 데이터만 그린다.
        grid.horizontalHeader().setVisible(False)

    for r, row in enumerate(table.rows):
        for c, cell in enumerate(_pad(row, n_cols)):
            item = QTableWidgetItem(cell)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            grid.setItem(r, c, item)

    grid.resizeColumnsToContents()
    grid.resizeRowsToContents()
    grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    _fix_grid_height(grid, bool(table.header_row))

    return grid


def _fix_grid_height(grid: QTableWidget, has_header: bool) -> None:
    """헤더+행 높이 실측치로 내부 스크롤 없이 표 전체가 보이게 고정한다."""
    grid.resizeRowsToContents()
    height = grid.horizontalHeader().height() if has_header else 0
    height += sum(grid.rowHeight(r) for r in range(grid.rowCount()))
    height += 2 * grid.frameWidth()
    grid.setFixedHeight(height)

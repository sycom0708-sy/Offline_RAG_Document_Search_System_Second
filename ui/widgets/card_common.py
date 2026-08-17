"""텍스트·표·이미지 세 카드 타입이 공유하는 부품 (T5.1, DESIGN §5.1/§5.7).

세 카드는 상속이 아니라 이 모듈의 함수들을 조합해서 만든다 — 헤더 구성은
동일하지만 뒤에 붙는 버튼(표 복사/확대)이 카드마다 달라, `QFrame` 다중상속
보다 빌더 함수 하나를 공유하는 쪽이 단순하다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from parser.schema import ImageData, TableData

# 청크 해석·위치 표기는 sLM 프롬프트(Phase 6)도 같은 규칙으로 써야 해서
# PySide6에 묶이지 않는 `search/chunk_view.py`로 옮겼다. 카드 쪽 호출부가
# 그대로 쓰도록 여기서 재노출한다.
from search.chunk_view import format_location, parse_image_data, parse_table_data
from search.office_link import OfficeComError, OpenPlan, is_office_available, open_and_locate
from ui.open_file_worker import OpenFileWorker
from ui.thumbnail_cache import get_thumbnail_path
from ui.widgets.summary_card import SummaryCard

__all__ = [
    "format_location",
    "parse_image_data",
    "parse_table_data",
    "open_source_file",
    "start_open_source_file",
    "build_card_header",
    "apply_low_relevance_style",
    "SummarySection",
    "build_table_grid",
    "fix_table_grid_height",
    "table_to_tsv",
    "load_image_thumbnail",
    "show_image_zoom_dialog",
]

# DESIGN §5.6 / §11 — 0.5 이하로 내리지 않는다.
LOW_RELEVANCE_OPACITY = 0.5


def open_source_file(file_path: str, plan: OpenPlan | None = None) -> str | None:
    """원문을 연다. 성공 시 None, 실패 시(파일 자체가 없을 때만) 사유 문자열.

    `plan`이 있고(이동할 위치 정보가 하나라도 있고) 확장자가 지원되고 Office가
    설치돼 있으면 COM으로 열어 정확한 위치로 이동을 시도한다(T10.1). COM이
    없거나 실패해도 예외를 삼키고 조용히 OS 기본 프로그램으로 폴백한다 —
    "파일이 열리기는 한다"는 기존 보장을 절대 깨지 않는 순수 점진적 개선이다.
    """
    path = Path(file_path)
    if not path.is_file():
        return f"파일을 찾을 수 없습니다: {path}"

    ext = path.suffix.lower()
    if plan is not None and not plan.is_empty() and is_office_available(ext):
        try:
            open_and_locate(str(path), plan)
            return None
        except OfficeComError:
            pass  # 조용히 폴백 — 아래의 일반 열기로

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    return None


def start_open_source_file(
    file_path: str,
    plan: OpenPlan | None,
    button: QPushButton,
    on_failed: Callable[[str], None],
) -> OpenFileWorker:
    """비동기로 원문을 연다(T10.1) — COM 자동화는 1~3초 걸려 메인 스레드를 막으면 안 된다.

    진행 중엔 버튼을 비활성화해 두 번 눌러 워커 참조가 덮어써지는 걸 막는다
    (Phase 4의 `_active_workers` 도입 계기가 된 GC 크래시와 같은 함정).
    """
    button.setEnabled(False)

    worker = OpenFileWorker(file_path, plan)
    worker.failed.connect(on_failed)
    worker.finished.connect(lambda: button.setEnabled(True))
    worker.start()
    return worker


def build_card_header(
    hybrid_result,
    extra_buttons: Sequence[QPushButton] = (),
) -> tuple[QHBoxLayout, QPushButton]:
    """파일명 · 위치 · (관련성 낮음) · (파일명 매치) · 부가 버튼 · 원문 열기 순으로 헤더를 조립한다.

    `open_button`은 클릭 시그널 연결을 호출부가 하도록 그대로 반환한다 —
    "원문 열기 실패" 처리(사유 emit 등)는 카드마다 어느 시그널에 실어 보낼지
    다르기 때문이다.
    """
    header = QHBoxLayout()
    header.setSpacing(6)

    name_label = QLabel(hybrid_result.file_name)
    name_label.setObjectName("ResultCardFileName")
    name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    header.addWidget(name_label)

    sep = QLabel("·")
    sep.setObjectName("ResultCardSeparator")
    header.addWidget(sep)

    location_label = QLabel(format_location(hybrid_result.result))
    location_label.setObjectName("ResultCardLocation")
    location_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    header.addWidget(location_label)

    header.addStretch()

    if hybrid_result.is_low_relevance:
        relevance_label = QLabel("관련성 낮음")
        relevance_label.setObjectName("ResultCardRelevanceLabel")
        header.addWidget(relevance_label)

    if hybrid_result.is_filename_only_match:
        # T10.6: 검색어가 파일명에만 있고 본문·캡션엔 없는 결과 — "관련성
        # 낮음"과 독립적인 신호라 동시에 뜰 수 있다.
        filename_match_label = QLabel("파일명 매치")
        filename_match_label.setObjectName("ResultCardFileNameMatchLabel")
        header.addWidget(filename_match_label)

    for button in extra_buttons:
        header.addWidget(button)

    open_button = QPushButton("원문 열기 ↗")
    open_button.setObjectName("ResultCardOpenButton")
    open_button.setCursor(Qt.CursorShape.PointingHandCursor)
    header.addWidget(open_button)

    return header, open_button


def apply_low_relevance_style(card: QFrame, hybrid_result) -> None:
    """DESIGN §5.6 — 카드 전체를 흐리게 + QSS `[relevance="low"]` 훅.

    🔴 원래 `ResultCard`(텍스트 카드)에만 있던 로직이다. "관련성 낮음" **라벨**은
    `build_card_header()`가 세 카드 공통으로 붙여주지만, 실제로 흐려 보이게
    하는 이 효과는 표·이미지 카드에 옮겨 붙이는 걸 빠뜨렸다 — 라벨은 있는데
    흐림은 없는 카드가 나온 이유(실사용에서 발견, 2026-08-11). 세 카드
    생성자 마지막에서 반드시 이 함수를 불러야 한다.
    """
    if hybrid_result.is_low_relevance:
        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(LOW_RELEVANCE_OPACITY)
        card.setGraphicsEffect(effect)
        card.setProperty("relevance", "low")
    else:
        card.setProperty("relevance", "normal")


class SummarySection(QWidget):
    """카드 한 장 단위의 AI 요약(T10.14) — "AI 요약 보기" 버튼 + `SummaryCard`.

    챗봇의 `_AnswerBubble`은 그 턴 전체(top-1 발췌 근거)를 대상으로 이
    조합을 딱 하나만 갖지만, 검색 결과 카드는 카드마다 하나씩 갖는다 — 그
    카드 하나의 발췌만 근거로 요약한다("검색 결과 각 항목마다 AI 요약이
    있어야 한다"는 사용자 요청, 2026-08-15).

    `SummaryWorker`의 신호(`started_loading`/`succeeded`/`failed`)를 이
    위젯의 메서드에 직접 연결해서 쓴다(중간에 람다를 두지 않는다) — Qt는
    연결된 슬롯이 속한 QObject가 파괴되면 연결을 자동으로 끊어준다. 새
    검색으로 이 카드가 사라진 뒤 뒤늦게 응답이 와도, 이미 없는 위젯을
    건드리는 크래시로 이어지지 않는다.
    """

    requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._button = QPushButton("AI 요약 보기")
        self._button.setObjectName("ResultCardCopyButton")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.clicked.connect(self.requested.emit)
        button_row.addWidget(self._button)
        layout.addLayout(button_row)

        self._card = SummaryCard()
        self._card.setVisible(False)
        layout.addWidget(self._card)

    def show_generating(self) -> None:
        self._card.setVisible(True)
        self._button.setEnabled(False)
        self._card.show_generating()

    def show_starting(self, _request_id: int = 0) -> None:
        """`SummaryWorker.started_loading`(서버 콜드스타트) 직결용."""
        self._card.setVisible(True)
        self._button.setEnabled(False)
        self._card.show_starting()

    def receive_summary(self, _request_id: int, summary) -> None:
        """`SummaryWorker.succeeded` 직결용."""
        self._card.setVisible(True)
        self._button.setEnabled(True)
        self._card.show_summary(summary)

    def receive_error(self, _request_id: int, message: str) -> None:
        """`SummaryWorker.failed` 직결용."""
        self._card.setVisible(True)
        self._button.setEnabled(True)
        self._card.show_error(message)

    # --- 테스트·검증용 --------------------------------------------------

    def summary_text(self) -> str:
        return self._card.body_text()

    def is_summary_visible(self) -> bool:
        return self._card.isVisibleTo(self)


# --- 표 그리드 (T5.2, 챗봇 즉시 발췌도 재사용 — 2026-08-14) --------------------
#
# 원래 table_card.py에만 있던 로직이다. T10.10이 "공유해야 할 로직이 카드
# 하나에만 있어서 다른 카드가 흐림 처리를 빠뜨린" 것과 같은 함정을 챗봇에서
# 반복하지 않도록, 처음부터 여기로 옮겨 TableCard·챗봇 말풍선이 같은 함수를
# 쓰게 한다.

_TABLE_GRID_FONT_PIXEL_SIZE = 13


def table_to_tsv(table: TableData) -> str:
    """DESIGN §5.4 제안 — TSV(탭 구분)로 복사하면 Excel·한글에서 셀 구조가 유지된다."""
    lines = []
    if table.header_row:
        lines.append("\t".join(table.header_row))
    for row in table.rows:
        lines.append("\t".join(row))
    return "\n".join(lines)


def _pad(row: list[str], width: int) -> list[str]:
    return row + [""] * (width - len(row))


def build_table_grid(table: TableData) -> QTableWidget:
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
    font.setPixelSize(_TABLE_GRID_FONT_PIXEL_SIZE)
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
    fix_table_grid_height(grid, bool(table.header_row))

    return grid


def fix_table_grid_height(grid: QTableWidget, has_header: bool) -> None:
    """헤더+행 높이 실측치로 내부 스크롤 없이 표 전체가 보이게 고정한다.

    🔴 부모 창에 실제로 붙어 화면에 표시되기 전에는 헤더 높이가 확정되지
    않는다(DPI 스케일링이 적용된 실제 화면과 연결되기 전이라 폰트 메트릭이
    더 작게 잡힘 — 실측 확인, 10px 정도 차이가 났다). 생성 시점 계산은 최선
    추정치일 뿐이고, 호출부가 `showEvent()`에서 다시 불러야 한다.
    """
    grid.resizeRowsToContents()
    height = grid.horizontalHeader().height() if has_header else 0
    height += sum(grid.rowHeight(r) for r in range(grid.rowCount()))
    height += 2 * grid.frameWidth()
    grid.setFixedHeight(height)


# --- 이미지 썸네일·확대 (T5.3~T5.5, 챗봇 즉시 발췌도 재사용 — 2026-08-14) -----

IMAGE_THUMBNAIL_DISPLAY_SIZE = 120


def load_image_thumbnail(chunk_id: str, image_data: ImageData | None, display_size: int) -> QPixmap | None:
    """캐시된(또는 새로 만든) 썸네일을 표시용 크기로 스케일해 돌려준다.

    원본을 못 읽으면 None — 호출부가 "미리보기 없음" 상태를 보여준다.
    """
    if image_data is None:
        return None
    source = Path(image_data.image_path)
    cache_path = get_thumbnail_path(chunk_id, source)
    if cache_path is None:
        return None
    pixmap = QPixmap(str(cache_path))
    if pixmap.isNull():
        return None
    return pixmap.scaled(
        display_size,
        display_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def show_image_zoom_dialog(parent: QWidget, image_path: str, title: str) -> str | None:
    """원본 이미지를 화면의 80% 이내로 스케일해 다이얼로그로 보여준다.

    성공 시 None, 실패 시(파일 없음·디코딩 실패) 사유 문자열 —
    `open_source_file()`과 같은 계약이라 호출부가 그대로 `open_failed`에 실어
    보낼 수 있다.
    """
    path = Path(image_path)
    if not path.is_file():
        return f"이미지를 찾을 수 없습니다: {path}"

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return f"이미지를 열 수 없습니다: {path}"

    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableSize()
        max_size = QSize(int(available.width() * 0.8), int(available.height() * 0.8))
        if pixmap.width() > max_size.width() or pixmap.height() > max_size.height():
            pixmap = pixmap.scaled(
                max_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    label = QLabel()
    label.setPixmap(pixmap)
    dialog_layout = QVBoxLayout(dialog)
    dialog_layout.addWidget(label)
    dialog.exec()
    return None

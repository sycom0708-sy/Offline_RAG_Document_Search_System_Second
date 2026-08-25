"""문서 관리 페이지 (DESIGN §14.4).

11-A에서는 사이드바에 있던 `폴더 관리` 버튼을 `폴더 선택`으로 옮겨 온
뼈대뿐이었다. **11-B에서 카드 5장을 채운다** — 상태 배지, 인덱스 작업
(현재 단계·현재 파일·마지막 실행 + 버튼 3개), 통계 7칸, 파일 진단.

이 페이지는 값을 직접 계산하지 않는다. `MainWindow`가 인덱싱 콜백과 DB에서
읽어 넘겨주는 것을 그리기만 한다 — 위젯이 DB를 직접 열면 인덱싱 스레드와
같은 파일을 두 곳에서 만지게 된다.

🔴 **생성 시점에 기하(높이·폭)를 계산하지 않는다.** Phase 4·5·7·7.7·11-A에서
반복해 밟은 "화면 부착 전/후" 함정이다 — 창에 붙기 전 좌표는 실제 값이
아니다. 이 페이지는 레이아웃에 전부 맡기고 스크롤 영역으로 넘침을 받는다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.widgets.status_bar import build_ci_watermark_row, format_relative_time

NO_FOLDER_TEXT = "선택되지 않음"
FOLDER_BUTTON_LABEL = "폴더 선택"
UPDATE_BUTTON_LABEL = "인덱스 업데이트"
CANCEL_BUTTON_LABEL = "취소"
RETRY_BUTTON_LABEL = "재시도"

BADGE_IDLE = "대기"
BADGE_RUNNING = "인덱싱 중"

NEVER_RUN_TEXT = "실행 기록 없음"
NO_FAILURE_TEXT = "실패한 파일이 없습니다."

# 파일 진단 목록에 한 번에 보여줄 최대 줄 수. 전부 나열하면 실패가 많을 때
# 페이지가 끝없이 길어진다 — 나머지는 건수로만 알린다.
_MAX_FAILURE_ROWS = 20

# DESIGN §14.4.1의 통계 7칸. (키, 표시 이름) 순서가 곧 화면 순서다.
STAT_KEYS: tuple[tuple[str, str], ...] = (
    ("total", "총"),
    ("created", "신규"),
    ("updated", "변경"),
    ("pruned", "삭제"),
    ("skipped", "미변경"),
    ("indexed", "성공"),
    ("failed", "실패"),
)


def _card(parent_layout: QVBoxLayout) -> QVBoxLayout:
    """흰 카드 한 장을 만들어 그 안쪽 레이아웃을 돌려준다 (DESIGN §14.4)."""
    card = QFrame()
    card.setObjectName("PageCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)
    parent_layout.addWidget(card)
    return layout


def _eyebrow(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageEyebrow")
    return label


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageCardTitle")
    return label


def _body(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageCardBody")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class _Badge(QLabel):
    """상태 배지. 색은 QSS 동적 프로퍼티 `tone`으로 가른다."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setObjectName("PageBadge")
        self.set_tone("idle")

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        # 11-A의 `_NavButton`과 같다 — 동적 프로퍼티는 다시 폴리시해야 반영된다.
        self.style().unpolish(self)
        self.style().polish(self)


class _StatCell(QFrame):
    """통계 한 칸 (숫자 + 이름)."""

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCell")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        self._value = QLabel("0")
        self._value.setObjectName("StatCellValue")
        layout.addWidget(self._value)

        name = QLabel(label)
        name.setObjectName("StatCellLabel")
        layout.addWidget(name)

    def set_value(self, value: int) -> None:
        self._value.setText(f"{value:,}")

    def value_text(self) -> str:
        """테스트·검증용."""
        return self._value.text()


class DocumentPage(QWidget):
    """문서 관리 페이지."""

    folder_requested = Signal()
    update_requested = Signal()
    cancel_requested = Signal()
    retry_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DocumentPage")

        # 카드가 5장이라 낮은 창에서는 넘친다 — 넘침은 스크롤로 받는다.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        # CI 워터마크 — 검색/대화 페이지의 상태바와 같은 자리(우측 하단).
        # 이 페이지엔 상태바가 없어(위 docstring) 스크롤 영역 바깥에 따로
        # 고정한다 — 카드 안에 넣으면 스크롤에 같이 밀려 올라간다.
        watermark = build_ci_watermark_row()
        if watermark is not None:
            outer.addWidget(watermark)

        content = QWidget()
        content.setObjectName("PageScrollContent")
        scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        self._build_header(root)
        self._build_folder_card(root)
        self._build_job_card(root)
        self._build_overview_card(root)
        self._build_diagnostics_card(root)

        root.addStretch()

        self._failures: list[tuple[Path, str]] = []
        self.set_busy(False)
        self.set_failures([])

    # --- 구성 -------------------------------------------------------

    def _build_header(self, root: QVBoxLayout) -> None:
        header = _card(root)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(_eyebrow("LOCAL LIBRARY"))
        text.addWidget(_title("문서 관리"))
        top.addLayout(text, 1)

        self.header_badge = _Badge(BADGE_IDLE)
        top.addWidget(self.header_badge, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(top)

        header.addWidget(_body("선택한 로컬 폴더만 색인하고 상태를 추적합니다."))

    def _build_folder_card(self, root: QVBoxLayout) -> None:
        folder_card = _card(root)
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(0, 0, 0, 0)
        folder_row.setSpacing(12)

        folder_text = QVBoxLayout()
        folder_text.setContentsMargins(0, 0, 0, 0)
        folder_text.setSpacing(2)
        folder_text.addWidget(_eyebrow("SOURCE FOLDER"))
        folder_text.addWidget(_title("선택 폴더"))
        self._folder_label = _body(NO_FOLDER_TEXT)
        folder_text.addWidget(self._folder_label)
        folder_row.addLayout(folder_text, 1)

        self.folder_button = QPushButton(FOLDER_BUTTON_LABEL)
        self.folder_button.setObjectName("PrimaryButton")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.clicked.connect(self.folder_requested)
        folder_row.addWidget(self.folder_button, 0, Qt.AlignmentFlag.AlignTop)

        folder_card.addLayout(folder_row)

    def _build_job_card(self, root: QVBoxLayout) -> None:
        job = _card(root)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(_eyebrow("INDEX JOB"))
        text.addWidget(_title("인덱스 작업"))
        top.addLayout(text, 1)

        self.job_badge = _Badge(BADGE_IDLE)
        top.addWidget(self.job_badge, 0, Qt.AlignmentFlag.AlignTop)
        job.addLayout(top)

        self._stage_label = _body("현재 단계 —")
        job.addWidget(self._stage_label)

        self._progress = QProgressBar()
        self._progress.setObjectName("IndexingProgressBar")
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        job.addWidget(self._progress)

        # 처리 중인 파일 경로는 길어서 가운데를 생략한다(T10.4와 같은 방식).
        # 폭이 실제로 정해지는 것은 화면에 붙은 뒤라, 원본을 따로 들고 있다가
        # `resizeEvent`에서 다시 줄인다.
        self._current_path = ""
        self._file_label = _body("")
        self._file_label.setObjectName("PageCardHint")
        job.addWidget(self._file_label)

        self._last_run_label = _body(f"마지막 실행 {NEVER_RUN_TEXT}")
        self._last_run_label.setObjectName("PageCardHint")
        job.addWidget(self._last_run_label)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)

        self.update_button = QPushButton(UPDATE_BUTTON_LABEL)
        self.update_button.setObjectName("PrimaryButton")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.clicked.connect(self.update_requested)
        buttons.addWidget(self.update_button)

        self.cancel_button = QPushButton(CANCEL_BUTTON_LABEL)
        self.cancel_button.setObjectName("SidebarFooterButton")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancel_requested)
        buttons.addWidget(self.cancel_button)

        self.retry_button = QPushButton(RETRY_BUTTON_LABEL)
        self.retry_button.setObjectName("SidebarFooterButton")
        self.retry_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_button.setToolTip("실패한 파일만 강제로 다시 파싱합니다.")
        self.retry_button.clicked.connect(self.retry_requested)
        buttons.addWidget(self.retry_button)

        buttons.addStretch()
        job.addLayout(buttons)

    def _build_overview_card(self, root: QVBoxLayout) -> None:
        overview = _card(root)
        overview.addWidget(_eyebrow("INDEX OVERVIEW"))
        overview.addWidget(_title("이번 실행 통계"))

        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(8)
        self._stat_cells: dict[str, _StatCell] = {}
        for index, (key, label) in enumerate(STAT_KEYS):
            cell = _StatCell(label)
            self._stat_cells[key] = cell
            grid.addWidget(cell, index // 4, index % 4)
        overview.addLayout(grid)

    def _build_diagnostics_card(self, root: QVBoxLayout) -> None:
        diagnostics = _card(root)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(_eyebrow("FILE DIAGNOSTICS"))
        text.addWidget(_title("파일 진단"))
        top.addLayout(text, 1)

        self.failure_badge = _Badge("0건")
        top.addWidget(self.failure_badge, 0, Qt.AlignmentFlag.AlignTop)
        diagnostics.addLayout(top)

        self._failure_container = QWidget()
        self._failure_layout = QVBoxLayout(self._failure_container)
        self._failure_layout.setContentsMargins(0, 0, 0, 0)
        self._failure_layout.setSpacing(6)
        diagnostics.addWidget(self._failure_container)

    # --- 갱신 API ---------------------------------------------------

    def set_folder(self, folder: str | None) -> None:
        self._folder_label.setText(folder or NO_FOLDER_TEXT)

    def folder_text(self) -> str:
        """테스트·검증용."""
        return self._folder_label.text()

    def set_busy(self, busy: bool) -> None:
        """인덱싱 스레드 생존 여부를 배지·버튼에 반영한다 (DESIGN §14.4)."""
        self._busy = busy
        text = BADGE_RUNNING if busy else BADGE_IDLE
        for badge in (self.header_badge, self.job_badge):
            badge.setText(text)
            badge.set_tone("busy" if busy else "idle")

        self.update_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        # 실패가 없으면 재시도할 대상 자체가 없다 — 눌러도 아무 일이 없는
        # 버튼을 살려두면 "눌렀는데 반응이 없다"가 된다.
        self.retry_button.setEnabled(not busy and bool(self._failures))
        self._progress.setVisible(busy)
        if busy:
            # 카드 제목이 "이번 실행 통계"다 — 새 실행이 시작됐는데 직전 실행의
            # 숫자가 남아 있으면 진행 중인 작업의 결과처럼 읽힌다.
            self.set_stats({})
            self._progress.setRange(0, 0)  # 첫 진행 콜백 전까지는 불확정 표시
        else:
            self._current_path = ""
            self._file_label.setText("")
            self.set_stage(None)

    def is_busy(self) -> bool:
        return self._busy

    def set_stage(self, stage: str | None, done: int = 0, total: int = 0) -> None:
        """현재 단계(파싱 / 임베딩)를 표시한다.

        임베딩 구간은 파일이 아니라 청크 단위로 돌아 총량이 파일 진행률과
        다르다 — 그래서 여기서 받은 done/total을 그대로 덧붙인다.

        🔴 막대도 이 단계 자신의 진행률로 다시 채운다. 파싱이 끝나면 막대가
        파일 기준 100%에서 멈추는데, 그 상태 그대로 두고 "현재 단계 임베딩"
        문구만 바뀌면 임베딩이 이미 끝난 것처럼 보인다(실사용 보고). 총량을
        아직 모르는 순간(단계 전환 직후)은 불확정(marquee)으로 돌려 "막대가
        멈춘 게 아니라 새 단계가 시작됐다"는 걸 즉시 알린다.
        """
        if stage is None:
            self._stage_label.setText("현재 단계 —")
            return
        text = f"현재 단계 {stage}"
        if total > 0:
            text += f" · {done:,}/{total:,}"
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)
        self._stage_label.setText(text)

    def stage_text(self) -> str:
        """테스트·검증용."""
        return self._stage_label.text()

    def set_progress(self, done: int, total: int, current_path: str = "") -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)  # 총량 미확정 — 불확정(marquee) 표시
        self._current_path = current_path
        self._elide_current_path()

    def set_last_run(self, last_run: datetime | None) -> None:
        if last_run is None:
            self._last_run_label.setText(f"마지막 실행 {NEVER_RUN_TEXT}")
            return
        self._last_run_label.setText(
            f"마지막 실행 {format_relative_time(last_run)} "
            f"({last_run.astimezone().strftime('%Y-%m-%d %H:%M')})"
        )

    def last_run_text(self) -> str:
        """테스트·검증용."""
        return self._last_run_label.text()

    def set_stats(self, stats: dict[str, int]) -> None:
        """통계 7칸을 채운다. 키는 `STAT_KEYS`의 것을 쓴다."""
        for key, cell in self._stat_cells.items():
            cell.set_value(stats.get(key, 0))

    def stat_value(self, key: str) -> str:
        """테스트·검증용."""
        return self._stat_cells[key].value_text()

    def set_failures(self, failures: list[tuple[Path, str]]) -> None:
        self._failures = list(failures)
        self.failure_badge.setText(f"{len(self._failures)}건")
        self.failure_badge.set_tone("error" if self._failures else "idle")
        self.retry_button.setEnabled(not self._busy and bool(self._failures))

        while self._failure_layout.count():
            item = self._failure_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 부모를 끊지 않으면 지운 위젯이 화면에 잔상으로 남는다
                # (Phase 4 `ResultList`에서 실제로 겪었다).
                widget.setParent(None)
                widget.deleteLater()

        if not self._failures:
            self._failure_layout.addWidget(_body(NO_FAILURE_TEXT))
            return

        for path, message in self._failures[:_MAX_FAILURE_ROWS]:
            row = _body(f"{path.name} — {message}")
            row.setObjectName("FailureRow")
            row.setWordWrap(True)
            row.setToolTip(str(path))
            self._failure_layout.addWidget(row)

        remaining = len(self._failures) - _MAX_FAILURE_ROWS
        if remaining > 0:
            self._failure_layout.addWidget(_body(f"외 {remaining:,}건"))

    def failure_rows(self) -> list[str]:
        """테스트·검증용 — 지금 표시 중인 진단 줄 텍스트."""
        rows = []
        for index in range(self._failure_layout.count()):
            widget = self._failure_layout.itemAt(index).widget()
            if widget is not None:
                rows.append(widget.text())
        return rows

    def failure_paths(self) -> list[Path]:
        """재시도 대상 — 지금 목록에 있는 실패 파일 경로."""
        return [path for path, _ in self._failures]

    # --- 기하 -------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        super().resizeEvent(event)
        self._elide_current_path()

    def _elide_current_path(self) -> None:
        """긴 경로를 실제 라벨 폭에 맞춰 가운데 생략한다.

        폭은 화면에 붙은 뒤에야 실제 값이 되므로 원본을 따로 들고 있다가
        매번 다시 줄인다 — 생성 시점 폭으로 한 번만 계산하면 창을 넓혀도
        줄인 채로 굳는다.
        """
        if not self._current_path:
            return
        self._file_label.setToolTip(self._current_path)
        width = self._file_label.width() or self.width()
        metrics = QFontMetrics(self._file_label.font())
        self._file_label.setText(
            metrics.elidedText(self._current_path, Qt.TextElideMode.ElideMiddle, max(80, width))
        )

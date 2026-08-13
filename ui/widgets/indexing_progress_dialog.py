"""인덱싱 진행률 팝업 — 비모달 (T10.4).

**비모달이다.** TECH 4.6("인덱싱은 별도 스레드에서 진행 바와 함께 수행해
메인 UI가 멈추지 않도록 한다")을 지키기 위해 `exec()`가 아니라 `show()`로
띄운다 — 검색·필터 조작은 이 창이 떠 있어도 그대로 된다. 상태바의 기존
진행률 표시(T4.16)는 그대로 두고, 이 팝업은 **취소 버튼이 있는 더 눈에 띄는
보조 표시**로 얹는다.

사용자가 이후 완전 모달로 바꾸고 싶어할 수 있어(요청 시점에 확정하지 않음),
`setWindowModality(Qt.ApplicationModal)`을 걸고 `exec()`로 띄우도록 바꾸는
정도로 전환 가능하게 최소한만 건드리도록 구조를 잡는다.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

# 남은 시간 추정에 쓰는 최근 표본 개수 (T10.7) — [제안], 명세 없음. 전체
# 평균을 쓰면 구버전 포맷(doc/xls/ppt, 순정 파싱보다 약 250배 느림, Phase 1
# 실측)이 뒤에 몰려 있을 때 추정이 크게 틀어진다 — 최근 구간만 보면 그
# 순간의 실제 처리 속도에 더 가깝게 반응한다.
_RATE_WINDOW = 5


def _format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds}초"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}분 {rest}초"


class IndexingProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(
        self,
        parent=None,
        *,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("인덱싱 중")
        self.setMinimumWidth(360)

        self._time_source = time_source
        self._start_time: float | None = None
        # (시각, done) 표본 — 남은 시간을 최근 구간의 처리율로 추정하는 데 쓴다.
        self._samples: deque[tuple[float, int]] = deque(maxlen=_RATE_WINDOW)

        layout = QVBoxLayout(self)

        self._info_label = QLabel("인덱싱 준비 중…")
        self._info_label.setObjectName("IndexingProgressInfo")
        layout.addWidget(self._info_label)

        self._progress = QProgressBar()
        self._progress.setObjectName("IndexingProgressBar")
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)

        self._time_label = QLabel("")
        self._time_label.setObjectName("IndexingProgressTime")
        layout.addWidget(self._time_label)

        # 지금 처리 중인 파일 — 전체 경로는 길어서 가운데를 생략(...)해 줄인다.
        # 전체 경로가 궁금할 때를 위해 툴팁에는 원본을 그대로 남긴다.
        self._file_label = QLabel("")
        self._file_label.setObjectName("IndexingProgressFile")
        layout.addWidget(self._file_label)

        buttons = QHBoxLayout()
        buttons.addStretch()  # 취소 버튼을 우측 하단에 배치
        self.cancel_button = QPushButton("취소")
        self.cancel_button.setObjectName("IndexingProgressCancelButton")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

    def set_progress(self, done: int, total: int, current_path: str = "") -> None:
        self._info_label.setText(f"인덱싱 중… {done:,}/{total:,}")
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)  # 총량 미확정 — 불확정(marquee) 표시

        self._file_label.setToolTip(current_path)
        metrics = QFontMetrics(self._file_label.font())
        elided = metrics.elidedText(current_path, Qt.TextElideMode.ElideMiddle,
                                    self._file_label.width() or self.width() - 24)
        self._file_label.setText(elided)

        now = self._time_source()
        if self._start_time is None:
            self._start_time = now
        self._samples.append((now, done))
        self._update_time_label(now, done, total)

    def _update_time_label(self, now: float, done: int, total: int) -> None:
        elapsed = now - self._start_time
        text = f"경과 {_format_duration(elapsed)}"

        remaining = self._estimate_remaining_seconds(now, done, total)
        if remaining is not None:
            text += f" · 약 {_format_duration(remaining)} 남음"

        self._time_label.setText(text)

    def _estimate_remaining_seconds(self, now: float, done: int, total: int) -> float | None:
        """최근 표본 구간의 처리율로 남은 시간을 추정한다.

        표본이 2개 미만이거나(추정 불가), 총량이 불확정(marquee)이거나, 이미
        끝났으면 추정하지 않는다 — 억지 추정이 오히려 혼란스럽다(T10.7).
        """
        if total <= 0 or done >= total or len(self._samples) < 2:
            return None

        first_time, first_done = self._samples[0]
        last_time, last_done = self._samples[-1]
        elapsed_window = last_time - first_time
        done_window = last_done - first_done
        if elapsed_window <= 0 or done_window <= 0:
            return None

        rate = done_window / elapsed_window  # 초당 처리 파일 수
        remaining_files = total - done
        return remaining_files / rate

    def _on_cancel_clicked(self) -> None:
        # 두 번 눌러도 중복 요청이 안 나가게 즉시 잠그고, 실제로 멈추기까지는
        # 파일 하나 처리 시간만큼 걸릴 수 있어 그 사실을 알린다.
        self.cancel_button.setEnabled(False)
        self._info_label.setText("취소하는 중…")
        self.cancel_requested.emit()

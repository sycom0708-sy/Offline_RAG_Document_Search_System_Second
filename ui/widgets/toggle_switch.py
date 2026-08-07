"""공용 토글 스위치 컴포넌트 (T4.6).

DESIGN §4.2: "AI 요약 보기 / 대·소문자 구분 / 일치되는 단어"가 동일한
컴포넌트를 재사용한다. 모두 기본 OFF.

목업(§4.2 ASCII 다이어그램: `AI 요약 보기      ( ●)  OFF`)은 **라벨이 왼쪽,
스위치가 오른쪽**이다. `QCheckBox`는 기본적으로 인디케이터가 텍스트보다
먼저 오므로(실제 렌더링에서 순서가 뒤바뀜을 확인), `QLabel` + 인디케이터
전용 위젯을 조합해 순서를 맞춘다. 클릭 영역은 행 전체로 넓혀 작은
스위치만 정확히 눌러야 하는 부담을 없앤다.

`_SwitchIndicator`는 iOS풍 알약(pill) + 슬라이딩 손잡이를 직접 그린다 —
QSS의 `::indicator`는 사각 박스 하나만 스타일링할 수 있어, 배경색과
손잡이 위치가 동시에 달라지는 모양은 커스텀 페인팅 없이는 표현할 수 없다.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QWidget

PILL_WIDTH = 40
PILL_HEIGHT = 22
CIRCLE_MARGIN = 3

PILL_COLOR_ON = QColor("#2563EB")
PILL_COLOR_OFF = QColor("#B5B9C0")
PILL_COLOR_DISABLED = QColor("#E5E7EB")
CIRCLE_COLOR = QColor("#FFFFFF")
CIRCLE_COLOR_DISABLED = QColor("#F7F8F9")


class _SwitchIndicator(QCheckBox):
    """텍스트 없이 알약 손잡이만 그리는 내부 인디케이터."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(PILL_WIDTH, PILL_HEIGHT)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 이벤트 핸들러 네이밍
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        if not self.isEnabled():
            pill_color, circle_color = PILL_COLOR_DISABLED, CIRCLE_COLOR_DISABLED
        elif self.isChecked():
            pill_color, circle_color = PILL_COLOR_ON, CIRCLE_COLOR
        else:
            pill_color, circle_color = PILL_COLOR_OFF, CIRCLE_COLOR

        painter.setBrush(pill_color)
        painter.drawRoundedRect(0, 0, PILL_WIDTH, PILL_HEIGHT, PILL_HEIGHT / 2, PILL_HEIGHT / 2)

        diameter = PILL_HEIGHT - 2 * CIRCLE_MARGIN
        circle_x = PILL_WIDTH - diameter - CIRCLE_MARGIN if self.isChecked() else CIRCLE_MARGIN
        painter.setBrush(circle_color)
        painter.drawEllipse(circle_x, CIRCLE_MARGIN, diameter, diameter)

    def sizeHint(self) -> QSize:
        return QSize(PILL_WIDTH, PILL_HEIGHT)


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._label = QLabel(label)
        self._label.setObjectName("ToggleSwitchLabel")

        self._checkbox = _SwitchIndicator()
        self._checkbox.setChecked(False)
        self._checkbox.toggled.connect(self.toggled.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        layout.addStretch()
        layout.addWidget(self._checkbox)

    # --- QCheckBox 위임 (기존 호출부와의 API 호환) --------------------

    def isChecked(self) -> bool:
        return self._checkbox.isChecked()

    def setChecked(self, checked: bool) -> None:
        self._checkbox.setChecked(checked)

    def isEnabled(self) -> bool:  # noqa: D102 - QWidget.isEnabled 오버라이드
        return super().isEnabled()

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self._checkbox.setEnabled(enabled)

    def setToolTip(self, text: str) -> None:  # noqa: D102
        super().setToolTip(text)
        self._checkbox.setToolTip(text)
        self._label.setToolTip(text)

    def toolTip(self) -> str:
        return super().toolTip()

    # --- 행 전체 클릭으로 토글 --------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 이벤트 핸들러 네이밍
        if self._checkbox.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self._checkbox.toggle()
        super().mousePressEvent(event)

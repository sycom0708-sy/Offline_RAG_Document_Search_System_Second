"""공용 토글 스위치 컴포넌트 (T4.6).

DESIGN §4.2: "AI 요약 보기 / 대·소문자 구분 / 일치되는 단어"가 동일한
컴포넌트를 재사용한다. 모두 기본 OFF.

목업(§4.2 ASCII 다이어그램: `AI 요약 보기      ( ●)  OFF`)은 **라벨이 왼쪽,
스위치가 오른쪽**이다. `QCheckBox`는 기본적으로 인디케이터가 텍스트보다
먼저 오므로(실제 렌더링에서 순서가 뒤바뀜을 확인), `QLabel` + 인디케이터
전용 `QCheckBox`를 조합해 순서를 맞춘다. 클릭 영역은 행 전체로 넓혀 작은
스위치만 정확히 눌러야 하는 부담을 없앤다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QWidget


class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._label = QLabel(label)
        self._label.setObjectName("ToggleSwitchLabel")

        self._checkbox = QCheckBox()
        self._checkbox.setProperty("variant", "switch")
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

"""설정 페이지 (Phase 11-A 뼈대, DESIGN §14.5).

11-A에서는 **기존 기능의 이동만** 한다 — 사이드바에 있던 `PC 성능 선택`
콤보와 `모델 관리` 버튼이 여기로 옮겨 왔다. 두 위젯 모두 기존 것을 그대로
재사용하므로 동작(프로파일 전환 시 T10.26 벡터 자동 보완 포함)은 변하지
않는다.

모델 상주 · 유휴 종료 시간 · AI CPU 사용과 런타임 정보 표시는 11-C에서
채운다 — 값 노출 자체는 쉽지만 **적용**이 `SlmService` 재기동을 요구해
(DESIGN §14.5.2) 별도 결정이 필요하기 때문이다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.widgets.performance_combo import PerformanceCombo

MODEL_MANAGER_BUTTON_LABEL = "모델 관리"


def _card(parent_layout: QVBoxLayout) -> QVBoxLayout:
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


class SettingsPage(QWidget):
    """설정 페이지."""

    model_manager_requested = Signal(str)  # 현재 활성 프로파일을 실어 보낸다

    def __init__(self, parent=None, initial_profile: str | None = None) -> None:
        """`initial_profile`을 생성 시점에 넘기는 이유는 사이드바와 같다 —
        기본값으로 만든 뒤 setter로 맞추면 `PerformanceCombo.refresh()`가 두 번
        타면서 테스트 스위트가 불안정해졌다(`performance_combo.py` 참고)."""
        super().__init__(parent)
        self.setObjectName("SettingsPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        header = _card(root)
        header.addWidget(_eyebrow("SETTINGS"))
        header.addWidget(_title("설정"))
        description = QLabel("변경 가능한 옵션과 이 PC의 로컬 실행 정보를 확인합니다.")
        description.setObjectName("PageCardBody")
        header.addWidget(description)

        options = _card(root)
        options.addWidget(_eyebrow("OPTIONS"))
        options.addWidget(_title("옵션"))

        self.performance_combo = PerformanceCombo(initial_key=initial_profile)
        options.addWidget(self.performance_combo)

        self.model_button = QPushButton(MODEL_MANAGER_BUTTON_LABEL)
        self.model_button.setObjectName("SidebarFooterButton")
        self.model_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_button.clicked.connect(
            lambda: self.model_manager_requested.emit(self.performance_combo.current_profile())
        )
        options.addWidget(self.model_button, 0, Qt.AlignmentFlag.AlignLeft)

        pending = _card(root)
        pending.addWidget(_eyebrow("LOCAL AI & RUNTIME"))
        pending.addWidget(_title("정보"))
        placeholder = QLabel("모델 상주·유휴 종료 시간·AI CPU 사용과 런타임 정보는 준비 중입니다.")
        placeholder.setObjectName("PageCardBody")
        pending.addWidget(placeholder)

        root.addStretch()

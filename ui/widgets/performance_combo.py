"""PC 성능 선택 콤보박스 (T4.10~T4.11).

DESIGN §4.3 / PLAN §4-C — **Option A**: 콤보박스는 선택 트리거만 담당하고
실제 전환은 모델 관리 팝업에서만 일어난다. 미설치 옵션을 고르면 팝업이
열리고, 콤보박스 선택은 현재 유효 프로파일로 되돌아간다 — "설정은 권장
모드인데 실제로는 경량으로 검색되는" 어긋난 상태를 만들지 않기 위함이다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.settings import PROFILE_ORDER, PROFILES

SECTION_LABEL = "PC 성능 선택"
MANAGE_BUTTON_LABEL = "모델 관리"


class PerformanceCombo(QWidget):
    profile_activated = Signal(str)  # 설치된 프로파일을 실제로 선택함
    # 미설치 프로파일 선택 -> 모델 관리 열기 요청. "모델 관리" 버튼을 직접
    # 눌렀을 때도 같은 신호를 쓴다 — 둘 다 결국 "이 프로파일에 포커스해서
    # 모델 관리 팝업을 열어달라"는 같은 요청이라 MainWindow 쪽 처리를
    # 하나로 재사용할 수 있다.
    model_manager_requested = Signal(str)

    _TOOLTIP_ROLE = Qt.ItemDataRole.ToolTipRole

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(SECTION_LABEL)
        label.setObjectName("SidebarSectionLabel")
        layout.addWidget(label)

        self._combo = QComboBox()
        self._combo.setObjectName("PerformanceCombo")
        layout.addWidget(self._combo)

        # 콤보 우측 하단 — 미설치 옵션을 굳이 고르지 않아도 설치 현황을
        # 바로 확인·관리할 수 있는 진입점. 기존엔 "미설치 옵션 선택" 한
        # 경로뿐이라 이미 다 설치된 사용자는 모델 관리를 열 방법이 없었다.
        manage_row = QHBoxLayout()
        manage_row.setContentsMargins(0, 0, 0, 0)
        manage_row.addStretch()
        self._manage_button = QPushButton(MANAGE_BUTTON_LABEL)
        self._manage_button.setObjectName("PerformanceComboManageButton")
        self._manage_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._manage_button.clicked.connect(
            lambda: self.model_manager_requested.emit(self._current_key)
        )
        manage_row.addWidget(self._manage_button)
        layout.addLayout(manage_row)

        self._current_key = PROFILE_ORDER[0]
        self.refresh()
        self._combo.activated.connect(self._on_activated)

    def refresh(self) -> None:
        """설치 상태를 다시 읽어 배지를 갱신한다 (모델 관리 새로고침 후 호출).

        DESIGN §4.3 "목업 결함" — 사이드바 폭(220px)에서 `label · 배지`를
        온전히 표시하면 잘린다. 문서가 제안한 두 대안(레이블 축약 / 콤보
        폭 확장) 중 레이블 축약을 택한다 — 사이드바 전체 폭을 넓히면 다른
        블록들과의 비례가 깨진다. 전체 문구는 툴팁으로 남긴다.
        """
        self._combo.blockSignals(True)
        self._combo.clear()
        for key in PROFILE_ORDER:
            profile = PROFILES[key]
            installed = profile.is_installed()
            badge = "설치됨" if installed else "준비 중"
            short_label = profile.label.split(" ", 1)[0] + " 모드"  # "경량 모드 (최소 사양)" -> "경량 모드"
            self._combo.addItem(f"{short_label} · {badge}", userData=key)
            self._combo.setItemData(
                self._combo.count() - 1, f"{profile.label} · {badge}", role=self._TOOLTIP_ROLE
            )
        self._select_key(self._current_key)
        self._combo.blockSignals(False)

    def set_current_profile(self, key: str) -> None:
        """실제 활성 프로파일이 바뀌었을 때 콤보 표시를 맞춘다."""
        self._current_key = key
        self.refresh()

    def current_profile(self) -> str:
        return self._current_key

    def _select_key(self, key: str) -> None:
        index = PROFILE_ORDER.index(key) if key in PROFILE_ORDER else 0
        self._combo.setCurrentIndex(index)
        # 드롭다운을 펼치지 않은 상태에서도 축약 전 전체 문구를 볼 수 있게 한다.
        self._combo.setToolTip(self._combo.itemData(index, role=self._TOOLTIP_ROLE) or "")

    def _on_activated(self, index: int) -> None:
        key = self._combo.itemData(index)
        profile = PROFILES[key]
        if profile.is_installed():
            self._current_key = key
            self.profile_activated.emit(key)
        else:
            self.model_manager_requested.emit(key)
            self._select_key(self._current_key)  # 되돌림

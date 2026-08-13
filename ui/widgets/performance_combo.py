"""PC 성능 선택 콤보박스 (T4.10~T4.11).

DESIGN §4.3 / PLAN §4-C — **Option A**: 콤보박스는 선택 트리거만 담당하고
실제 전환은 모델 관리 팝업에서만 일어난다. 미설치 옵션을 고르면 팝업이
열리고, 콤보박스 선택은 현재 유효 프로파일로 되돌아간다 — "설정은 권장
모드인데 실제로는 경량으로 검색되는" 어긋난 상태를 만들지 않기 위함이다.

Phase 7.7까지는 콤보 우측 하단에 "모델 관리" 링크 버튼을 따로 뒀다(이미
설치된 사용자가 관리 화면을 열 방법이 없어서). 목업에 맞춰 사이드바 하단에
"모델 관리" 버튼을 새로 두면서 그 버튼과 기능이 겹쳐, 이 안의 링크는
제거했다 — `model_manager_requested` 신호는 미설치 프로파일 선택 경로에서
계속 쓰인다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from config.settings import PROFILE_ORDER, PROFILES

SECTION_LABEL = "PC 성능 선택"


class PerformanceCombo(QWidget):
    profile_activated = Signal(str)  # 설치된 프로파일을 실제로 선택함
    # 미설치 프로파일 선택 -> 모델 관리 열기 요청.
    model_manager_requested = Signal(str)

    _TOOLTIP_ROLE = Qt.ItemDataRole.ToolTipRole

    def __init__(self, parent=None, initial_key: str | None = None) -> None:
        """`initial_key`를 넘기면 그 프로파일로 시작한다(저장된
        `state.model_profile` 복원용) — 생략하면 `PROFILE_ORDER[0]`(경량).

        🔴 기본값으로 먼저 만들고 별도 setter로 나중에 맞추는 방식은 시도했다가
        되돌렸다 — `refresh()`(Qt 콤보 재구성)가 매번 두 번 타면서, 수백 개의
        `MainWindow`를 만드는 테스트 스위트 전체를 돌릴 때 실행 중인
        `SearchWorker` 스레드가 15초 안에 안 끝나는 간헐적 타임아웃을 실제로
        냈다(원인 메커니즘은 특정하지 못했으나 재현 2/2, 되돌린 뒤 재현 0/2로
        실측 확인). 생성자에서 처음부터 올바른 키로 **한 번만** `refresh()`한다.
        """
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

        self._current_key = initial_key if initial_key in PROFILE_ORDER else PROFILE_ORDER[0]
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

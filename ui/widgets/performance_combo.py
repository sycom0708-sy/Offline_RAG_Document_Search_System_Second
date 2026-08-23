"""PC 성능 선택 콤보박스 (T4.10~T4.11).

DESIGN §4.3 / PLAN §4-C — **Option A**: 콤보박스는 선택 트리거만 담당하고
실제 전환은 모델 관리 팝업에서만 일어난다.

🔴 **"설치됨" 판정 기준(T10.41 후속, 2026-08-22 [사용자 확정])**: 임베딩
모델과 그 등급의 sLM이 **둘 다** 있어야 한다(`_is_fully_installed()`) —
T10.41에서 PC 성능 선택이 sLM도 함께 고르게 됐으니, "권장 모드"가 실제로
완전히 쓸 수 있으려면 KURE-v1(검색)·Qwen3.5-4B(AI 챗봇) 둘 다 필요하다.
미설치 항목은 **드롭다운에서 아예 고를 수 없게 회색으로 막는다**(Qt
`QStandardItem.setEnabled(False)`) — 이전에는 클릭은 되고 선택만
되돌아갔는데, 이제는 클릭 자체가 막힌다. `model_manager_requested` 신호와
되돌림 로직은 혹시 모를 경로(키보드 조작 등)에 대비해 방어적으로 남겨뒀다.

Phase 7.7까지는 콤보 우측 하단에 "모델 관리" 링크 버튼을 따로 뒀다(이미
설치된 사용자가 관리 화면을 열 방법이 없어서). 목업에 맞춰 사이드바 하단에
"모델 관리" 버튼을 새로 두면서 그 버튼과 기능이 겹쳐, 이 안의 링크는
제거했다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from config.settings import PROFILE_ORDER, PROFILES, get_slm_profile, slm_for_model_profile

SECTION_LABEL = "PC 성능 선택"


def _is_fully_installed(key: str) -> bool:
    """이 모드를 실제로 쓸 수 있는가 — 임베딩 모델과 그 등급의 sLM이 **둘 다**
    있어야 한다[사용자 확정, 2026-08-22].

    경량은 임베딩(ko-sroberta-multitask)이 항상 번들이라 사실상 EXAONE
    설치 여부로만 갈리고, 권장은 임베딩(KURE-v1)·sLM(Qwen3.5-4B) 둘 다
    받아야 한다 — Qwen만 받고 KURE-v1은 안 받았는데 "권장 설치됨"으로
    뜨면 검색은 여전히 경량 임베딩을 쓰는 중이라 표시가 거짓말이 된다.
    """
    embedding_installed = PROFILES[key].is_installed()
    slm_installed = get_slm_profile(slm_for_model_profile(key)).is_installed()
    return embedding_installed and slm_installed


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
            installed = _is_fully_installed(key)
            badge = "설치됨" if installed else "설치 안 됨"
            short_label = profile.label.split(" ", 1)[0] + " 모드"  # "경량 모드 (최소 사양)" -> "경량 모드"
            self._combo.addItem(f"{short_label} · {badge}", userData=key)
            row = self._combo.count() - 1
            self._combo.setItemData(
                row, f"{profile.label} · {badge}", role=self._TOOLTIP_ROLE
            )
            # 설치 안 된 항목은 아예 고를 수 없게 회색으로 막는다
            # [사용자 확정] — QComboBox 기본 모델은 QStandardItemModel이라
            # 항목 단위로 활성/비활성을 걸 수 있다.
            item = self._combo.model().item(row)
            if item is not None:
                item.setEnabled(installed)
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
        if _is_fully_installed(key):
            self._current_key = key
            self.profile_activated.emit(key)
        else:
            # 비활성 항목은 Qt가 클릭 자체를 막아 보통 여기 안 온다 — 그래도
            # 방어적으로 안내 경로는 남겨둔다.
            self.model_manager_requested.emit(key)
            self._select_key(self._current_key)  # 되돌림

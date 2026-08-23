"""검색 옵션 3토글 (T4.7~T4.9, T7.5, T7.6).

DESIGN §4.2 남은 결정 #3은 Phase 4~6 동안 "AI 요약 보기"를 비활성 + "Phase
7에서 지원 예정" 툴팁으로 두기로 했었다(PLAN §4-B ⑤). Phase 7에서 실제
동작으로 바꾸되, 모델이 없으면 켜지지 않는다.

Phase 7.6에서 "AI 요약 보기"(검색마다 자동 1회 요약)를 "AI 챗봇 사용"으로
교체했다 — 라벨·툴팁만 바뀌고 내부 시그널/메서드 이름(`ai_summary_changed`
등)은 그대로 둔다(호출부 변경을 최소화하기 위한 의도적 선택, PLAN Phase
7.6 참고).

**Phase 11에서 토글이 1개로 줄었다** (DESIGN §14.7) — `대/소문자 구분`과
`일치되는 단어`를 화면에서 뺐다. **검색 기능 자체는 그대로다**: `hybrid_search()`
의 `case_sensitive`/`exact_word` 인자도, Phase 2에서 만든 FTS5 접두/완전 토큰
매칭 전환도 살아 있고, 값을 `AppState`에서 읽도록 바꿨을 뿐이다. 기능 회귀는
`tests/test_indexer_search.py`가 UI 없이 계속 검증한다.

🔴 **모델 미설치 시 `setEnabled(False)`로 회색 비활성화하던 것을 걷어냈다**
[사용자 확정, 2026-08-22] — 배포 exe를 처음 설치한 사용자는 sLM을 아직
안 받은 게 당연한데, 토글이 회색으로 죽어 있으면 "고장났다"로 보인다.
지금은 **항상 클릭 가능하게 두고**, 켜려는 시도 자체를 막지 않되 모델이
없으면 켜지는 대신 안내 팝업을 띄우고 꺼진 채로 되돌린다 — "왜 안 켜지는지"
그 자리에서 바로 알려준다.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.widgets.info_dialog import show_info
from ui.widgets.toggle_switch import ToggleSwitch

SECTION_LABEL = "검색 옵션"
AI_SUMMARY_TOOLTIP = "검색 결과를 근거로 AI 챗봇과 대화합니다 (문서에 없으면 답하지 않습니다)"
AI_SUMMARY_UNAVAILABLE_TOOLTIP = (
    "AI 챗봇 모델이 설치되지 않았습니다. 모델 관리에서 다운로드 안내를 확인하세요."
)
AI_SUMMARY_UNAVAILABLE_TITLE = "AI 챗봇 사용 불가"
AI_SUMMARY_UNAVAILABLE_MESSAGE = (
    "AI 챗봇 모델이 설치되어 있지 않아 켤 수 없습니다.\n"
    "설정 → 모델 관리에서 sLM 모델을 다운로드한 뒤 다시 시도하세요."
)


class SearchOptions(QWidget):
    ai_summary_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._available = False  # 실제 설치 여부는 MainWindow가 알려준다

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(SECTION_LABEL)
        label.setObjectName("SidebarSectionLabel")
        layout.addWidget(label)

        self.ai_summary = ToggleSwitch("챗봇 모드")
        self.ai_summary.setToolTip(AI_SUMMARY_UNAVAILABLE_TOOLTIP)
        self.ai_summary.toggled.connect(self._on_toggled)
        layout.addWidget(self.ai_summary)

    def set_ai_summary_available(self, available: bool) -> None:
        """모델 설치 여부를 기록한다. 토글 자체는 계속 클릭 가능하게 둔다.

        쓸 수 없게 되는 순간(모델을 지웠다 등) 켜져 있던 토글은 꺼야 한다 —
        상태만 남으면 "켜져 있는데 아무 요약도 안 나오는" 화면이 된다.
        """
        self._available = available
        self.ai_summary.setToolTip(
            AI_SUMMARY_TOOLTIP if available else AI_SUMMARY_UNAVAILABLE_TOOLTIP
        )
        if not available and self.ai_summary.isChecked():
            self.ai_summary.setChecked(False)

    def _on_toggled(self, checked: bool) -> None:
        if checked and not self._available:
            # 켜려는 시도 자체는 받아주되(비활성화하지 않는다), 실제로 켜지진
            # 않는다 — 되돌리고 왜 안 되는지 그 자리에서 안내한다.
            self.ai_summary.setChecked(False)
            show_info(AI_SUMMARY_UNAVAILABLE_TITLE, AI_SUMMARY_UNAVAILABLE_MESSAGE, self)
            return
        self.ai_summary_changed.emit(checked)

    def set_ai_summary(self, enabled: bool) -> None:
        """저장된 상태를 복원한다. 쓸 수 없으면 무시한다."""
        if enabled and not self._available:
            return
        self.ai_summary.setChecked(enabled)

    def is_ai_summary(self) -> bool:
        return self.ai_summary.isChecked()

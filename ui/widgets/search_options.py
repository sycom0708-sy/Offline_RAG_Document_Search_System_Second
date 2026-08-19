"""검색 옵션 3토글 (T4.7~T4.9, T7.5, T7.6).

DESIGN §4.2 남은 결정 #3은 Phase 4~6 동안 "AI 요약 보기"를 비활성 + "Phase
7에서 지원 예정" 툴팁으로 두기로 했었다(PLAN §4-B ⑤). Phase 7에서 실제
동작으로 바꾸되, **모델이 없으면 계속 비활성**이다 — 켰는데 매번 "모델이
없습니다"만 뜨는 것은 그때 피하려던 "고장처럼 보이는 상황" 그대로다.

Phase 7.6에서 "AI 요약 보기"(검색마다 자동 1회 요약)를 "AI 챗봇 사용"으로
교체했다 — 라벨·툴팁만 바뀌고 내부 시그널/메서드 이름(`ai_summary_changed`
등)은 그대로 둔다(호출부 변경을 최소화하기 위한 의도적 선택, PLAN Phase
7.6 참고).

**Phase 11에서 토글이 1개로 줄었다** (DESIGN §14.7) — `대/소문자 구분`과
`일치되는 단어`를 화면에서 뺐다. **검색 기능 자체는 그대로다**: `hybrid_search()`
의 `case_sensitive`/`exact_word` 인자도, Phase 2에서 만든 FTS5 접두/완전 토큰
매칭 전환도 살아 있고, 값을 `AppState`에서 읽도록 바꿨을 뿐이다. 기능 회귀는
`tests/test_indexer_search.py`가 UI 없이 계속 검증한다.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.widgets.toggle_switch import ToggleSwitch

SECTION_LABEL = "검색 옵션"
AI_SUMMARY_TOOLTIP = "검색 결과를 근거로 AI 챗봇과 대화합니다 (문서에 없으면 답하지 않습니다)"
AI_SUMMARY_UNAVAILABLE_TOOLTIP = (
    "AI 챗봇 모델이 설치되지 않았습니다. 모델 관리에서 다운로드 안내를 확인하세요."
)


class SearchOptions(QWidget):
    ai_summary_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(SECTION_LABEL)
        label.setObjectName("SidebarSectionLabel")
        layout.addWidget(label)

        self.ai_summary = ToggleSwitch("AI 챗봇 사용")
        self.ai_summary.toggled.connect(self.ai_summary_changed.emit)
        layout.addWidget(self.ai_summary)
        self.set_ai_summary_available(False)  # 실제 설치 여부는 MainWindow가 알려준다

    def set_ai_summary_available(self, available: bool) -> None:
        """모델 설치 여부에 따라 토글을 열고 닫는다.

        쓸 수 없게 되는 순간(모델을 지웠다 등) 켜져 있던 토글은 꺼야 한다 —
        상태만 남으면 "켜져 있는데 아무 요약도 안 나오는" 화면이 된다.
        """
        self.ai_summary.setEnabled(available)
        self.ai_summary.setToolTip(
            AI_SUMMARY_TOOLTIP if available else AI_SUMMARY_UNAVAILABLE_TOOLTIP
        )
        if not available and self.ai_summary.isChecked():
            self.ai_summary.setChecked(False)

    def set_ai_summary(self, enabled: bool) -> None:
        """저장된 상태를 복원한다. 쓸 수 없으면 무시한다."""
        if enabled and not self.ai_summary.isEnabled():
            return
        self.ai_summary.setChecked(enabled)

    def is_ai_summary(self) -> bool:
        return self.ai_summary.isChecked()

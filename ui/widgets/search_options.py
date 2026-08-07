"""검색 옵션 3토글 (T4.7~T4.9).

DESIGN §4.2 남은 결정 #3 (PLAN §4-B ⑤에서 확정): "AI 요약 보기"는 비활성
(disabled) + "Phase 7에서 지원 예정" 툴팁으로 둔다. 켰다 꺼도 아무 일이
안 일어나는 고장처럼 보이는 상황을 차단하기 위함이다.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.widgets.toggle_switch import ToggleSwitch

SECTION_LABEL = "검색 옵션"
AI_SUMMARY_TOOLTIP = "AI 요약은 Phase 7에서 지원될 예정입니다"


class SearchOptions(QWidget):
    case_sensitive_changed = Signal(bool)
    exact_word_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(SECTION_LABEL)
        label.setObjectName("SidebarSectionLabel")
        layout.addWidget(label)

        self.ai_summary = ToggleSwitch("AI 요약 보기")
        self.ai_summary.setEnabled(False)
        self.ai_summary.setToolTip(AI_SUMMARY_TOOLTIP)
        layout.addWidget(self.ai_summary)

        self.case_sensitive = ToggleSwitch("대/소문자 구분")
        self.case_sensitive.toggled.connect(self.case_sensitive_changed.emit)
        layout.addWidget(self.case_sensitive)

        self.exact_word = ToggleSwitch("일치되는 단어")
        self.exact_word.toggled.connect(self.exact_word_changed.emit)
        layout.addWidget(self.exact_word)

    def is_case_sensitive(self) -> bool:
        return self.case_sensitive.isChecked()

    def is_exact_word(self) -> bool:
        return self.exact_word.isChecked()

"""설정 페이지 (DESIGN §14.5).

11-A에서는 **기존 기능의 이동만** 했다 — 사이드바에 있던 `PC 성능 선택`
콤보와 `모델 관리` 버튼이 여기로 옮겨 왔다. **11-C에서 나머지를 채운다**:
모델 상주 · 유휴 종료 시간 · AI CPU 사용과 런타임 정보 표시.

🔴 **세 옵션의 적용 방식이 서로 다르다** — DESIGN §14.5.2는 셋 다 `SlmService`
생성자 인자라 재기동이 필요하다고 봤지만, 실제로는 `유휴 종료 시간`(그리고
그것을 끄는 `모델 상주`)은 `touch()`가 요청마다 값을 다시 읽어 **재기동 없이
바로 먹는다.** 기동 인자인 것은 `AI CPU 사용`(`n_threads`)뿐이고, 그것만
[사용자 확정]에 따라 서버를 즉시 내린다. 이 위젯은 값만 신호로 내보내고
실제 반영은 `MainWindow`가 `SlmService`에 넘긴다.

이 페이지는 값을 직접 계산하지 않는다(문서 관리 페이지와 같은 원칙) —
런타임 정보도 `MainWindow`가 조회해 넘겨준다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from config.settings import CHAT_RETENTION_CHOICES, SLM_CPU_MODES, chat_retention_description
from ui.widgets.performance_combo import PerformanceCombo
from ui.widgets.status_bar import build_ci_watermark_row
from ui.widgets.toggle_switch import ToggleSwitch

MODEL_MANAGER_BUTTON_LABEL = "모델 관리"
KEEP_RESIDENT_LABEL = "모델 상주"
KEEP_RESIDENT_DESCRIPTION = (
    "켜면 AI 모델을 메모리에 계속 올려 둡니다. 답변은 빨라지지만 "
    "쓰지 않는 동안에도 메모리를 차지합니다."
)
IDLE_LABEL = "유휴 종료 시간"
IDLE_DESCRIPTION = "이 시간 동안 요청이 없으면 AI 모델을 내려 메모리를 돌려줍니다."
CPU_LABEL = "AI CPU 사용"
CPU_DESCRIPTION = (
    "AI가 쓸 CPU 스레드 수입니다. 바꾸면 떠 있던 모델을 내리고 "
    "다음 질문에서 새 값으로 다시 올립니다."
)
CHAT_RETENTION_LABEL = "챗봇 대화 보관"

# 구버전 문서(doc/xls/ppt) 변환에 LibreOffice Portable을 동봉해 쓴다
# (Phase 3·9, TECH 9.1). MPLv2/LGPLv3+는 수정 없이 그대로 동봉하는 것 자체엔
# 소스 공개 의무를 지우지 않지만, 어떤 오픈소스가 들어있는지 사용자가 확인할
# 수 있게 설정 화면에 고지한다(사용자 요청).
OPEN_SOURCE_NOTICE = (
    "이 제품은 LibreOffice(© LibreOffice contributors, MPLv2/LGPLv3+)를 "
    "구버전 문서(doc/xls/ppt) 변환에 포함하고 있습니다."
)

# 유휴 종료 시간 선택지(초). Phase 7이 실물로 정한 5분이 기본이다.
IDLE_CHOICES: tuple[tuple[int, str], ...] = (
    (60, "1분"),
    (180, "3분"),
    (300, "5분"),
    (600, "10분"),
    (1800, "30분"),
)

RUNTIME_MISSING_TEXT = "설치되지 않음"


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


def _body(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageCardBody")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _option_row(layout: QVBoxLayout, label_text: str, widget: QWidget) -> None:
    """`라벨 ─ 위젯` 한 줄. 라벨은 `ToggleSwitch`(모델 상주)와 같은 톤을 쓴다
    (`ToggleSwitchLabel` 재사용 — `PageCardBody`를 쓰면 14px/#1F2937 대
    13px/#6B7280로 옆 항목과 폰트가 달라진다)."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(12)

    label = QLabel(label_text)
    label.setObjectName("ToggleSwitchLabel")
    row.addWidget(label)
    row.addStretch()

    row.addWidget(widget)
    layout.addLayout(row)


def _description(layout: QVBoxLayout, text: str) -> QLabel:
    """타이틀 아래 상시 노출 설명. 타이틀(14px/#1F2937)보다 작고 연한 톤으로
    `PageCardBody`(13px/#6B7280)를 재사용한다."""
    label = _body(text)
    label.setWordWrap(True)
    layout.addWidget(label)
    return label


class SettingsPage(QWidget):
    """설정 페이지."""

    model_manager_requested = Signal(str)  # 현재 활성 프로파일을 실어 보낸다
    keep_resident_changed = Signal(bool)
    idle_timeout_changed = Signal(int)  # 초
    cpu_mode_changed = Signal(str)  # "auto" | "half" | "max"
    chat_retain_turns_changed = Signal(int)  # 2026-08-21

    def __init__(self, parent=None, initial_profile: str | None = None) -> None:
        """`initial_profile`을 생성 시점에 넘기는 이유는 사이드바와 같다 —
        기본값으로 만든 뒤 setter로 맞추면 `PerformanceCombo.refresh()`가 두 번
        타면서 테스트 스위트가 불안정해졌다(`performance_combo.py` 참고)."""
        super().__init__(parent)
        self.setObjectName("SettingsPage")

        # 워터마크는 카드 영역(24px 여백)이 아니라 페이지 가장자리에 붙어야
        # 문서 관리·상태바와 같은 위치가 된다 — 그래서 여백 없는 `outer`를
        # 따로 두고, 카드들은 스크롤 영역 안에 담는다(문서 관리 페이지와
        # 같은 구조, 2026-08-23 위치 불일치 수정). 스크롤이 없으면 창이
        # 낮을 때 워터마크가 창 아래로 밀려나 아예 안 보이는 것도 이번에
        # 같이 확인했다 — 카드 5개+옵션이 늘어난 지금은 문서 관리 페이지와
        # 마찬가지로 넘침이 생길 수 있다.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("PageScrollContent")
        scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        header = _card(root)
        header.addWidget(_eyebrow("SETTINGS"))
        header.addWidget(_title("설정"))
        header.addWidget(_body("변경 가능한 옵션과 이 PC의 로컬 실행 정보를 확인합니다."))

        self._build_options_card(root, initial_profile)
        self._build_runtime_card(root)
        self._build_open_source_card(root)

        root.addStretch()

        # CI 워터마크 — 검색/대화 페이지의 상태바와 같은 자리(우측 하단).
        watermark = build_ci_watermark_row()
        if watermark is not None:
            outer.addWidget(watermark)

    # --- 구성 -------------------------------------------------------

    def _build_options_card(self, root: QVBoxLayout, initial_profile: str | None) -> None:
        options = _card(root)
        options.addWidget(_eyebrow("OPTIONS"))
        options.addWidget(_title("옵션"))

        self.performance_combo = PerformanceCombo(initial_key=initial_profile)
        options.addWidget(self.performance_combo)

        self.model_button = QPushButton(MODEL_MANAGER_BUTTON_LABEL)
        self.model_button.setObjectName("PrimaryButton")
        self.model_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_button.clicked.connect(
            lambda: self.model_manager_requested.emit(self.performance_combo.current_profile())
        )
        options.addWidget(self.model_button, 0, Qt.AlignmentFlag.AlignRight)

        self.keep_resident = ToggleSwitch(KEEP_RESIDENT_LABEL)
        self.keep_resident.toggled.connect(self._on_keep_resident_toggled)
        options.addWidget(self.keep_resident)
        _description(options, KEEP_RESIDENT_DESCRIPTION)

        self.idle_combo = QComboBox()
        for seconds, label in IDLE_CHOICES:
            self.idle_combo.addItem(label, seconds)
        self.idle_combo.currentIndexChanged.connect(
            lambda: self.idle_timeout_changed.emit(self.current_idle_timeout())
        )
        _option_row(options, IDLE_LABEL, self.idle_combo)
        _description(options, IDLE_DESCRIPTION)

        self.cpu_combo = QComboBox()
        for key, label in SLM_CPU_MODES:
            self.cpu_combo.addItem(label, key)
        self.cpu_combo.currentIndexChanged.connect(
            lambda: self.cpu_mode_changed.emit(self.current_cpu_mode())
        )
        _option_row(options, CPU_LABEL, self.cpu_combo)
        _description(options, CPU_DESCRIPTION)

        self.chat_retention_combo = QComboBox()
        for turns, label in CHAT_RETENTION_CHOICES:
            self.chat_retention_combo.addItem(label, turns)
        self.chat_retention_combo.currentIndexChanged.connect(self._on_chat_retention_changed)
        _option_row(options, CHAT_RETENTION_LABEL, self.chat_retention_combo)
        # 고정 문구가 아니라 선택마다 바뀐다(2026-08-21, 사용자 요청) —
        # 아래 set_chat_retain_turns()/_on_chat_retention_changed()가 갱신한다.
        self._chat_retention_description = _description(
            options, chat_retention_description(self.current_chat_retain_turns())
        )

    def _build_runtime_card(self, root: QVBoxLayout) -> None:
        runtime = _card(root)
        runtime.addWidget(_eyebrow("LOCAL AI & RUNTIME"))
        runtime.addWidget(_title("실행 정보"))
        runtime.addWidget(
            _body("이 PC에서 실제로 쓰이는 경로입니다. 표시 전용이며 여기서 바꾸지 않습니다.")
        )

        grid = QGridLayout()
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        self._runtime_values: dict[str, QLabel] = {}
        rows = (
            ("embedding", "임베딩 모델"),
            ("llm", "로컬 LLM 모델"),
            ("llama", "llama.cpp 런타임"),
            ("data", "런타임 데이터"),
        )
        for index, (key, label_text) in enumerate(rows):
            name = _body(label_text)
            name.setObjectName("PageCardHint")
            grid.addWidget(name, index, 0, Qt.AlignmentFlag.AlignTop)

            value = _body("—")
            value.setWordWrap(True)
            self._runtime_values[key] = value
            grid.addWidget(value, index, 1)

        grid.setColumnStretch(1, 1)
        runtime.addLayout(grid)

    def _build_open_source_card(self, root: QVBoxLayout) -> None:
        licenses = _card(root)
        licenses.addWidget(_eyebrow("OPEN SOURCE"))
        licenses.addWidget(_title("오픈소스 라이선스"))
        self._open_source_notice = _description(licenses, OPEN_SOURCE_NOTICE)

    # --- 값 읽기·쓰기 ------------------------------------------------

    def _on_keep_resident_toggled(self, resident: bool) -> None:
        # 상주 중에는 유휴 종료 시간을 고를 이유가 없다 — 값이 살아 있는데
        # 아무 효과가 없는 콤보를 열어두면 "골랐는데 안 먹는다"가 된다.
        self.idle_combo.setEnabled(not resident)
        self.keep_resident_changed.emit(resident)

    def set_slm_options(self, *, keep_resident: bool, idle_timeout_sec: int, cpu_mode: str) -> None:
        """저장된 값으로 위젯을 맞춘다.

        신호를 막고 넣는다 — 복원 자체가 "사용자가 바꿨다"로 읽혀 저장·적용이
        도로 도는 것을 피한다(모델을 내리는 부작용까지 딸려 온다).
        """
        for widget in (self.keep_resident, self.idle_combo, self.cpu_combo):
            widget.blockSignals(True)
        try:
            self.keep_resident.setChecked(keep_resident)
            index = self.idle_combo.findData(idle_timeout_sec)
            if index >= 0:
                self.idle_combo.setCurrentIndex(index)
            index = self.cpu_combo.findData(cpu_mode)
            if index >= 0:
                self.cpu_combo.setCurrentIndex(index)
        finally:
            for widget in (self.keep_resident, self.idle_combo, self.cpu_combo):
                widget.blockSignals(False)
        self.idle_combo.setEnabled(not keep_resident)

    def current_idle_timeout(self) -> int:
        return int(self.idle_combo.currentData())

    def _on_chat_retention_changed(self) -> None:
        turns = self.current_chat_retain_turns()
        self._chat_retention_description.setText(chat_retention_description(turns))
        self.chat_retain_turns_changed.emit(turns)

    def set_chat_retain_turns(self, turns: int) -> None:
        """저장된 값으로 콤보·설명 문구를 맞춘다. `set_slm_options`와 같은
        이유로 신호를 막는다 — 복원이 "사용자가 바꿨다"로 읽히면 안 된다."""
        self.chat_retention_combo.blockSignals(True)
        try:
            index = self.chat_retention_combo.findData(turns)
            if index >= 0:
                self.chat_retention_combo.setCurrentIndex(index)
        finally:
            self.chat_retention_combo.blockSignals(False)
        self._chat_retention_description.setText(chat_retention_description(turns))

    def current_chat_retain_turns(self) -> int:
        return int(self.chat_retention_combo.currentData())

    def current_cpu_mode(self) -> str:
        return str(self.cpu_combo.currentData())

    def set_runtime_info(self, values: dict[str, str]) -> None:
        """실행 정보 4줄을 채운다. 긴 경로는 툴팁에 원본을 남긴다."""
        for key, label in self._runtime_values.items():
            text = values.get(key) or "—"
            label.setText(text)
            label.setToolTip(text)

    def runtime_text(self, key: str) -> str:
        """테스트·검증용."""
        return self._runtime_values[key].text()

    def open_source_notice_text(self) -> str:
        """테스트·검증용."""
        return self._open_source_notice.text()

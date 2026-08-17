"""AI 요약 카드 (T7.5) — 결과 목록 맨 위에 붙는다.

**DESIGN 문서에 이 카드의 명세가 없다.** §4.2에 토글만 있고 출력 영역은
정의된 적이 없어(§2.1 레이아웃에도 요약 자리가 없다) 이번에 새로 정했다
[사용자 확정, 2026-08-10]: 별도 패널이나 팝업이 아니라 **결과 목록 맨 위의
카드**다. 요약과 그 근거 카드를 한 화면에서 위아래로 대조할 수 있는 배치가
TECH 5.3의 "사용자가 즉시 검증할 수 있는 구조"에 가장 맞는다.

`objectName`은 **`AiSummaryCard`** — 결과 카드 세 종류가 공유하는
`"ResultCard"`를 쓰면 `ResultList.card_count()`에 함께 잡혀 "검색 결과 N건"
계산이 어긋난다.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from slm.summarize import Summary, SummaryStatus

TITLE = "AI 요약"
STARTING_MESSAGE = "AI 모델을 준비하는 중입니다… (첫 실행은 시간이 걸립니다.)"
GENERATING_MESSAGE = "AI 요약을 진행하고 있습니다. 성능에 따라 시간이 소요됩니다."
REVIEW_BADGE = "확인 필요"

# 진행 중임을 알리는 부드러운 깜박임(T10.22, 사용자 요청) — 생성형 AI가
# "지금 동작 중"을 보여주는 방식. 실측 지연이 수십 초라 정적인 문구만
# 있으면 멈춘 것처럼 보인다. 불투명도를 왕복시켜 눈에 거슬리지 않게 한다.
PULSE_DURATION_MS = 1200
PULSE_MIN_OPACITY = 0.35
PULSE_MAX_OPACITY = 1.0

# 기권·근거 없음은 실패가 아니다 — 안전장치가 의도대로 동작한 결과다.
# 사용자가 "고장"으로 읽지 않도록 다음 행동을 함께 안내한다.
ABSTAINED_HINT = "아래 검색 결과에서 직접 확인해 주세요."
NO_EVIDENCE_HINT = "질문과 충분히 관련된 문서를 찾지 못해 요약하지 않았습니다."


class SummaryCard(QFrame):
    """요약 1건 또는 그 진행/실패 상태를 보여준다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AiSummaryCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)  # 결과 카드와 같은 여백
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel(TITLE)
        title.setObjectName("AiSummaryTitle")
        header.addWidget(title)
        header.addStretch()

        self._badge = QLabel(REVIEW_BADGE)
        self._badge.setObjectName("AiSummaryReviewBadge")
        self._badge.setVisible(False)
        header.addWidget(self._badge)
        layout.addLayout(header)

        self._body = QLabel()
        self._body.setObjectName("AiSummaryBody")
        self._body.setWordWrap(True)
        # **PlainText로 고정한다.** 답변에는 모델이 발췌에서 옮겨온 문서 내용이
        # 그대로 섞이는데, RichText면 `<`가 든 문서(예: 수식·코드)가 태그로
        # 해석돼 글자가 사라진다. 결과 카드 본문은 하이라이트 때문에 RichText가
        # 필요하지만 여기는 하이라이트하지 않으므로 그럴 이유가 없다.
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        self._body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._body)

        self._hint = QLabel()
        self._hint.setObjectName("AiSummaryHint")
        self._hint.setWordWrap(True)
        self._hint.setVisible(False)
        layout.addWidget(self._hint)

        # 진행 중 깜박임(T10.22). `QGraphicsOpacityEffect`를 본문에만 걸어
        # 제목·배지는 안정적으로 남긴다 — 카드 전체가 깜박이면 산만하다.
        # 효과는 계속 붙여두고 애니메이션만 시작/정지한다(붙였다 뗐다 하면
        # 그때마다 레이아웃이 다시 계산돼 미세하게 흔들린다).
        self._pulse_effect = QGraphicsOpacityEffect(self._body)
        self._pulse_effect.setOpacity(PULSE_MAX_OPACITY)
        self._body.setGraphicsEffect(self._pulse_effect)

        self._pulse = QPropertyAnimation(self._pulse_effect, b"opacity", self)
        self._pulse.setDuration(PULSE_DURATION_MS)
        self._pulse.setStartValue(PULSE_MAX_OPACITY)
        self._pulse.setKeyValueAt(0.5, PULSE_MIN_OPACITY)
        self._pulse.setEndValue(PULSE_MAX_OPACITY)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)  # 부드럽게
        self._pulse.setLoopCount(-1)  # 완료·실패 시 코드가 명시적으로 멈춘다

    # --- 상태 전환 --------------------------------------------------

    def show_starting(self) -> None:
        """서버 기동 중 (첫 요청, 실측 4.7초)."""
        self._set(STARTING_MESSAGE, state="pending")

    def show_generating(self) -> None:
        self._set(GENERATING_MESSAGE, state="pending")

    def show_summary(self, summary: Summary) -> None:
        if summary.status is SummaryStatus.FAILED:
            self._set(summary.error or "AI 요약을 생성하지 못했습니다.", state="failed")
            return

        if summary.status is SummaryStatus.NO_EVIDENCE:
            self._set(summary.text, state="empty", hint=NO_EVIDENCE_HINT)
            return

        if summary.status is SummaryStatus.ABSTAINED:
            self._set(summary.text, state="empty", hint=ABSTAINED_HINT)
            return

        self._set(
            summary.text,
            state="ok",
            needs_review=summary.needs_review,
            hint=summary.review_reason if summary.needs_review else "",
        )

    def show_error(self, message: str) -> None:
        self._set(message, state="failed")

    # --- 내부 --------------------------------------------------

    def _set(
        self,
        text: str,
        *,
        state: str,
        needs_review: bool = False,
        hint: str = "",
    ) -> None:
        # "pending"(모델 준비 중·요약 생성 중)일 때만 깜박인다 — 결과가
        # 나온 뒤에도 계속 깜박이면 아직 진행 중인 것처럼 읽힌다.
        self._set_pulsing(state == "pending")
        self._body.setText(text)
        self._badge.setVisible(needs_review)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))

        # QSS가 상태별 색을 잡을 수 있도록 property로 노출하고, 이미 폴리시된
        # 위젯이라 unpolish/polish로 다시 적용시킨다(Phase 4·5에서 반복해
        # 밟은 자리 — property만 바꾸면 화면은 그대로다).
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def _set_pulsing(self, active: bool) -> None:
        if active:
            if self._pulse.state() != QPropertyAnimation.State.Running:
                self._pulse.start()
            return
        self._pulse.stop()
        # 멈춘 자리의 불투명도가 그대로 남으면 결과 문구가 흐리게 굳는다.
        self._pulse_effect.setOpacity(PULSE_MAX_OPACITY)

    def body_text(self) -> str:
        """테스트·검증용."""
        return self._body.text()

    def is_pulsing(self) -> bool:
        """테스트·검증용 — 진행 중 깜박임이 돌고 있는가."""
        return self._pulse.state() == QPropertyAnimation.State.Running

    def is_review_visible(self) -> bool:
        """"확인 필요" 배지가 이 카드 안에서 보이는 상태인가.

        `isVisible()`이 아니라 `isVisibleTo(self)`다 — 부모 창이 아직 화면에
        올라오지 않았으면 자식은 `setVisible(True)`를 해도 `isVisible()`이
        False다. Phase 4·5에서 반복해 밟은 "화면 부착 전/후로 값이 달라지는"
        함정과 같은 종류다.
        """
        return self._badge.isVisibleTo(self)

    def is_hint_visible(self) -> bool:
        return self._hint.isVisibleTo(self)

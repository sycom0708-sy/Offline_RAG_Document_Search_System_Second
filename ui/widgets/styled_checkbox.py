"""사용자 참조 이미지 스타일 체크박스 — 진한 사각 테두리 + 굵은 파란 체크마크.

QSS `::indicator { image: url(...) }` 방식은 애셋 파일 관리와 상대경로
문제가 생겨(TECH 9.1 포터블 원칙 — 압축 해제 후 즉시 실행) 커스텀 페인팅으로
대신한다. 클릭 판정·텍스트 접근(`text()`) 등 나머지는 `QCheckBox`를 그대로
물려받아 기존 호출부(`isChecked()`/`setChecked()`/`toggled`)가 그대로 동작한다.

**Phase 11에서 다크 네이비 배경용으로 바꿨다** — 유일한 사용처인 `FormatFilter`가
사이드바 확장 영역(다크 네이비)으로 들어갔기 때문이다. 밝은 배경 기준 색(흰 박스 +
진한 테두리)을 그대로 두면 네이비 위에서 흰 사각형만 도드라진다. 체크 시에는 박스를
강조색으로 채우고 체크마크를 흰색으로 그려 대비를 확보한다.

글자 크기는 `문서 형식` 섹션 라벨(`#SidebarSectionLabel`, 12px)과 맞추고, 박스 크기도
거기에 비례해 줄였다[사용자 확정, 2026-08-18].
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox

# 섹션 라벨(#SidebarSectionLabel)과 같은 12px, 박스는 거기에 비례(18*12/14≒16).
FONT_PIXEL_SIZE = 12
BOX_SIZE = 16
BOX_RADIUS = 4
# 다크 네이비(#1E293B) 위에서 읽히는 값들.
BORDER_COLOR = QColor("#94A3B8")   # 사이드바 비활성 텍스트와 같은 계열
FILL_COLOR = QColor("#2563EB")     # 체크 시 박스를 강조색으로 채운다
CHECK_COLOR = QColor("#FFFFFF")    # 채운 박스 위의 체크마크
TEXT_COLOR = QColor("#E2E8F0")
DISABLED_COLOR = QColor("#64748B")


class StyledCheckbox(QCheckBox):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        font = self.font()
        font.setPixelSize(FONT_PIXEL_SIZE)
        self.setFont(font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 이벤트 핸들러 네이밍
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        box_rect = QRectF(0, (self.height() - BOX_SIZE) / 2, BOX_SIZE, BOX_SIZE)
        enabled = self.isEnabled()
        checked = self.isChecked()
        if checked:
            border_color = FILL_COLOR if enabled else DISABLED_COLOR
            fill = FILL_COLOR if enabled else Qt.GlobalColor.transparent
        else:
            border_color = BORDER_COLOR if enabled else DISABLED_COLOR
            # 네이비 배경이 그대로 비쳐야 한다 — 흰색으로 채우면 사각형만 도드라진다.
            fill = Qt.GlobalColor.transparent
        painter.setPen(QPen(border_color, 2))
        painter.setBrush(fill)
        painter.drawRoundedRect(box_rect, BOX_RADIUS, BOX_RADIUS)

        if checked:
            check_color = CHECK_COLOR if enabled else DISABLED_COLOR
            pen = QPen(check_color, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            x, y = box_rect.x(), box_rect.y()
            path = QPainterPath()
            # 체크마크가 박스 우상단 모서리를 살짝 넘치도록 — 참조 이미지 비례
            path.moveTo(x + BOX_SIZE * 0.16, y + BOX_SIZE * 0.52)
            path.lineTo(x + BOX_SIZE * 0.42, y + BOX_SIZE * 0.80)
            path.lineTo(x + BOX_SIZE * 0.92, y + BOX_SIZE * 0.12)
            painter.drawPath(path)

        painter.setFont(self.font())
        painter.setPen(TEXT_COLOR if self.isEnabled() else DISABLED_COLOR)
        text_rect = QRectF(BOX_SIZE + 6, 0, self.width() - BOX_SIZE - 6, self.height())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.text(),
        )

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        return QSize(base.width(), max(base.height(), BOX_SIZE + 6))

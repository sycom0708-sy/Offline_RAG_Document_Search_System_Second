"""사용자 참조 이미지 스타일 체크박스 — 진한 사각 테두리 + 굵은 파란 체크마크.

QSS `::indicator { image: url(...) }` 방식은 애셋 파일 관리와 상대경로
문제가 생겨(TECH 9.1 포터블 원칙 — 압축 해제 후 즉시 실행) 커스텀 페인팅으로
대신한다. 클릭 판정·텍스트 접근(`text()`) 등 나머지는 `QCheckBox`를 그대로
물려받아 기존 호출부(`isChecked()`/`setChecked()`/`toggled`)가 그대로 동작한다.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox

BOX_SIZE = 18
BOX_RADIUS = 5
BORDER_COLOR = QColor("#1F2937")
CHECK_COLOR = QColor("#2563EB")
TEXT_COLOR = QColor("#1F2937")
DISABLED_COLOR = QColor("#D1D5DB")


class StyledCheckbox(QCheckBox):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        font = self.font()
        font.setPixelSize(14)
        self.setFont(font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 이벤트 핸들러 네이밍
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        box_rect = QRectF(0, (self.height() - BOX_SIZE) / 2, BOX_SIZE, BOX_SIZE)
        border_color = BORDER_COLOR if self.isEnabled() else DISABLED_COLOR
        painter.setPen(QPen(border_color, 2))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(box_rect, BOX_RADIUS, BOX_RADIUS)

        if self.isChecked():
            check_color = CHECK_COLOR if self.isEnabled() else DISABLED_COLOR
            pen = QPen(check_color, 3)
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
        text_rect = QRectF(BOX_SIZE + 8, 0, self.width() - BOX_SIZE - 8, self.height())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self.text(),
        )

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        return QSize(base.width(), max(base.height(), BOX_SIZE + 6))

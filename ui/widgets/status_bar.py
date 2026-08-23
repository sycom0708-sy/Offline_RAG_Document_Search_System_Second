"""하단 상태바 (T4.16, DESIGN §6, Phase 7.7).

인덱싱 진행 중에는 문서 수 텍스트를 "인덱싱 중… N/M"으로 바꾸고 진행바를
덧댄다 — TECH 4.6의 "진행 바와 함께 백그라운드 수행" 요구를 목업이 담지
못해서 DESIGN §6이 제안한 방식이다.

Phase 7.7에서 "폴더 관리" 버튼을 사이드바 하단(모델 관리 옆)으로 옮겼다 —
목업(`rag_ui_concept_*.html`)이 두 관리 버튼을 사이드바에 나란히 둔다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

IDLE_NO_DOCS_MESSAGE = "인덱싱된 문서가 없습니다"

# 상태바 우측 CI 워터마크 (2026-08-22 요청) — `ui/icons`는 `ui/qss`와 같은
# 방식으로 `deploy/app.spec`의 datas에 등록돼 있어, `__file__` 기준 상대
# 경로가 개발 모드·얼린 exe 양쪽에서 그대로 맞는다(T9.2와 같은 이유).
_LOGO_PATH = Path(__file__).resolve().parents[1] / "icons" / "atecmobility_ci.png"
_LOGO_HEIGHT = 12  # 상태바 한 줄 높이보다 작게 — 텍스트보다 튀지 않게


def build_ci_watermark_row() -> QWidget | None:
    """CI 워터마크 한 줄을 새로 만들어 반환한다.

    `StatusBar`가 없는 문서 관리·설정 페이지에도 같은 워터마크를 붙이려고
    분리했다(2026-08-23 요청) — 로고 파일이 없으면 `None`을 돌려줘 호출부가
    `if` 하나로 안전하게 건너뛸 수 있다.
    """
    if not _LOGO_PATH.is_file():
        return None
    row = QWidget()
    row.setObjectName("CiWatermarkRow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(16, 0, 16, 8)
    layout.addStretch()
    logo_label = QLabel()
    logo_label.setPixmap(
        QPixmap(str(_LOGO_PATH)).scaledToHeight(
            _LOGO_HEIGHT, Qt.TransformationMode.SmoothTransformation
        )
    )
    layout.addWidget(logo_label)
    return row


def format_relative_time(dt: datetime, now: datetime | None = None) -> str:
    """DESIGN §6: 상대 시간 표기(`10분 전`)."""
    now = now or datetime.now(dt.tzinfo)
    seconds = max(0.0, (now - dt).total_seconds())

    if seconds < 60:
        return "방금 전"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}분 전"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}시간 전"
    days = hours / 24
    if days < 7:
        return f"{int(days)}일 전"
    return dt.strftime("%Y-%m-%d")


class StatusBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBar")

        self._info_label = QLabel(IDLE_NO_DOCS_MESSAGE)
        self._info_label.setObjectName("StatusBarInfo")

        # T10.2: LibreOffice 미설치로 구버전 문서(.doc/.xls/.ppt)가 조용히
        # 0청크로 빠지는 것을 안내한다. 정적 안내만 보여준다(사용자 확정) —
        # 자동 다운로드·재시도 버튼은 없다.
        self._warning_label = QLabel("")
        self._warning_label.setObjectName("StatusBarWarning")
        self._warning_label.hide()

        self._progress = QProgressBar()
        self._progress.setObjectName("StatusBarProgress")
        self._progress.setTextVisible(False)
        self._progress.setFixedWidth(160)
        self._progress.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.addWidget(self._info_label)
        layout.addWidget(self._warning_label)
        layout.addWidget(self._progress)
        layout.addStretch()

        # CI 워터마크 — 검색 흐름을 방해하지 않는 우측 끝에 작게.
        if _LOGO_PATH.is_file():
            logo_label = QLabel()
            logo_label.setPixmap(
                QPixmap(str(_LOGO_PATH)).scaledToHeight(
                    _LOGO_HEIGHT, Qt.TransformationMode.SmoothTransformation
                )
            )
            layout.addWidget(logo_label)

    def set_idle(self, document_count: int, last_indexed_at: datetime | None) -> None:
        self._progress.hide()
        if document_count == 0:
            self._info_label.setText(IDLE_NO_DOCS_MESSAGE)
            return

        text = f"문서 {document_count:,}개 인덱싱됨"
        if last_indexed_at is not None:
            text += f" · 마지막 갱신 {format_relative_time(last_indexed_at)}"
        self._info_label.setText(text)

    def set_warning(self, message: str | None) -> None:
        """인덱싱 완료 후 안내가 있으면 보여주고, 없으면 숨긴다.

        다음 인덱싱이 끝날 때까지 그대로 남아 있는다 — 검색·필터 조작으로는
        지워지지 않는다(상태바를 다시 그리는 지점이 인덱싱 완료뿐이라 자연히
        그렇게 된다).
        """
        if message:
            self._warning_label.setText(message)
            self._warning_label.show()
        else:
            self._warning_label.hide()

    def set_indexing_progress(self, done: int, total: int) -> None:
        self._info_label.setText(f"인덱싱 중… {done:,}/{total:,}")
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)  # 총량 미확정 — 불확정(marquee) 표시
        self._progress.show()

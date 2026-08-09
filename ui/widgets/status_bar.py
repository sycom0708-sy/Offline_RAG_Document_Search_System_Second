"""하단 상태바 (T4.16, DESIGN §6).

인덱싱 진행 중에는 문서 수 텍스트를 "인덱싱 중… N/M"으로 바꾸고 진행바를
덧댄다 — TECH 4.6의 "진행 바와 함께 백그라운드 수행" 요구를 목업이 담지
못해서 DESIGN §6이 제안한 방식이다.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

IDLE_NO_DOCS_MESSAGE = "인덱싱된 문서가 없습니다"


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

        self.folder_button = QPushButton("폴더 관리")
        self.folder_button.setObjectName("StatusBarFolderButton")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.addWidget(self._info_label)
        layout.addWidget(self._warning_label)
        layout.addWidget(self._progress)
        layout.addStretch()
        layout.addWidget(self.folder_button)

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

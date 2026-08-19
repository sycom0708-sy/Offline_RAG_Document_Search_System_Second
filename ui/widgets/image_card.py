"""이미지 카드 (T5.3~T5.5, DESIGN §5.5).

썸네일은 캐시(`ui/thumbnail_cache.py`)를 조회해 즉시 표시하고, "확대"는
원본을 화면의 80% 이내로 스케일해 `QDialog`로 보여준다 — 목업·TECH 어디에도
확대 동작의 세부 스펙이 없어 가장 자연스러운 해석을 택했다(별도 팬/줌
인터랙션 없이 크게 보여주기만).

썸네일 로딩·확대 다이얼로그는 `card_common.py`의 공유 함수다 — 챗봇 즉시
발췌(`chat_panel.py`)도 이미지 청크가 top-1일 때 같은 함수로 렌더링한다
(2026-08-14).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from search.hybrid_search import HybridResult
from search.office_link import plan_open
from ui.widgets.card_common import (
    NearbySection,
    SummarySection,
    apply_low_relevance_style,
    build_card_header,
    build_heading_label,
    load_image_thumbnail,
    parse_image_data,
    show_image_zoom_dialog,
    start_open_source_file,
)

IMAGE_TEXT_NOT_RECOGNIZED_NOTICE = "이미지 내 텍스트는 인식되지 않았습니다."
NO_PREVIEW_TEXT = "미리보기를 표시할 수 없습니다."
THUMBNAIL_DISPLAY_SIZE = 120


class ImageCard(QFrame):
    open_failed = Signal(str)
    summarize_requested = Signal(object, object)  # T10.14, ResultCard와 동일
    nearby_requested = Signal(object, str)  # T10.21, ResultCard와 동일

    def __init__(self, hybrid_result: HybridResult, show_summary: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultCard")
        self._result = hybrid_result
        self._image_data = parse_image_data(hybrid_result.result)

        zoom_button = QPushButton("확대")
        zoom_button.setObjectName("ResultCardCopyButton")  # 원문열기와 같은 텍스트버튼 스타일 재사용
        zoom_button.setCursor(Qt.CursorShape.PointingHandCursor)
        zoom_button.clicked.connect(self._zoom)
        self._zoom_button = zoom_button

        header, open_button = build_card_header(hybrid_result, extra_buttons=[zoom_button])
        self._open_button = open_button
        open_button.clicked.connect(self._open_source)

        thumbnail_label = QLabel()
        thumbnail_label.setObjectName("ImageCardThumbnail")
        thumbnail_label.setFixedSize(THUMBNAIL_DISPLAY_SIZE, THUMBNAIL_DISPLAY_SIZE)
        thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        pixmap = load_image_thumbnail(self._result.chunk_id, self._image_data, THUMBNAIL_DISPLAY_SIZE)
        if pixmap is not None:
            thumbnail_label.setPixmap(pixmap)
        else:
            thumbnail_label.setText(NO_PREVIEW_TEXT)
            thumbnail_label.setWordWrap(True)
            self._zoom_button.setEnabled(False)

        notice = QLabel(IMAGE_TEXT_NOT_RECOGNIZED_NOTICE)
        notice.setObjectName("ImageCardNotice")
        notice.setWordWrap(True)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(thumbnail_label)
        body.addWidget(notice, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        heading_label = build_heading_label(hybrid_result)
        if heading_label is not None:
            layout.addWidget(heading_label)
        layout.addLayout(body)

        self.summary_section: SummarySection | None = None
        if show_summary:
            self.summary_section = SummarySection()
            self.summary_section.requested.connect(self._request_summary)
            layout.addWidget(self.summary_section)

        self.nearby_section = NearbySection(hybrid_result.result.chunk_id)
        self.nearby_section.requested.connect(self._request_nearby)
        layout.addWidget(self.nearby_section)

        apply_low_relevance_style(self, hybrid_result)

    def _request_summary(self) -> None:
        self.summarize_requested.emit(self.summary_section, self._result)

    def _request_nearby(self, chunk_id: str) -> None:
        self.nearby_requested.emit(self.nearby_section, chunk_id)

    def _zoom(self) -> None:
        if self._image_data is None:
            return
        error = show_image_zoom_dialog(self, self._image_data.image_path, self._result.file_name)
        if error:
            self.open_failed.emit(error)

    def _open_source(self) -> None:
        path = Path(self._result.result.file_path)
        if not path.is_file():
            self.open_failed.emit(f"파일을 찾을 수 없습니다: {path}")
            return
        plan = plan_open(self._result)
        self._open_worker = start_open_source_file(
            str(path), plan, self._open_button, self.open_failed.emit
        )

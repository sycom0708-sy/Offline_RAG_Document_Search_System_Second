"""이미지 카드 (T5.3~T5.5, DESIGN §5.5).

썸네일은 캐시(`ui/thumbnail_cache.py`)를 조회해 즉시 표시하고, "확대"는
원본을 화면의 80% 이내로 스케일해 `QDialog`로 보여준다 — 목업·TECH 어디에도
확대 동작의 세부 스펙이 없어 가장 자연스러운 해석을 택했다(별도 팬/줌
인터랙션 없이 크게 보여주기만).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from search.hybrid_search import HybridResult
from ui.thumbnail_cache import get_thumbnail_path
from ui.widgets.card_common import build_card_header, open_source_file, parse_image_data

IMAGE_TEXT_NOT_RECOGNIZED_NOTICE = "이미지 내 텍스트는 인식되지 않았습니다."
NO_PREVIEW_TEXT = "미리보기를 표시할 수 없습니다."
THUMBNAIL_DISPLAY_SIZE = 120


class ImageCard(QFrame):
    open_failed = Signal(str)

    def __init__(self, hybrid_result: HybridResult, parent=None) -> None:
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
        open_button.clicked.connect(self._open_source)

        thumbnail_label = QLabel()
        thumbnail_label.setObjectName("ImageCardThumbnail")
        thumbnail_label.setFixedSize(THUMBNAIL_DISPLAY_SIZE, THUMBNAIL_DISPLAY_SIZE)
        thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        pixmap = self._load_thumbnail()
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
        layout.addLayout(body)

    def _load_thumbnail(self) -> QPixmap | None:
        if self._image_data is None:
            return None
        source = Path(self._image_data.image_path)
        cache_path = get_thumbnail_path(self._result.chunk_id, source)
        if cache_path is None:
            return None
        pixmap = QPixmap(str(cache_path))
        if pixmap.isNull():
            return None
        return pixmap.scaled(
            THUMBNAIL_DISPLAY_SIZE,
            THUMBNAIL_DISPLAY_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _zoom(self) -> None:
        if self._image_data is None:
            return
        path = Path(self._image_data.image_path)
        if not path.is_file():
            self.open_failed.emit(f"이미지를 찾을 수 없습니다: {path}")
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.open_failed.emit(f"이미지를 열 수 없습니다: {path}")
            return

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableSize()
            max_size = QSize(int(available.width() * 0.8), int(available.height() * 0.8))
            if pixmap.width() > max_size.width() or pixmap.height() > max_size.height():
                pixmap = pixmap.scaled(
                    max_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

        dialog = QDialog(self)
        dialog.setWindowTitle(self._result.file_name)
        label = QLabel()
        label.setPixmap(pixmap)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(label)
        dialog.exec()

    def _open_source(self) -> None:
        error = open_source_file(self._result.result.file_path)
        if error:
            self.open_failed.emit(error)

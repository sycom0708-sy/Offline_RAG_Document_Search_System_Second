"""모델 관리 팝업 — 임베딩 섹션만 (T4.11a~T4.11b).

원래 Phase 7(T7.6) 범위였던 임베딩 섹션을 이번 Phase로 앞당긴다. sLM
섹션은 이 저장소에 아직 다운로드 인프라가 없어(Phase 6/7 미착수) 실제
동작하지 않는 행을 가짜로 넣지 않고 안내 문구만 둔다.

`KURE-v1`은 허깅페이스 레포에 ONNX가 없어(safetensors만 제공) 실제
다운로드가 동작하려면 별도 변환 파이프라인이 필요하다 — 설치돼 있지
않으면 "준비 중" 배지만 보여주고 다운로드 버튼은 비활성 상태로 둔다
(PLAN §4-C 확정 사항). 파일이 실제로 존재하면(수동 배치 등) 정상적으로
"설치됨"으로 인식한다 — 상태를 하드코딩하지 않고 항상 실제로 검사한다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.settings import LIGHT, PROFILE_ORDER, PROFILES, ModelProfile

INTRO_TEXT = (
    "sLM 모델은 용량이 커서 프로그램에 포함되지 않습니다.\n"
    "인터넷이 되는 PC에서 받아 models 폴더에 넣은 뒤 새로고침을 누르세요."
)
SLM_PLACEHOLDER = "AI 요약 모델(sLM)은 Phase 7에서 추가됩니다."


class ModelManagerDialog(QDialog):
    """임베딩 모델 목록 + (자리만 있는) sLM 안내."""

    def __init__(self, focus_profile: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("모델 관리")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        intro = QLabel(INTRO_TEXT)
        intro.setObjectName("ModelManagerIntro")
        layout.addWidget(intro)

        section_label = QLabel("임베딩 모델 — 검색 정확도")
        section_label.setObjectName("SidebarSectionLabel")
        layout.addWidget(section_label)

        self.rows: dict[str, "_ModelRow"] = {}
        for key in PROFILE_ORDER:
            row = _ModelRow(PROFILES[key])
            row.folder_requested.connect(self._open_folder)
            self.rows[key] = row
            layout.addWidget(row)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)

        slm_note = QLabel(SLM_PLACEHOLDER)
        slm_note.setObjectName("ModelManagerPlaceholder")
        layout.addWidget(slm_note)

        footer = QHBoxLayout()
        footer.addStretch()
        self.refresh_button = QPushButton("새로고침 (파일 확인)")
        self.refresh_button.clicked.connect(self.refresh)
        footer.addWidget(self.refresh_button)
        layout.addLayout(footer)

        if focus_profile and focus_profile in self.rows:
            self.rows[focus_profile].setFocus()

    def refresh(self) -> None:
        for row in self.rows.values():
            row.refresh()

    def _open_folder(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        # 다른 OS는 이번 범위 밖 (TECH 문서 기준 Windows 전용 배포)


class _ModelRow(QWidget):
    folder_requested = Signal(Path)

    def __init__(self, profile: ModelProfile, parent=None) -> None:
        super().__init__(parent)
        self._profile = profile
        # 기본 QWidget은 포커스를 받지 못한다 — 다이얼로그가 특정 행에 포커스를
        # 주려면(예: 콤보에서 미설치 옵션을 골랐을 때) 명시적으로 정책을 켜야 한다.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QGridLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)

        name = QLabel(profile.repo_id)
        name.setObjectName("ModelRowName")
        layout.addWidget(name, 0, 0)

        self._badge = QLabel()
        self._badge.setObjectName("ModelRowBadge")
        layout.addWidget(self._badge, 0, 1)

        self._folder_btn = QPushButton("폴더 열기")
        self._folder_btn.clicked.connect(lambda: self.folder_requested.emit(self._profile.local_dir))
        layout.addWidget(self._folder_btn, 0, 2)

        self._download_btn = QPushButton("다운로드 안내")
        self._download_btn.setEnabled(False)  # T7.7에서 실제 다운로드 흐름 구현 예정
        layout.addWidget(self._download_btn, 0, 3)

        self._note = QLabel()
        self._note.setObjectName("ModelRowNote")
        self._note.setWordWrap(True)
        layout.addWidget(self._note, 1, 0, 1, 4)

        self.refresh()

    def refresh(self) -> None:
        profile = self._profile
        installed = profile.is_installed()
        bundled_suffix = " · 프로그램 포함" if profile.key == LIGHT.key else ""

        if installed:
            self._badge.setText(f"설치됨{bundled_suffix}")
            self._badge.setProperty("state", "installed")
            self._folder_btn.setEnabled(True)
            self._note.setText("")
        else:
            self._badge.setText("준비 중")
            self._badge.setProperty("state", "pending")
            self._folder_btn.setEnabled(False)
            if profile.key == LIGHT.key:
                self._note.setText(
                    "`python -m indexer.vector.download`로 받아 models 폴더에 넣어주세요."
                )
            else:
                self._note.setText(
                    "원본 레포에 ONNX가 없어 변환 파이프라인 구현 후 지원 예정입니다."
                )

        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)

"""모델 관리 팝업 — 임베딩 + sLM (T4.11a~T4.11b, T7.6~T7.10, T7.5.5).

`KURE-v1`은 허깅페이스 레포에 ONNX가 없어(safetensors만 제공) Phase 3~4
동안은 "준비 중" 배지 + 비활성 버튼으로만 노출했다(PLAN §4-C). Phase 7.5에서
`scripts/convert_kure.py` 변환 파이프라인이 생기면서 실제로 설치 가능해졌다
— 미설치 상태는 이제 "미설치"(정상적으로 아직 안 받은 것)로 표시하고,
"설치 안내" 버튼이 변환 방법을 알려준다. 파일이 실제로 존재하면 정상적으로
"설치됨"으로 인식한다 — 상태를 하드코딩하지 않고 항상 실제로 검사한다.

**sLM은 Phase 7에서 채택된 2종만 노출한다**(`config.settings.SLM_OFFERED`).
측정 후보는 4종이지만 그건 벤치마크 하네스의 관심사고, 제품이 권할 모델은
권장 사양 Qwen3.5-4B / 최소 사양 EXAONE-4.0-1.2B 둘뿐이다.

체크섬 검증(TECH 9.3)은 GB 단위 파일을 통째로 읽으므로 **백그라운드
스레드**에서 돈다 — UI 스레드에서 하면 수 초간 창이 얼어붙는다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.settings import (
    LIGHT,
    PROFILE_ORDER,
    PROFILES,
    SLM_MINIMUM,
    SLM_OFFERED,
    SLM_RECOMMENDED,
    ModelProfile,
    SlmProfile,
)
from slm.download import load_verified_marker, save_verified_marker, verify_installed

INTRO_TEXT = (
    "sLM 모델은 용량이 커서 프로그램에 포함되지 않습니다.\n"
    "인터넷이 되는 PC에서 받아 models 폴더에 넣은 뒤 새로고침을 누르세요."
)

# 사양별 권장 표시 — 어느 것을 골라야 하는지가 목록만 봐서는 안 보인다.
_TIER_LABEL = {
    SLM_RECOMMENDED: "권장 사양 (16GB)",
    SLM_MINIMUM: "최소 사양 (8GB)",
}


class ModelManagerDialog(QDialog):
    """임베딩 모델 + AI 요약 모델(sLM) 목록."""

    # 사용자가 sLM 설치 상태를 바꿨을 수 있다 — MainWindow가 토글 가용성을
    # 다시 계산해야 한다.
    slm_changed = Signal()

    def __init__(
        self,
        focus_profile: str | None = None,
        parent=None,
        verify_checksums: bool = True,
    ) -> None:
        """`verify_checksums=False`는 테스트용이다 — 실제 GGUF(수 GB)를 해시하지
        않게 한다. `MainWindow(db_path=..., state=...)`와 같은 주입 방식."""
        super().__init__(parent)
        self.setObjectName("ModelManagerDialog")
        self.setWindowTitle("모델 관리")
        self.setMinimumWidth(560)
        self._verify_checksums = verify_checksums

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

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
        divider.setObjectName("ModelManagerDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        slm_label = QLabel("AI 요약 모델 (sLM) — 선택 설치")
        slm_label.setObjectName("SidebarSectionLabel")
        layout.addWidget(slm_label)

        self.slm_rows: dict[str, "_SlmRow"] = {}
        for profile in SLM_OFFERED:
            row = _SlmRow(profile)
            row.folder_requested.connect(self._open_folder)
            row.download_requested.connect(self._show_download_guide)
            self.slm_rows[profile.key] = row
            layout.addWidget(row)

        footer = QHBoxLayout()
        footer.addStretch()
        self.refresh_button = QPushButton("새로고침 (파일 확인)")
        self.refresh_button.setObjectName("PrimaryButton")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.clicked.connect(self.refresh)
        footer.addWidget(self.refresh_button)
        layout.addLayout(footer)

        self._verify_thread: QThread | None = None
        self._verifier: "_VerifyWorker | None" = None

        if focus_profile and focus_profile in self.rows:
            self.rows[focus_profile].setFocus()

    def refresh(self) -> None:
        """재스캔 + 체크섬 검증 (TECH 9.3).

        크기 검사는 즉시 하고, 해시는 GB 단위라 백그라운드로 돌린다.
        """
        for row in self.rows.values():
            row.refresh()
        for row in self.slm_rows.values():
            row.refresh()
        self.slm_changed.emit()
        self._start_checksum_verification()

    def _start_checksum_verification(self) -> None:
        if not self._verify_checksums:
            return

        targets = []
        for row in self.slm_rows.values():
            profile = row.profile
            if not (profile.is_installed() and profile.sha256):
                continue
            # 지난번에 검증한 그 파일 그대로면 다시 읽지 않는다 — GB 단위를
            # 새로고침마다 해시하면 버튼이 매번 수십 초 돈다.
            if load_verified_marker(profile):
                row.set_verified(None)
                continue
            targets.append(profile)

        if not targets or self._verify_thread is not None:
            return

        for profile in targets:
            self.slm_rows[profile.key].set_verifying()

        thread = QThread(self)
        worker = _VerifyWorker(targets)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.verified.connect(self._on_verified)
        worker.done.connect(thread.quit)
        thread.finished.connect(self._on_verify_finished)
        # 워커·스레드 참조를 붙들어야 한다 — 놓치면 실행 중에 GC되며 죽는다
        # (Phase 6에서 SearchWorker로 실측한 것과 같은 크래시).
        self._verify_thread = thread
        self._verifier = worker
        thread.start()

    def _on_verified(self, key: str, problem: str) -> None:
        row = self.slm_rows.get(key)
        if row is None:
            return
        row.set_verified(problem or None)
        if not problem:
            save_verified_marker(row.profile)

    def _on_verify_finished(self) -> None:
        if self._verify_thread is not None:
            self._verify_thread.deleteLater()
        self._verify_thread = None
        self._verifier = None
        self.slm_changed.emit()

    def _show_download_guide(self, profile: SlmProfile) -> None:
        """TECH 9.3 다운로드 안내 팝업 — 링크·파일명·용량·체크섬·저장 위치.

        **자동 다운로드는 하지 않는다.** 오프라인 PC가 전제라 앱이 인터넷에
        나가지 않고, 인터넷 되는 PC에서 받아 옮기는 흐름을 안내한다
        (LibreOffice 포터블과 같은 방침 — TECH 9.1).
        """
        checksum = profile.sha256 or "(기록된 체크섬 없음 — 크기만 확인합니다)"
        QMessageBox.information(
            self,
            f"{profile.label} 다운로드 안내",
            f"인터넷이 되는 PC에서 아래 파일을 받아 저장 위치에 넣은 뒤 "
            f"'새로고침 (파일 확인)'을 누르세요.\n\n"
            f"링크\n{profile.download_url}\n\n"
            f"파일명\n{profile.local_path.name}  (레포 원본: {profile.gguf_file})\n\n"
            f"용량\n{profile.size_gb:.2f} GB\n\n"
            f"SHA256\n{checksum}\n\n"
            f"저장 위치\n{profile.local_path.parent}\n\n"
            f"명령줄로 받으려면:\n  python -m slm.download {profile.key}",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt 규약
        """검증 스레드가 도는 중 창을 닫으면 죽는다 — 기다렸다 닫는다."""
        if self._verify_thread is not None:
            self._verify_thread.quit()
            self._verify_thread.wait(3000)
        super().closeEvent(event)

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
        self.setObjectName("ModelRow")
        # 🔴 순정 QWidget은 스타일시트의 background/border를 기본적으로 안 그린다
        # (QFrame과 다르다) — 이 속성 없이 #ModelRow에 배경을 걸면 조용히 무시된다.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QGridLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(10)

        name = QLabel(profile.repo_id)
        name.setObjectName("ModelRowName")
        layout.addWidget(name, 0, 0)

        self._badge = QLabel()
        self._badge.setObjectName("ModelRowBadge")
        layout.addWidget(self._badge, 0, 1)

        self._folder_btn = QPushButton("폴더 열기")
        self._folder_btn.setObjectName("SidebarFooterButton")
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.clicked.connect(lambda: self.folder_requested.emit(self._profile.local_dir))
        layout.addWidget(self._folder_btn, 0, 2)

        self._download_btn = QPushButton("설치 안내")
        self._download_btn.setObjectName("SidebarFooterButton")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # LIGHT는 항상 번들 설치라 버튼이 쓰일 일이 없다. HEAVY(KURE-v1)는
        # Phase 7.5부터 변환 파이프라인이 생겨 안내가 실제로 의미 있다 —
        # "다운로드"가 아니라 "직접 변환하거나 폴더를 복사하라"는 안내라
        # sLM 행의 "다운로드 안내"와 문구를 구분했다.
        self._download_btn.clicked.connect(self._show_install_guide)
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
            self._download_btn.setEnabled(False)
            self._note.setText("")
        else:
            self._folder_btn.setEnabled(False)
            if profile.key == LIGHT.key:
                # 번들 모델이 없는 건 배포가 깨진 것에 가깝다 — 정상 경로가
                # 아니므로 기존 문구를 그대로 둔다.
                self._badge.setText("준비 중")
                self._badge.setProperty("state", "pending")
                self._download_btn.setEnabled(False)
                self._note.setText(
                    "`python -m indexer.vector.download`로 받아 models 폴더에 넣어주세요."
                )
            else:
                # Phase 7.5부터 변환 파이프라인(scripts/convert_kure.py)이
                # 있으므로 "준비 중"(=지원 안 함)이 아니라 "미설치"다.
                self._badge.setText("미설치")
                self._badge.setProperty("state", "pending")
                self._download_btn.setEnabled(True)
                self._note.setText(
                    "변환 스크립트를 실행하거나 다른 PC의 models/KURE-v1 폴더를 "
                    "복사하세요. '설치 안내' 참고."
                )

        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)

    def _show_install_guide(self) -> None:
        """KURE-v1 설치 안내 — sLM과 달리 단순 다운로드가 아니라 **변환**이다.

        허깅페이스에 이 프로젝트가 쓰는 형식(ONNX int8)의 아티팩트가 없어서
        직접 만들어야 한다(Phase 7.5). 자동화하지 않는다 — 인터넷이 되는 PC에서
        1회 실행하고 결과물을 오프라인 PC로 옮기는 게 이 프로젝트의 일관된
        배포 방침이다(LibreOffice 포터블·sLM GGUF와 동일, TECH 9.1/9.3).
        """
        profile = self._profile
        QMessageBox.information(
            self,
            f"{profile.label} 설치 안내",
            "이 모델은 허깅페이스에 완성된 ONNX 파일이 없어 자동 다운로드가 "
            "안 됩니다. 아래 둘 중 하나로 준비하세요.\n\n"
            "방법 1 — 이 PC에서 직접 변환 (인터넷 필요, 1회):\n"
            "  py -3.14 -m venv .venv-convert\n"
            "  .venv-convert/Scripts/python -m pip install torch "
            "--index-url https://download.pytorch.org/whl/cpu\n"
            "  .venv-convert/Scripts/python -m pip install "
            '"optimum[onnxruntime]" sentence-transformers truststore\n'
            "  .venv-convert/Scripts/python -m scripts.convert_kure --clean\n\n"
            "방법 2 — 이미 변환해 둔 다른 PC에서 폴더째 복사:\n"
            f"  {profile.local_dir}\n\n"
            "완료 후 '새로고침 (파일 확인)'을 누르세요.",
        )


class _SlmRow(QWidget):
    """AI 요약 모델 한 줄 (T7.6).

    임베딩(`_ModelRow`)과 달리 **다운로드 안내가 실제로 동작한다** — sLM은
    Phase 6에서 다운로더(`slm/download.py`)까지 만들어 뒀기 때문이다.
    """

    folder_requested = Signal(Path)
    download_requested = Signal(object)  # SlmProfile

    def __init__(self, profile: SlmProfile, parent=None) -> None:
        super().__init__(parent)
        self._profile = profile
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("ModelRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QGridLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setHorizontalSpacing(10)

        tier = _TIER_LABEL.get(profile.key, "")
        name = QLabel(f"{profile.label} · {profile.size_gb:.2f} GB")
        name.setObjectName("ModelRowName")
        layout.addWidget(name, 0, 0)

        self._badge = QLabel()
        self._badge.setObjectName("ModelRowBadge")
        layout.addWidget(self._badge, 0, 1)

        self._folder_btn = QPushButton("폴더 열기")
        self._folder_btn.setObjectName("SidebarFooterButton")
        self._folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._folder_btn.clicked.connect(
            lambda: self.folder_requested.emit(self._profile.local_path.parent)
        )
        layout.addWidget(self._folder_btn, 0, 2)

        self._download_btn = QPushButton("다운로드 안내")
        self._download_btn.setObjectName("SidebarFooterButton")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.clicked.connect(
            lambda: self.download_requested.emit(self._profile)
        )
        layout.addWidget(self._download_btn, 0, 3)

        self._note = QLabel()
        self._note.setObjectName("ModelRowNote")
        self._note.setWordWrap(True)
        layout.addWidget(self._note, 1, 0, 1, 4)

        self._tier = tier
        self.refresh()

    @property
    def profile(self) -> SlmProfile:
        return self._profile

    def refresh(self) -> None:
        """크기까지만 즉시 검사한다 — 해시는 오래 걸려 별도로 돈다."""
        problem = verify_installed(self._profile, check_hash=False)
        if problem is None:
            self._set_badge("설치됨", "installed")
            self._folder_btn.setEnabled(True)
            self._note.setText(self._tier)
        elif not self._profile.is_installed():
            self._set_badge("다운로드 필요", "pending")
            self._folder_btn.setEnabled(False)
            self._note.setText(f"{self._tier} — 다운로드 안내를 눌러 받는 방법을 확인하세요.")
        else:
            self._set_badge("확인 필요", "invalid")
            self._folder_btn.setEnabled(True)
            self._note.setText(problem)

    def set_verifying(self) -> None:
        self._set_badge("검증 중…", "pending")
        self._note.setText("체크섬을 확인하는 중입니다 (파일이 커서 시간이 걸립니다).")

    def set_verified(self, problem: str | None) -> None:
        if problem is None:
            self._set_badge("설치됨 · 검증됨", "installed")
            self._note.setText(self._tier)
        else:
            self._set_badge("확인 필요", "invalid")
            self._note.setText(problem)

    def _set_badge(self, text: str, state: str) -> None:
        self._badge.setText(text)
        self._badge.setProperty("state", state)
        self._badge.style().unpolish(self._badge)
        self._badge.style().polish(self._badge)


class _VerifyWorker(QObject):
    """SHA256 검증을 백그라운드에서 돈다 (T7.9).

    GB 단위 파일을 읽으므로 UI 스레드에서 하면 창이 수 초간 얼어붙는다.
    """

    verified = Signal(str, str)  # (profile key, 문제 사유 — 정상이면 빈 문자열)
    done = Signal()

    def __init__(self, profiles: list[SlmProfile]) -> None:
        super().__init__()
        self._profiles = profiles

    def run(self) -> None:
        for profile in self._profiles:
            try:
                problem = verify_installed(profile, check_hash=True)
            except OSError as exc:
                problem = f"파일을 읽지 못했습니다: {exc}"
            self.verified.emit(profile.key, problem or "")
        self.done.emit()

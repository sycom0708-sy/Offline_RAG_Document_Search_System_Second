"""검색 옵션·PC 성능·모델 관리 위젯 테스트 (T4.7~T4.11b)."""

from __future__ import annotations

from config.settings import HEAVY, LIGHT
from ui.widgets.model_manager_dialog import ModelManagerDialog
from ui.widgets.performance_combo import PerformanceCombo
from ui.widgets.search_options import SearchOptions


class TestSearchOptions:
    def test_all_three_toggles_default_off(self, qtbot):
        widget = SearchOptions()
        qtbot.addWidget(widget)
        assert widget.ai_summary.isChecked() is False
        assert widget.is_case_sensitive() is False
        assert widget.is_exact_word() is False

    def test_ai_summary_toggle_is_disabled(self, qtbot):
        """DESIGN §4.2 결정: 비활성 + 툴팁으로 Phase 7 이전까지 막아둔다."""
        widget = SearchOptions()
        qtbot.addWidget(widget)
        assert widget.ai_summary.isEnabled() is False
        assert "Phase 7" in widget.ai_summary.toolTip()

    def test_case_sensitive_toggle_emits_signal(self, qtbot):
        widget = SearchOptions()
        qtbot.addWidget(widget)
        received = []
        widget.case_sensitive_changed.connect(received.append)
        widget.case_sensitive.setChecked(True)
        assert received == [True]

    def test_exact_word_toggle_emits_signal(self, qtbot):
        widget = SearchOptions()
        qtbot.addWidget(widget)
        received = []
        widget.exact_word_changed.connect(received.append)
        widget.exact_word.setChecked(True)
        assert received == [True]

    def test_toggles_are_independent(self, qtbot):
        widget = SearchOptions()
        qtbot.addWidget(widget)
        widget.case_sensitive.setChecked(True)
        assert widget.is_exact_word() is False
        assert widget.is_case_sensitive() is True


class TestPerformanceCombo:
    def test_default_selection_is_light(self, qtbot):
        widget = PerformanceCombo()
        qtbot.addWidget(widget)
        assert widget.current_profile() == LIGHT.key

    def test_shows_installed_badge_for_light(self, qtbot):
        """이 개발 환경에는 LIGHT 모델이 실제로 받아져 있다 (Phase 3에서 확인)."""
        widget = PerformanceCombo()
        qtbot.addWidget(widget)
        assert "설치됨" in widget._combo.itemText(0)

    def test_shows_pending_badge_for_uninstalled_heavy(self, qtbot):
        widget = PerformanceCombo()
        qtbot.addWidget(widget)
        heavy_index = widget._combo.findData(HEAVY.key)
        assert "준비 중" in widget._combo.itemText(heavy_index)

    def test_selecting_installed_profile_emits_profile_activated(self, qtbot):
        widget = PerformanceCombo()
        qtbot.addWidget(widget)
        received = []
        widget.profile_activated.connect(received.append)

        light_index = widget._combo.findData(LIGHT.key)
        widget._combo.setCurrentIndex(light_index)
        widget._on_activated(light_index)  # activated는 실제 사용자 조작에서만 발생

        assert received == [LIGHT.key]

    def test_selecting_uninstalled_profile_requests_model_manager_and_reverts(self, qtbot):
        """Option A 핵심: 미설치 선택 시 콤보는 현재 유효 프로파일로 되돌아간다."""
        widget = PerformanceCombo()
        qtbot.addWidget(widget)
        requested = []
        widget.model_manager_requested.connect(requested.append)

        heavy_index = widget._combo.findData(HEAVY.key)
        widget._on_activated(heavy_index)

        assert requested == [HEAVY.key]
        assert widget.current_profile() == LIGHT.key  # 되돌아감
        assert widget._combo.currentData() == LIGHT.key

    def test_refresh_updates_badges(self, qtbot, monkeypatch):
        widget = PerformanceCombo()
        qtbot.addWidget(widget)

        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: True)
        widget.refresh()

        heavy_index = widget._combo.findData(HEAVY.key)
        assert "설치됨" in widget._combo.itemText(heavy_index)


class TestModelManagerDialog:
    def test_light_row_shows_installed_and_bundled_note(self, qtbot):
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        row = dialog.rows[LIGHT.key]
        assert "설치됨" in row._badge.text()
        assert "프로그램 포함" in row._badge.text()

    def test_heavy_row_shows_pending_with_explanation(self, qtbot):
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        row = dialog.rows[HEAVY.key]
        assert row._badge.text() == "준비 중"
        assert "ONNX" in row._note.text()

    def test_heavy_download_button_disabled(self, qtbot):
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.rows[HEAVY.key]._download_btn.isEnabled() is False

    def test_heavy_folder_button_disabled_when_not_installed(self, qtbot):
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.rows[HEAVY.key]._folder_btn.isEnabled() is False

    def test_light_folder_button_enabled_when_installed(self, qtbot):
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.rows[LIGHT.key]._folder_btn.isEnabled() is True

    def test_slm_placeholder_note_present(self, qtbot):
        from PySide6.QtWidgets import QLabel

        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        placeholder = dialog.findChild(QLabel, "ModelManagerPlaceholder")
        assert placeholder is not None
        assert "Phase 7" in placeholder.text()

    def test_refresh_reflects_newly_installed_state(self, qtbot, monkeypatch):
        """상태를 하드코딩하지 않고 실제로 재검사해야 한다."""
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)

        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: True)
        dialog.refresh()

        assert "설치됨" in dialog.rows[HEAVY.key]._badge.text()
        assert dialog.rows[HEAVY.key]._folder_btn.isEnabled() is True

    def test_focus_profile_focuses_that_row(self, qtbot):
        """콤보박스에서 미설치 옵션을 고르면 해당 행에 포커스가 가야 한다 (PLAN §4-C)."""
        dialog = ModelManagerDialog(focus_profile=HEAVY.key)
        qtbot.addWidget(dialog)
        with qtbot.waitExposed(dialog):
            dialog.show()
        assert dialog.focusWidget() is dialog.rows[HEAVY.key]

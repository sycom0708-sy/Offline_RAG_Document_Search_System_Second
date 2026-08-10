"""검색 옵션·PC 성능·모델 관리 위젯 테스트 (T4.7~T4.11b)."""

from __future__ import annotations

from config.settings import HEAVY, LIGHT, SLM_MINIMUM, SLM_RECOMMENDED
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

    def test_ai_summary_toggle_disabled_until_model_installed(self, qtbot):
        """Phase 7: placeholder는 걷혔지만 **모델이 없으면 여전히 비활성**이다.

        DESIGN §4.2 결정("켜도 아무 일이 없으면 고장으로 보인다")은 Phase 7
        이후에도 유효하다 — 모델 없이 켤 수 있게 두면 매번 실패 메시지만 뜬다.
        """
        widget = SearchOptions()
        qtbot.addWidget(widget)
        assert widget.ai_summary.isEnabled() is False
        assert "모델 관리" in widget.ai_summary.toolTip()

    def test_ai_summary_toggle_opens_when_model_available(self, qtbot):
        widget = SearchOptions()
        qtbot.addWidget(widget)
        widget.set_ai_summary_available(True)
        assert widget.ai_summary.isEnabled() is True

        received = []
        widget.ai_summary_changed.connect(received.append)
        widget.ai_summary.setChecked(True)
        assert received == [True]
        assert widget.is_ai_summary() is True

    def test_losing_model_turns_the_toggle_back_off(self, qtbot):
        """모델이 사라졌는데 켜진 상태가 남으면 "켜져 있는데 요약이 없는" 화면이 된다."""
        widget = SearchOptions()
        qtbot.addWidget(widget)
        widget.set_ai_summary_available(True)
        widget.ai_summary.setChecked(True)

        widget.set_ai_summary_available(False)
        assert widget.is_ai_summary() is False
        assert widget.ai_summary.isEnabled() is False

    def test_restoring_saved_state_is_ignored_without_model(self, qtbot):
        widget = SearchOptions()
        qtbot.addWidget(widget)
        widget.set_ai_summary(True)  # 저장된 상태가 ON이어도
        assert widget.is_ai_summary() is False  # 모델이 없으면 안 켜진다

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

    def test_shows_pending_badge_for_uninstalled_heavy(self, qtbot, monkeypatch):
        """Phase 7.5부터 KURE-v1이 실제로 설치될 수 있어 미설치를 명시적으로 강제한다."""
        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: False)
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

    def test_selecting_uninstalled_profile_requests_model_manager_and_reverts(self, qtbot, monkeypatch):
        """Option A 핵심: 미설치 선택 시 콤보는 현재 유효 프로파일로 되돌아간다."""
        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: False)
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

    def test_heavy_row_shows_not_installed_with_explanation(self, qtbot, monkeypatch):
        """Phase 7.5: 변환 파이프라인이 생겨 "준비 중"(미지원)이 아니라 "미설치"다."""
        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: False)
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        row = dialog.rows[HEAVY.key]
        assert row._badge.text() == "미설치"
        assert "변환" in row._note.text()

    def test_heavy_download_button_enabled_when_not_installed(self, qtbot, monkeypatch):
        """Phase 7.5부터 "설치 안내"가 변환 스크립트 사용법을 실제로 알려준다."""
        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: False)
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.rows[HEAVY.key]._download_btn.isEnabled() is True

    def test_heavy_download_button_disabled_when_installed(self, qtbot, monkeypatch):
        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: True)
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.rows[HEAVY.key]._download_btn.isEnabled() is False

    def test_heavy_folder_button_disabled_when_not_installed(self, qtbot, monkeypatch):
        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: False)
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.rows[HEAVY.key]._folder_btn.isEnabled() is False

    def test_heavy_install_guide_mentions_conversion_and_copy_paths(self, qtbot, monkeypatch):
        """설치 안내가 실제 스크립트 경로를 담고 있는지 — 문구만 있고 방법이 틀리면 무용지물이다."""
        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: False)
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)

        captured = {}
        monkeypatch.setattr(
            "ui.widgets.model_manager_dialog.QMessageBox.information",
            lambda *args, **kwargs: captured.setdefault("text", args[2] if len(args) > 2 else ""),
        )
        dialog.rows[HEAVY.key]._show_install_guide()

        assert "convert_kure" in captured["text"]
        assert str(HEAVY.local_dir) in captured["text"]

    def test_light_folder_button_enabled_when_installed(self, qtbot):
        dialog = ModelManagerDialog()
        qtbot.addWidget(dialog)
        assert dialog.rows[LIGHT.key]._folder_btn.isEnabled() is True

    def test_slm_section_lists_only_the_two_adopted_models(self, qtbot):
        """측정 후보는 4종이지만 제품이 권하는 것은 채택된 2종뿐이다 (Phase 7)."""
        dialog = ModelManagerDialog(verify_checksums=False)
        qtbot.addWidget(dialog)
        assert set(dialog.slm_rows) == {SLM_RECOMMENDED, SLM_MINIMUM}

    def test_slm_rows_show_spec_tier(self, qtbot):
        dialog = ModelManagerDialog(verify_checksums=False)
        qtbot.addWidget(dialog)
        assert "권장 사양" in dialog.slm_rows[SLM_RECOMMENDED]._note.text()
        assert "최소 사양" in dialog.slm_rows[SLM_MINIMUM]._note.text()

    def test_slm_download_button_is_enabled(self, qtbot):
        """임베딩 고성능과 달리 sLM은 다운로드 안내가 실제로 동작한다."""
        dialog = ModelManagerDialog(verify_checksums=False)
        qtbot.addWidget(dialog)
        assert dialog.slm_rows[SLM_RECOMMENDED]._download_btn.isEnabled() is True

    def test_refresh_reflects_newly_installed_state(self, qtbot, monkeypatch):
        """상태를 하드코딩하지 않고 실제로 재검사해야 한다."""
        dialog = ModelManagerDialog(verify_checksums=False)
        qtbot.addWidget(dialog)

        monkeypatch.setattr(type(HEAVY), "is_installed", lambda self: True)
        dialog.refresh()

        assert "설치됨" in dialog.rows[HEAVY.key]._badge.text()
        assert dialog.rows[HEAVY.key]._folder_btn.isEnabled() is True

    def test_focus_profile_focuses_that_row(self, qtbot):
        """콤보박스에서 미설치 옵션을 고르면 해당 행에 포커스가 가야 한다 (PLAN §4-C)."""
        dialog = ModelManagerDialog(focus_profile=HEAVY.key, verify_checksums=False)
        qtbot.addWidget(dialog)
        with qtbot.waitExposed(dialog):
            dialog.show()
        assert dialog.focusWidget() is dialog.rows[HEAVY.key]

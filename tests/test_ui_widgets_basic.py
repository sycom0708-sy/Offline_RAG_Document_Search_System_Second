"""기본 위젯 테스트 — toggle_switch, search_bar, format_filter (T4.2~T4.6)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from ui.widgets.format_filter import FormatFilter
from ui.widgets.search_bar import DEBOUNCE_MS, SearchBar
from ui.widgets.toggle_switch import ToggleSwitch


class TestToggleSwitch:
    def test_default_is_off(self, qtbot):
        toggle = ToggleSwitch("AI 요약 보기")
        qtbot.addWidget(toggle)
        assert toggle.isChecked() is False

    def test_click_toggles_state(self, qtbot):
        toggle = ToggleSwitch("대/소문자 구분")
        qtbot.addWidget(toggle)
        qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
        assert toggle.isChecked() is True

    def test_disabled_cannot_be_toggled_by_click(self, qtbot):
        """AI 요약 보기는 Phase 7까지 비활성 상태로 둔다 (DESIGN §4.2 결정)."""
        toggle = ToggleSwitch("AI 요약 보기")
        qtbot.addWidget(toggle)
        toggle.setEnabled(False)
        qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
        assert toggle.isChecked() is False

    def test_variant_property_set_on_inner_checkbox_for_qss_targeting(self, qtbot):
        """QSS 선택자 `QCheckBox[variant="switch"]`가 실제 QCheckBox에 걸려야
        한다 — 합성 위젯(ToggleSwitch) 자체는 QCheckBox가 아니라 선택자가
        안 먹는다."""
        toggle = ToggleSwitch()
        qtbot.addWidget(toggle)
        assert toggle._checkbox.property("variant") == "switch"

    def test_label_comes_before_switch_matching_design_mockup(self, qtbot):
        """DESIGN §4.2: `AI 요약 보기      ( ●)  OFF` — 라벨이 왼쪽, 스위치가 오른쪽."""
        toggle = ToggleSwitch("대/소문자 구분")
        qtbot.addWidget(toggle)
        layout = toggle.layout()
        label_index = next(i for i in range(layout.count()) if layout.itemAt(i).widget() is toggle._label)
        checkbox_index = next(
            i for i in range(layout.count()) if layout.itemAt(i).widget() is toggle._checkbox
        )
        assert label_index < checkbox_index

    def test_clicking_row_toggles_without_double_firing(self, qtbot):
        """행 전체가 클릭 영역이다. 클릭 좌표가 내부 체크박스 위여도
        이중 토글(핸들러 두 번 실행)이 나면 안 된다."""
        toggle = ToggleSwitch("일치되는 단어")
        toggle.resize(200, 24)
        qtbot.addWidget(toggle)

        events = []
        toggle.toggled.connect(events.append)

        qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)  # 라벨/여백 영역 클릭
        assert events == [True]

        qtbot.mouseClick(toggle._checkbox, Qt.MouseButton.LeftButton)  # 체크박스 자체 클릭
        assert events == [True, False]

    def test_disabled_row_click_does_not_toggle(self, qtbot):
        toggle = ToggleSwitch("AI 요약 보기")
        toggle.setEnabled(False)
        qtbot.addWidget(toggle)
        qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
        assert toggle.isChecked() is False


class TestSearchBar:
    def test_placeholder_matches_design_spec(self, qtbot):
        bar = SearchBar()
        qtbot.addWidget(bar)
        assert bar._input.placeholderText() == "계약서 검토 기준이 뭐였지"

    def test_enter_triggers_immediate_search(self, qtbot):
        bar = SearchBar()
        qtbot.addWidget(bar)
        received = []
        bar.search_requested.connect(received.append)

        bar.set_text("계약서 검토")
        qtbot.keyClick(bar._input, Qt.Key.Key_Return)

        assert received == ["계약서 검토"]

    def test_typing_debounces_before_emitting(self, qtbot):
        """`qtbot.keyClicks`는 US 키보드 레이아웃으로 키 이벤트를 합성하는데,
        한글은 대응하는 가상 키가 없어 멈춘다(실측 확인) — 실제 IME 입력이
        아니라 테스트 도구의 한계이므로, `setText()`로 textChanged를 직접
        트리거해 debounce 로직 자체를 검증한다."""
        bar = SearchBar()
        qtbot.addWidget(bar)
        received = []
        bar.search_requested.connect(received.append)

        bar.set_text("계약")
        assert received == []  # debounce 대기 중이라 아직 안 나감

        qtbot.wait(DEBOUNCE_MS + 150)
        assert received == ["계약"]

    def test_rapid_typing_only_emits_once_with_final_text(self, qtbot):
        bar = SearchBar()
        qtbot.addWidget(bar)
        received = []
        bar.search_requested.connect(received.append)

        bar.set_text("계")
        qtbot.wait(50)
        bar.set_text("계약")
        qtbot.wait(50)
        bar.set_text("계약서")

        qtbot.wait(DEBOUNCE_MS + 150)
        assert received == ["계약서"]  # 중간 상태(계, 계약)는 나가지 않아야 함

    def test_clear_empties_input(self, qtbot):
        bar = SearchBar()
        qtbot.addWidget(bar)
        bar.set_text("무언가")
        bar.clear()
        assert bar.text() == ""


class TestFormatFilter:
    def test_default_selection_is_all(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        assert widget.selected_extensions() is None
        assert widget._all_checkbox.isChecked() is True

    def test_set_available_formats_creates_checkboxes(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats([".docx", ".pdf", ".xlsx"])
        assert set(widget._format_checkboxes) == {".docx", ".pdf", ".xlsx"}

    def test_checking_individual_format_unchecks_all(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats([".docx", ".pdf"])

        widget._format_checkboxes[".docx"].setChecked(True)

        assert widget._all_checkbox.isChecked() is False
        assert widget.selected_extensions() == {".docx"}

    def test_unchecking_all_individual_formats_reverts_to_all(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats([".docx", ".pdf"])

        widget._format_checkboxes[".docx"].setChecked(True)
        widget._format_checkboxes[".docx"].setChecked(False)

        assert widget._all_checkbox.isChecked() is True
        assert widget.selected_extensions() is None

    def test_checking_all_unchecks_individual_formats(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats([".docx", ".pdf"])
        widget._format_checkboxes[".docx"].setChecked(True)

        widget._all_checkbox.setChecked(True)

        assert all(not cb.isChecked() for cb in widget._format_checkboxes.values())

    def test_unchecking_all_with_no_formats_snaps_back(self, qtbot):
        """개별 항목이 하나도 없는데 전체를 끄면 0건 상태가 되므로 되돌린다."""
        widget = FormatFilter()
        qtbot.addWidget(widget)

        widget._all_checkbox.setChecked(False)

        assert widget._all_checkbox.isChecked() is True

    def test_selection_changed_emits_on_format_toggle(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats([".docx", ".pdf"])

        received = []
        widget.selection_changed.connect(received.append)
        widget._format_checkboxes[".pdf"].setChecked(True)

        assert received == [{".pdf"}]

    def test_multiple_individual_selections_combine(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats([".docx", ".pdf", ".xlsx"])

        widget._format_checkboxes[".docx"].setChecked(True)
        widget._format_checkboxes[".pdf"].setChecked(True)

        assert widget.selected_extensions() == {".docx", ".pdf"}

    def test_reloading_formats_resets_to_all(self, qtbot):
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats([".docx"])
        widget._format_checkboxes[".docx"].setChecked(True)

        widget.set_available_formats([".docx", ".pdf"])

        assert widget.selected_extensions() is None

"""기본 위젯 테스트 — toggle_switch, input_bar, format_filter (T4.2~T4.6, Phase 7.7)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from ui.widgets.format_filter import FormatFilter
from ui.widgets.search_bar import PLACEHOLDER, InputBar
from ui.widgets.toggle_switch import ToggleSwitch, _SwitchIndicator


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

    def test_inner_control_is_custom_painted_switch(self, qtbot):
        """알약 배경 + 슬라이딩 손잡이는 QSS `::indicator`로 표현할 수 없어
        `_SwitchIndicator`가 직접 페인팅한다 — 내부 컨트롤이 그 타입인지 확인."""
        toggle = ToggleSwitch()
        qtbot.addWidget(toggle)
        assert isinstance(toggle._checkbox, _SwitchIndicator)

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


class TestInputBar:
    """Phase 7.7: 검색 결과 모드·챗봇 모드가 공유하는 하단 입력창.

    300ms debounce는 폐기했다 — Enter·보내기 버튼으로만 제출한다
    [사용자 확정, 2026-08-13].
    """

    def test_placeholder_matches_concept_mockup(self, qtbot):
        bar = InputBar()
        qtbot.addWidget(bar)
        assert bar._input.placeholderText() == PLACEHOLDER

    def test_enter_emits_submitted(self, qtbot):
        bar = InputBar()
        qtbot.addWidget(bar)
        received = []
        bar.submitted.connect(received.append)

        bar.set_text("계약서 검토")
        qtbot.keyClick(bar._input, Qt.Key.Key_Return)

        assert received == ["계약서 검토"]

    def test_submit_clears_the_input(self, qtbot):
        """보낸 메시지가 입력창에 남아 있으면 챗봇 모드에서 어색하다 — 검색
        모드에서는 대신 ResultHeader가 현재 검색어를 보여준다."""
        bar = InputBar()
        qtbot.addWidget(bar)

        bar.set_text("계약서 검토")
        qtbot.keyClick(bar._input, Qt.Key.Key_Return)

        assert bar.text() == ""

    def test_send_button_click_emits_submitted(self, qtbot):
        bar = InputBar()
        qtbot.addWidget(bar)
        received = []
        bar.submitted.connect(received.append)

        bar.set_text("리눅스")
        qtbot.mouseClick(bar._send_button, Qt.MouseButton.LeftButton)

        assert received == ["리눅스"]

    def test_typing_alone_does_not_emit(self, qtbot):
        """debounce가 없으므로 텍스트만 바꿔서는 아무 것도 나가지 않는다."""
        bar = InputBar()
        qtbot.addWidget(bar)
        received = []
        bar.submitted.connect(received.append)

        bar.set_text("계약서")
        qtbot.wait(400)

        assert received == []

    def test_submit_text_sets_and_emits_immediately(self, qtbot):
        """최근 검색 항목 클릭 시 쓰는 헬퍼 — 텍스트를 넣고 즉시 제출한다."""
        bar = InputBar()
        qtbot.addWidget(bar)
        received = []
        bar.submitted.connect(received.append)

        bar.submit_text("서류검증")

        assert received == ["서류검증"]

    def test_clear_empties_input(self, qtbot):
        bar = InputBar()
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

    def test_checkboxes_fill_two_columns_top_to_bottom(self, qtbot):
        """세로 우선 채우기(Phase 7.7, 사용자 확정) — 알파벳순 목록을 반으로
        잘라 왼쪽 열을 먼저 채우고 오른쪽 열로 넘어간다. 8개 형식 기준
        doc/docx/hwp/hwpx가 왼쪽 열, pdf/txt/xls/xlsx가 오른쪽 열에 온다."""
        widget = FormatFilter()
        qtbot.addWidget(widget)
        widget.set_available_formats(
            [".doc", ".docx", ".hwp", ".hwpx", ".pdf", ".txt", ".xls", ".xlsx"]
        )

        grid = widget._grid
        positions = {
            ext: (grid.getItemPosition(grid.indexOf(cb))[0], grid.getItemPosition(grid.indexOf(cb))[1])
            for ext, cb in widget._format_checkboxes.items()
        }

        assert positions[".doc"] == (0, 0)
        assert positions[".docx"] == (1, 0)
        assert positions[".hwp"] == (2, 0)
        assert positions[".hwpx"] == (3, 0)
        assert positions[".pdf"] == (0, 1)
        assert positions[".txt"] == (1, 1)
        assert positions[".xls"] == (2, 1)
        assert positions[".xlsx"] == (3, 1)

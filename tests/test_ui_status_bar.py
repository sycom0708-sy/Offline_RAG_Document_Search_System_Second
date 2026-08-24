"""상태바·폴더 관리 다이얼로그 테스트 (T4.16~T4.17)."""

from __future__ import annotations

from datetime import datetime, timedelta

from ui.widgets.folder_dialog import FolderDialog, NO_FOLDER_TEXT
from ui.widgets.indexing_progress_dialog import IndexingProgressDialog
from ui.widgets.status_bar import StatusBar, format_relative_time


class TestFormatRelativeTime:
    def test_just_now(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        assert format_relative_time(now - timedelta(seconds=30), now=now) == "방금 전"

    def test_minutes_ago(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        assert format_relative_time(now - timedelta(minutes=10), now=now) == "10분 전"

    def test_boundary_59_seconds_is_just_now(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        assert format_relative_time(now - timedelta(seconds=59), now=now) == "방금 전"

    def test_boundary_60_seconds_is_one_minute(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        assert format_relative_time(now - timedelta(seconds=60), now=now) == "1분 전"

    def test_hours_ago(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        assert format_relative_time(now - timedelta(hours=3), now=now) == "3시간 전"

    def test_days_ago(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        assert format_relative_time(now - timedelta(days=2), now=now) == "2일 전"

    def test_week_or_more_shows_date(self):
        now = datetime(2026, 1, 10, 12, 0, 0)
        assert format_relative_time(datetime(2026, 1, 1, 12, 0, 0), now=now) == "2026-01-01"

    def test_future_timestamp_does_not_crash(self):
        """시계 오차 등으로 미래 시각이 들어와도 예외 없이 처리돼야 한다."""
        now = datetime(2026, 1, 1, 12, 0, 0)
        assert format_relative_time(now + timedelta(minutes=5), now=now) == "방금 전"


class TestStatusBar:
    def test_no_documents_shows_empty_message(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        bar.set_idle(0, None)
        assert bar._info_label.text() == "인덱싱된 문서가 없습니다"
        assert bar._progress.isVisibleTo(bar) is False

    def test_idle_shows_count_with_thousands_separator(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        bar.set_idle(1284, None)
        assert "1,284개" in bar._info_label.text()

    def test_idle_shows_relative_last_indexed_time(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        bar.set_idle(10, datetime.now() - timedelta(minutes=10))
        assert "10분 전" in bar._info_label.text()

    def test_indexing_progress_shows_counts_and_progress_bar(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        bar.set_indexing_progress(342, 1284)
        assert "342" in bar._info_label.text()
        assert "1,284" in bar._info_label.text()
        assert bar._progress.isVisibleTo(bar) is True
        assert bar._progress.value() == 342
        assert bar._progress.maximum() == 1284

    def test_returning_to_idle_hides_progress_bar(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        bar.set_indexing_progress(1, 10)
        bar.set_idle(10, None)
        assert bar._progress.isVisibleTo(bar) is False

    def test_warning_hidden_by_default(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        assert bar._warning_label.isVisibleTo(bar) is False

    def test_set_warning_shows_message(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        bar.set_warning("구버전 문서를 변환하지 못했습니다.")
        assert bar._warning_label.isVisibleTo(bar) is True
        assert "구버전 문서" in bar._warning_label.text()

    def test_set_warning_none_hides_it_again(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        bar.set_warning("문제 발생")
        bar.set_warning(None)
        assert bar._warning_label.isVisibleTo(bar) is False


class TestFolderDialog:
    def test_shows_no_folder_message_when_none(self, qtbot):
        dialog = FolderDialog(current_folder=None)
        qtbot.addWidget(dialog)
        assert NO_FOLDER_TEXT in dialog.folder_label.text()
        assert dialog.reindex_button.isEnabled() is False

    def test_shows_current_folder_when_set(self, qtbot):
        dialog = FolderDialog(current_folder=r"D:\사내 문서")
        qtbot.addWidget(dialog)
        assert r"D:\사내 문서" in dialog.folder_label.text()
        assert dialog.reindex_button.isEnabled() is True

    def test_reindex_emits_signal_with_folder_and_closes(self, qtbot):
        dialog = FolderDialog(current_folder=r"D:\사내 문서")
        qtbot.addWidget(dialog)

        received = []
        dialog.reindex_requested.connect(received.append)
        dialog.reindex_button.click()

        assert received == [r"D:\사내 문서"]
        assert dialog.result() == FolderDialog.DialogCode.Accepted

    def test_reindex_disabled_without_folder_does_nothing(self, qtbot):
        dialog = FolderDialog(current_folder=None)
        qtbot.addWidget(dialog)
        received = []
        dialog.reindex_requested.connect(received.append)

        dialog._start_reindex()  # 버튼이 비활성이라도 직접 호출 시 안전한지 확인

        assert received == []


class TestFolderDialogWatchToggle:
    """T8.5: 실시간 감시 토글 — 폴더 관리 다이얼로그에서 켜고 끈다."""

    def test_disabled_without_folder(self, qtbot):
        dialog = FolderDialog(current_folder=None)
        qtbot.addWidget(dialog)
        assert dialog.watch_toggle.isEnabled() is False
        assert dialog.watch_toggle.isChecked() is False

    def test_reflects_saved_state_when_folder_set(self, qtbot):
        dialog = FolderDialog(current_folder=r"D:\사내 문서", current_watch_enabled=True)
        qtbot.addWidget(dialog)
        assert dialog.watch_toggle.isEnabled() is True
        assert dialog.watch_toggle.isChecked() is True

    def test_selecting_a_folder_enables_the_toggle(self, qtbot, monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: r"D:\새 폴더")

        dialog = FolderDialog(current_folder=None)
        qtbot.addWidget(dialog)
        assert dialog.watch_toggle.isEnabled() is False

        dialog._select_folder()

        assert dialog.watch_toggle.isEnabled() is True

    def test_toggling_emits_watch_toggled(self, qtbot):
        dialog = FolderDialog(current_folder=r"D:\사내 문서")
        qtbot.addWidget(dialog)

        received = []
        dialog.watch_toggled.connect(received.append)
        dialog.watch_toggle.setChecked(True)

        assert received == [True]


class TestIndexingProgressDialog:
    """T10.4: 비모달 인덱싱 진행률 팝업 — TECH 4.6("메인 UI가 멈추지 않도록")을
    지키면서 취소 버튼을 추가한다."""

    def test_is_non_modal(self, qtbot):
        """exec()가 아니라 show()로 띄운다 — 검색·필터 조작을 막지 않아야 한다."""
        dialog = IndexingProgressDialog()
        qtbot.addWidget(dialog)
        assert dialog.isModal() is False

    def test_set_progress_updates_label_and_bar(self, qtbot):
        dialog = IndexingProgressDialog()
        qtbot.addWidget(dialog)
        dialog.set_progress(3, 10)
        assert "3" in dialog._info_label.text()
        assert "10" in dialog._info_label.text()
        assert dialog._progress.value() == 3
        assert dialog._progress.maximum() == 10

    def test_set_progress_shows_current_file(self, qtbot):
        dialog = IndexingProgressDialog()
        qtbot.addWidget(dialog)
        dialog.set_progress(3, 10, r"C:\문서\보고서.docx")
        assert "보고서.docx" in dialog._file_label.text()
        assert dialog._file_label.toolTip() == r"C:\문서\보고서.docx"  # 생략돼도 전체 경로는 툴팁으로

    def test_long_path_is_elided_not_wrapped(self, qtbot):
        """다이얼로그가 늘어나지 않도록 가운데를 생략(...)한다."""
        dialog = IndexingProgressDialog()
        qtbot.addWidget(dialog)
        long_path = "C:\\" + "매우긴폴더이름" * 20 + "\\파일.docx"
        dialog.set_progress(1, 1, long_path)
        assert len(dialog._file_label.text()) < len(long_path)
        assert "…" in dialog._file_label.text() or "..." in dialog._file_label.text()

    def test_stage_with_total_refills_the_bar(self, qtbot):
        """T10.46 — 파싱이 끝나 막대가 100%인 채로 "임베딩"만 표시되면 이미
        끝난 것처럼 보인다(실사용 보고). 임베딩 자신의 done/total로 다시
        채워야 한다.
        """
        dialog = IndexingProgressDialog()
        qtbot.addWidget(dialog)
        dialog.set_progress(19, 19)
        assert dialog._progress.value() == 19
        assert dialog._progress.maximum() == 19

        dialog.set_stage("임베딩", 128, 607)

        assert dialog._progress.value() == 128
        assert dialog._progress.maximum() == 607

    def test_stage_without_total_shows_marquee(self, qtbot):
        """총량을 아직 모르는 전환 직후는 불확정 표시로 "새 단계 시작"을 알린다."""
        dialog = IndexingProgressDialog()
        qtbot.addWidget(dialog)
        dialog.set_progress(19, 19)

        dialog.set_stage("임베딩", 0, 0)

        assert dialog._progress.maximum() == 0

    def test_cancel_button_emits_signal_and_disables_itself(self, qtbot):
        """두 번 눌러도 중복 요청이 안 나가야 한다."""
        dialog = IndexingProgressDialog()
        qtbot.addWidget(dialog)
        received = []
        dialog.cancel_requested.connect(lambda: received.append(1))

        dialog.cancel_button.click()

        assert received == [1]
        assert dialog.cancel_button.isEnabled() is False


class TestIndexingProgressDialogTiming:
    """T10.7: 경과/예상 남은 시간 — 가짜 시계로 결정론적으로 검증한다.

    실제 파일 처리 시간에 의존하면 느리고 flaky해진다(Phase 8이 시간 대신
    호출 횟수로 검증했던 것과 같은 이유).
    """

    def _clock(self, *times: float):
        it = iter(times)
        return lambda: next(it)

    def test_first_update_shows_only_elapsed(self, qtbot):
        dialog = IndexingProgressDialog(time_source=self._clock(100.0))
        qtbot.addWidget(dialog)

        dialog.set_progress(1, 10)

        assert dialog._time_label.text() == "경과 0초"

    def test_remaining_estimate_after_two_samples(self, qtbot):
        """1개 처리에 2초 걸렸다면(표본 구간 처리율 0.5개/초), 남은 8개(=10-2)는 16초로 추정돼야 한다."""
        dialog = IndexingProgressDialog(time_source=self._clock(0.0, 2.0))
        qtbot.addWidget(dialog)

        dialog.set_progress(1, 10)
        dialog.set_progress(2, 10)

        assert dialog._time_label.text() == "경과 2초 · 약 16초 남음"

    def test_no_estimate_when_total_unknown(self, qtbot):
        """total<=0은 총량 미확정(marquee) 상태다 — 남은 시간을 추정할 근거가 없다."""
        dialog = IndexingProgressDialog(time_source=self._clock(0.0, 2.0))
        qtbot.addWidget(dialog)

        dialog.set_progress(1, 0)
        dialog.set_progress(2, 0)

        assert "남음" not in dialog._time_label.text()

    def test_no_estimate_once_done_reaches_total(self, qtbot):
        dialog = IndexingProgressDialog(time_source=self._clock(0.0, 2.0))
        qtbot.addWidget(dialog)

        dialog.set_progress(1, 2)
        dialog.set_progress(2, 2)

        assert "남음" not in dialog._time_label.text()

    def test_estimate_reacts_to_recent_window_not_full_history(self, qtbot):
        """앞쪽에 느린 구간(구버전 포맷)이 있어도, 최근 구간이 빨라지면 그에
        맞춰 추정치가 줄어들어야 한다 — 전체 평균이었다면 계속 느리게 잡힌다.

        표본 창(`_RATE_WINDOW=5`)을 다 채우고 하나 더 넣어야 가장 느렸던
        첫 표본이 창에서 밀려난다 — 그래야 "최근 구간만 본다"는 설계가
        실제로 검증된다(창이 안 찼으면 그냥 전체 평균과 같아진다).
        """
        # 느린 구간: 파일 1개당 10초. 빠른 구간: 파일 1개당 1초.
        clock = self._clock(0.0, 10.0, 20.0, 21.0, 22.0, 23.0)
        dialog = IndexingProgressDialog(time_source=clock)
        qtbot.addWidget(dialog)

        dialog.set_progress(1, 10)  # t=0
        dialog.set_progress(2, 10)  # t=10 (느린 구간)
        dialog.set_progress(3, 10)  # t=20 (구간 전환, 표본 창이 꽉 참)
        dialog.set_progress(4, 10)  # t=21 (빠른 구간 — 가장 느렸던 첫 표본이 창에서 밀려남)
        dialog.set_progress(5, 10)  # t=22
        dialog.set_progress(6, 10)  # t=23

        # 표본 창엔 (10,2)~(23,6)만 남는다: 처리율 4개/13초 → 남은 4개는 13초.
        # 전체 평균(5개/23초≈0.217개/초)이었다면 남은 4개는 약 18초로 더 크게 나온다.
        assert dialog._time_label.text() == "경과 23초 · 약 13초 남음"


class TestFormatDuration:
    def test_seconds_only_under_a_minute(self):
        from ui.widgets.indexing_progress_dialog import _format_duration

        assert _format_duration(45) == "45초"

    def test_minutes_and_seconds_at_or_over_a_minute(self):
        from ui.widgets.indexing_progress_dialog import _format_duration

        assert _format_duration(65) == "1분 5초"

    def test_exact_minute_boundary(self):
        from ui.widgets.indexing_progress_dialog import _format_duration

        assert _format_duration(60) == "1분 0초"

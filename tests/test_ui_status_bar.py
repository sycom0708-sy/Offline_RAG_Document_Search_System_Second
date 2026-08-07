"""상태바·폴더 관리 다이얼로그 테스트 (T4.16~T4.17)."""

from __future__ import annotations

from datetime import datetime, timedelta

from ui.widgets.folder_dialog import FolderDialog, NO_FOLDER_TEXT
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

    def test_folder_button_exists(self, qtbot):
        bar = StatusBar()
        qtbot.addWidget(bar)
        assert bar.folder_button.text() == "폴더 관리"


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

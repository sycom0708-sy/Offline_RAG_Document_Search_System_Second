"""sLM 다운로드 다이얼로그 — 다운로드 시작 버튼 + 진행률 + 실패/완료 상태.

`download_slm()`을 가짜로 바꿔치기해 실제 네트워크 없이 진행률/성공/실패/
취소 경로를 검증한다. 실제 다운로드는 백그라운드 QThread에서 돌므로
`qtbot.waitUntil`로 비동기 완료를 기다린다.
"""

from __future__ import annotations

import threading

import pytest

from config.settings import SLM_RECOMMENDED, get_slm_profile
from slm.download import SlmDownloadCancelled, SlmDownloadError
from ui.widgets.slm_download_dialog import SlmDownloadDialog

PROFILE = get_slm_profile(SLM_RECOMMENDED)


def test_initial_state_shows_start_button_only(qtbot):
    dialog = SlmDownloadDialog(PROFILE)
    qtbot.addWidget(dialog)

    assert dialog._primary_btn.text() == "다운로드 시작"
    assert dialog._primary_btn.isEnabled()
    assert not dialog._progress_bar.isVisibleTo(dialog)
    assert not dialog._error_label.isVisibleTo(dialog)
    assert not dialog._success_label.isVisibleTo(dialog)


def test_clicking_start_shows_progress(qtbot, monkeypatch):
    def fake_download_slm(profile, *, on_progress=None, cancel_event=None, **kwargs):
        if on_progress is not None:
            on_progress(1_000_000, 2_000_000)
        # 여기서 멈춰 progress 상태를 유지 — 취소될 때까지 대기.
        while cancel_event is not None and not cancel_event.is_set():
            threading.Event().wait(0.01)
        raise SlmDownloadCancelled("취소됨")

    monkeypatch.setattr(
        "ui.widgets.slm_download_dialog.download_slm", fake_download_slm
    )

    dialog = SlmDownloadDialog(PROFILE)
    qtbot.addWidget(dialog)
    dialog._primary_btn.click()

    qtbot.waitUntil(lambda: dialog._progress_bar.isVisibleTo(dialog), timeout=2000)
    assert dialog._primary_btn.text() == "다운로드 중…"
    assert not dialog._primary_btn.isEnabled()

    dialog.close()  # closeEvent가 취소 신호를 보내고 스레드 정리까지 기다린다


def test_success_shows_done_state_and_emits_signal(qtbot, monkeypatch):
    def fake_download_slm(profile, *, on_progress=None, cancel_event=None, **kwargs):
        if on_progress is not None:
            on_progress(2_000_000, 2_000_000)
        return profile.local_path

    monkeypatch.setattr(
        "ui.widgets.slm_download_dialog.download_slm", fake_download_slm
    )

    dialog = SlmDownloadDialog(PROFILE)
    qtbot.addWidget(dialog)

    with qtbot.waitSignal(dialog.download_succeeded, timeout=2000):
        dialog._primary_btn.click()

    qtbot.waitUntil(lambda: dialog._success_label.isVisibleTo(dialog), timeout=2000)
    assert not dialog._primary_btn.isEnabled()


def test_failure_shows_error_message_and_allows_retry(qtbot, monkeypatch):
    def fake_download_slm(profile, *, on_progress=None, cancel_event=None, **kwargs):
        raise SlmDownloadError("네트워크 연결이 끊겼습니다")

    monkeypatch.setattr(
        "ui.widgets.slm_download_dialog.download_slm", fake_download_slm
    )

    dialog = SlmDownloadDialog(PROFILE)
    qtbot.addWidget(dialog)
    dialog._primary_btn.click()

    qtbot.waitUntil(lambda: dialog._error_label.isVisibleTo(dialog), timeout=2000)
    assert "네트워크 연결이 끊겼습니다" in dialog._error_label.text()
    assert dialog._primary_btn.text() == "다시 시도"
    assert dialog._primary_btn.isEnabled()


def test_closing_mid_download_sets_the_cancel_event(qtbot, monkeypatch):
    """T-new — 닫기가 진행 중인 다운로드를 조용히 취소한다(`.part`는 남는다).

    실제로 `cancel_event`가 세팅되는지까지 확인한다 — 안 그러면 백그라운드
    스레드가 다이얼로그 없이 계속 도는 채로 남을 수 있다.
    """
    received_cancel_event: dict[str, threading.Event] = {}

    def fake_download_slm(profile, *, on_progress=None, cancel_event=None, **kwargs):
        received_cancel_event["event"] = cancel_event
        while cancel_event is not None and not cancel_event.is_set():
            threading.Event().wait(0.01)
        raise SlmDownloadCancelled("취소됨")

    monkeypatch.setattr(
        "ui.widgets.slm_download_dialog.download_slm", fake_download_slm
    )

    dialog = SlmDownloadDialog(PROFILE)
    qtbot.addWidget(dialog)
    dialog._primary_btn.click()

    qtbot.waitUntil(lambda: "event" in received_cancel_event, timeout=2000)
    dialog.close()

    assert received_cancel_event["event"].is_set()

"""중복 실행 방지 가드 테스트 (사용자 보고: 앱이 실제로 중복 실행되고 있었음).

실제 앱과 같은 키를 쓰면 테스트 실행 중 진짜로 떠 있는 앱과 충돌해 오탐이
나므로, 테스트마다 격리된 키를 넣는다.
"""

from __future__ import annotations

import sys
import uuid

from ui.app import _APP_MUTEX_NAME, _acquire_single_instance_guard, _create_app_mutex


def _unique_key() -> str:
    return f"ATECMobility.OfflineRAGSearch.Test.{uuid.uuid4().hex}"


class TestSingleInstanceGuard:
    def test_first_call_acquires_guard(self, qapp):
        key = _unique_key()
        guard = _acquire_single_instance_guard(key)
        try:
            assert guard is not None
        finally:
            if guard is not None:
                guard.detach()

    def test_second_call_with_same_key_returns_none(self, qapp):
        key = _unique_key()
        first = _acquire_single_instance_guard(key)
        try:
            assert first is not None
            second = _acquire_single_instance_guard(key)
            assert second is None
        finally:
            if first is not None:
                first.detach()

    def test_after_detach_key_is_free_again(self, qapp):
        key = _unique_key()
        first = _acquire_single_instance_guard(key)
        assert first is not None
        first.detach()

        second = _acquire_single_instance_guard(key)
        try:
            assert second is not None
        finally:
            if second is not None:
                second.detach()

    def test_different_keys_do_not_collide(self, qapp):
        first = _acquire_single_instance_guard(_unique_key())
        second = _acquire_single_instance_guard(_unique_key())
        try:
            assert first is not None
            assert second is not None
        finally:
            if first is not None:
                first.detach()
            if second is not None:
                second.detach()


class TestAppMutex:
    """인스톨러(`deploy/installer.iss`의 `AppMutex`)가 감지하는 뮤텍스.

    실행 중인 앱 위에 재설치하면 로드된 DLL을 덮어쓰다 충돌하던 문제
    (실사용 중 발견, 2026-08-28)를 막으려고 추가했다 — Inno Setup이 실제로
    감지할 수 있는지는 이 뮤텍스가 Win32 `OpenMutexW`로 열리는지로 검증한다.
    """

    def test_creates_a_detectable_named_mutex(self):
        if sys.platform != "win32":
            return  # Windows 전용 — 다른 플랫폼에서는 그냥 통과
        import ctypes

        _create_app_mutex()
        handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, _APP_MUTEX_NAME)
        try:
            assert handle  # 0이 아니면 뮤텍스가 실제로 존재해 Inno Setup이 감지할 수 있다
        finally:
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)

    def test_calling_twice_does_not_raise(self):
        _create_app_mutex()
        _create_app_mutex()  # 이미 있는 뮤텍스를 다시 열어도 예외 없이 통과해야 한다

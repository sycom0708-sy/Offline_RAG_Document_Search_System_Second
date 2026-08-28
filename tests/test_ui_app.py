"""중복 실행 방지 가드 테스트 (사용자 보고: 앱이 실제로 중복 실행되고 있었음).

실제 앱과 같은 키를 쓰면 테스트 실행 중 진짜로 떠 있는 앱과 충돌해 오탐이
나므로, 테스트마다 격리된 키를 넣는다.
"""

from __future__ import annotations

import uuid

from ui.app import _acquire_single_instance_guard


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

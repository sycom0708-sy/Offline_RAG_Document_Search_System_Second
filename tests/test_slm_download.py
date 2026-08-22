"""sLM 다운로드 진행률·취소 콜백 테스트 (모델 관리 다이얼로그 다운로드 버튼).

실제 네트워크 없이 `urllib.request.urlopen`을 가짜 응답으로 바꿔치기한다.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from slm.download import SlmDownloadCancelled, SlmDownloadError, download_file


class _FakeResponse:
    """`urllib.request.urlopen()`이 돌려주는 응답 객체의 최소 흉내."""

    def __init__(self, chunks: list[bytes], *, status: int = 200, total: int | None = None):
        self._chunks = list(chunks)
        self.status = status
        self._headers = {"Content-Length": str(total)} if total is not None else {}

    @property
    def headers(self):
        return self._headers

    def read(self, size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, response_factory):
    """HEAD(용량 확인)과 GET(본문) 요청을 구분해 각각 가짜 응답을 준다."""
    import slm.download as download_module

    def fake_urlopen(request, timeout=None):
        if request.get_method() == "HEAD":
            return response_factory(head=True)
        return response_factory(head=False)

    monkeypatch.setattr(download_module.urllib.request, "urlopen", fake_urlopen)


def test_on_progress_receives_cumulative_bytes(tmp_path, monkeypatch):
    payload = [b"a" * 10, b"b" * 10, b"c" * 5]
    total = sum(len(c) for c in payload)

    def factory(head):
        if head:
            return _FakeResponse([], total=total)
        return _FakeResponse(list(payload), status=200, total=total)

    _patch_urlopen(monkeypatch, factory)

    seen: list[tuple[int, int | None]] = []
    dest = tmp_path / "model.gguf"
    download_file(
        "https://example.com/model.gguf", dest, quiet=True,
        on_progress=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(10, total), (20, total), (25, total)]
    assert dest.read_bytes() == b"a" * 10 + b"b" * 10 + b"c" * 5


def test_cancel_event_stops_and_keeps_partial_file(tmp_path, monkeypatch):
    payload = [b"a" * 10, b"b" * 10, b"c" * 10]

    def factory(head):
        if head:
            return _FakeResponse([], total=30)
        return _FakeResponse(list(payload), status=200, total=30)

    _patch_urlopen(monkeypatch, factory)

    cancel_event = threading.Event()
    dest = tmp_path / "model.gguf"

    def on_progress(done, total):
        if done >= 10:  # 첫 블록을 받자마자 취소 요청
            cancel_event.set()

    with pytest.raises(SlmDownloadCancelled):
        download_file(
            "https://example.com/model.gguf", dest, quiet=True,
            on_progress=on_progress, cancel_event=cancel_event,
        )

    assert not dest.is_file()  # 최종 파일로는 아직 이동 안 됨
    part = dest.with_suffix(dest.suffix + ".part")
    assert part.read_bytes() == b"a" * 10  # 받은 만큼은 남아 있다


def test_resumes_from_partial_file_after_cancel(tmp_path, monkeypatch):
    """취소로 남은 `.part`를 다음 호출이 이어받는다."""
    dest = tmp_path / "model.gguf"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"a" * 10)

    requested_ranges = []

    def factory(head):
        if head:
            return _FakeResponse([], total=30)
        return _FakeResponse([b"b" * 10, b"c" * 10], status=206, total=30)

    import slm.download as download_module

    def fake_urlopen(request, timeout=None):
        if request.get_method() == "HEAD":
            return factory(head=True)
        requested_ranges.append(request.get_header("Range"))
        return factory(head=False)

    monkeypatch.setattr(download_module.urllib.request, "urlopen", fake_urlopen)

    download_file("https://example.com/model.gguf", dest, quiet=True)

    assert requested_ranges == ["bytes=10-"]
    assert dest.read_bytes() == b"a" * 10 + b"b" * 10 + b"c" * 10


def test_network_error_raises_download_error_not_cancelled(tmp_path, monkeypatch):
    import slm.download as download_module
    import urllib.error

    def fake_urlopen(request, timeout=None):
        if request.get_method() == "HEAD":
            return _FakeResponse([], total=30)
        raise urllib.error.URLError("연결 끊김")

    monkeypatch.setattr(download_module.urllib.request, "urlopen", fake_urlopen)

    dest = tmp_path / "model.gguf"
    with pytest.raises(SlmDownloadError) as exc_info:
        download_file("https://example.com/model.gguf", dest, quiet=True)

    assert not isinstance(exc_info.value, SlmDownloadCancelled)

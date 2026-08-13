"""썸네일 캐시 무효화 테스트 (Phase 8, T8.4)."""

from __future__ import annotations

from ui import thumbnail_cache


def test_evict_thumbnails_removes_cached_files(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail_cache, "THUMBNAIL_DIR", tmp_path / "thumbs")
    thumbnail_cache.THUMBNAIL_DIR.mkdir(parents=True)

    kept = thumbnail_cache.THUMBNAIL_DIR / "keep_me.png"
    kept.write_bytes(b"png")
    evicted_path = thumbnail_cache.THUMBNAIL_DIR / f"{thumbnail_cache._safe_name('doc1_image_00000')}.png"
    evicted_path.write_bytes(b"png")

    thumbnail_cache.evict_thumbnails(["doc1_image_00000"])

    assert not evicted_path.exists()
    assert kept.exists()  # 요청하지 않은 캐시는 그대로 남는다


def test_evict_thumbnails_ignores_missing_files(tmp_path, monkeypatch):
    """캐시가 애초에 없던 chunk_id를 넘겨도 예외 없이 조용히 넘어간다."""
    monkeypatch.setattr(thumbnail_cache, "THUMBNAIL_DIR", tmp_path / "thumbs")

    thumbnail_cache.evict_thumbnails(["never_cached_00000"])  # 예외가 나면 테스트 실패

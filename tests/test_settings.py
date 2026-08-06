"""모델 프로파일·설정 테스트 (T3.7)."""

from __future__ import annotations

import pytest

from config import settings
from config.settings import (
    HEAVY,
    LIGHT,
    PROFILE_ORDER,
    PROFILES,
    SIMILARITY_THRESHOLD,
    get_profile,
)


def test_default_profile_is_light():
    """최소 사양이 기본이어야 안전하다 (TECH 8장)."""
    assert get_profile().key == LIGHT.key


def test_profile_selected_by_argument():
    assert get_profile(HEAVY.key).key == HEAVY.key


def test_profile_selected_by_env(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_PROFILE", HEAVY.key)
    assert get_profile().key == HEAVY.key


def test_argument_beats_env(monkeypatch):
    monkeypatch.setenv("RAG_MODEL_PROFILE", HEAVY.key)
    assert get_profile(LIGHT.key).key == LIGHT.key


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="알 수 없는 모델 프로파일"):
        get_profile("존재하지-않는-모델")


def test_threshold_matches_tech_spec():
    """TECH 5.3의 예시 임계값. DESIGN §5.6 흐림 처리와 Phase 7 sLM 차단이 함께 쓴다."""
    assert SIMILARITY_THRESHOLD == 0.5


def test_profiles_have_distinct_dimensions():
    """차원이 다르면 벡터를 섞어 쓸 수 없다 — store가 model 키로 구분하는 근거."""
    assert LIGHT.dim != HEAVY.dim


def test_model_paths_are_relative_to_project_root():
    """TECH 9.1 포터블 원칙: 절대 경로를 코드에 박지 않는다."""
    for profile in PROFILES.values():
        assert profile.local_dir.is_relative_to(settings.PROJECT_ROOT)


def test_profile_order_covers_all_profiles():
    assert set(PROFILE_ORDER) == set(PROFILES)


def test_is_installed_false_when_files_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MODELS_DIR", tmp_path)
    # local_dir은 MODELS_DIR을 참조하므로 새 프로파일을 만들어 확인한다.
    from dataclasses import replace

    profile = replace(LIGHT)
    monkeypatch.setattr(type(profile), "local_dir", property(lambda self: tmp_path / self.key))
    assert profile.is_installed() is False

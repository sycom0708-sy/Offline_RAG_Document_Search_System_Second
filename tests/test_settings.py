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
    SLM_MINIMUM,
    SLM_RECOMMENDED,
    get_profile,
    slm_for_model_profile,
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


def test_slm_matches_the_pc_performance_choice():
    """T6.8 실측 결론(경량→EXAONE, 권장→Qwen)이 실제로 코드에 반영돼 있는지."""
    assert slm_for_model_profile(LIGHT.key) == SLM_MINIMUM
    assert slm_for_model_profile(HEAVY.key) == SLM_RECOMMENDED


def test_profile_order_covers_all_profiles():
    assert set(PROFILE_ORDER) == set(PROFILES)


class TestProjectRootUnderPyInstaller:
    """`sys.frozen`일 때 exe 위치를 기준으로 잡는다 (T9.2).

    PyInstaller onedir 빌드는 `config/settings.py`를 exe와 다른 위치
    (`_internal/`)에 번들한다 — `__file__` 기준으로 계산하면 `models/`·
    `vendor/`를 exe 옆이 아니라 `_internal/` 안에서 찾게 돼 배포 폴더
    구조(exe와 같은 레벨)와 어긋난다.
    """

    def test_dev_mode_uses_file_location(self):
        assert not hasattr(settings.sys, "frozen") or not settings.sys.frozen
        assert settings._project_root() == settings.PROJECT_ROOT

    def test_frozen_mode_uses_executable_location(self, tmp_path, monkeypatch):
        fake_exe = tmp_path / "OfflineRAGSearch" / "OfflineRAGSearch.exe"
        fake_exe.parent.mkdir(parents=True)
        fake_exe.touch()

        monkeypatch.setattr(settings.sys, "frozen", True, raising=False)
        monkeypatch.setattr(settings.sys, "executable", str(fake_exe))

        assert settings._project_root() == fake_exe.parent


def test_is_installed_false_when_files_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MODELS_DIR", tmp_path)
    # local_dir은 MODELS_DIR을 참조하므로 새 프로파일을 만들어 확인한다.
    from dataclasses import replace

    profile = replace(LIGHT)
    monkeypatch.setattr(type(profile), "local_dir", property(lambda self: tmp_path / self.key))
    assert profile.is_installed() is False


# --- Phase 11-C: AI CPU 사용 모드 (DESIGN §14.5) -----------------------------


def test_auto_cpu_mode_leaves_the_decision_to_llama_cpp():
    """`auto`는 숫자를 계산하지 않고 `None`을 준다.

    Phase 6~7의 실측치가 전부 인자를 안 넘긴 상태에서 나온 값이라, 기본값이
    그 조건과 같아야 한다.
    """
    from config.settings import resolve_n_threads

    assert resolve_n_threads("auto") is None


def test_cpu_modes_scale_with_this_pc_core_count():
    from config.settings import resolve_n_threads

    assert resolve_n_threads("half", cpu_count=8) == 4
    assert resolve_n_threads("max", cpu_count=8) == 8


def test_cpu_modes_never_return_zero_threads():
    """1코어 PC에서 `half`가 0이 되면 llama-server가 못 뜬다."""
    from config.settings import resolve_n_threads

    assert resolve_n_threads("half", cpu_count=1) == 1


def test_unknown_cpu_mode_falls_back_to_auto():
    """옛 설정 파일이나 손으로 고친 값이 들어와도 앱이 멈추면 안 된다."""
    from config.settings import resolve_n_threads

    assert resolve_n_threads("무엇이든", cpu_count=8) is None

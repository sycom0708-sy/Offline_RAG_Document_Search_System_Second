"""AppState 영속화 테스트."""

from __future__ import annotations

from config.settings import HEAVY, LIGHT
from ui.state import AppState


def test_default_state_uses_light_profile_and_no_folder():
    state = AppState()
    assert state.model_profile == LIGHT.key
    assert state.target_folder is None


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "app_state.json"
    original = AppState(target_folder=r"D:\문서\사내자료", model_profile=HEAVY.key)
    original.save(path)

    loaded = AppState.load(path)
    assert loaded == original


def test_load_missing_file_returns_defaults(tmp_path):
    state = AppState.load(tmp_path / "없는파일.json")
    assert state == AppState()


def test_load_corrupted_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "app_state.json"
    path.write_text("{이건 유효한 JSON이 아님", encoding="utf-8")
    assert AppState.load(path) == AppState()


def test_load_ignores_unknown_fields_for_forward_compatibility(tmp_path):
    import json

    path = tmp_path / "app_state.json"
    path.write_text(
        json.dumps({"target_folder": "D:/x", "model_profile": LIGHT.key, "미래필드": 123}),
        encoding="utf-8",
    )
    state = AppState.load(path)
    assert state.target_folder == "D:/x"


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "app_state.json"
    AppState().save(path)
    assert path.is_file()


def test_save_preserves_korean_paths_without_escaping(tmp_path):
    path = tmp_path / "app_state.json"
    AppState(target_folder=r"D:\사내 공유 폴더\2024_계약서").save(path)
    raw = path.read_text(encoding="utf-8")
    assert "사내 공유 폴더" in raw  # ensure_ascii=False 확인

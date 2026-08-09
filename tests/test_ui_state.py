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


def test_load_then_save_without_path_writes_back_to_loaded_path(tmp_path):
    """`load(path=격리경로)` 후 `save()`를 인자 없이 불러도 진짜 STATE_PATH가
    아니라 그 격리 경로에 써야 한다.

    이게 안 되면(실제로 안 됐었다) 테스트가 실제 `data/app_state.json`을
    pytest 임시 폴더 경로로 덮어써 실사용 앱의 "대상 폴더"가 오염된다 —
    2026-08-09 실사용 중 실제로 겪었다.
    """
    isolated = tmp_path / "isolated_state.json"
    state = AppState.load(path=isolated)  # 파일이 없어도 _path는 기억한다
    state.target_folder = r"C:\어떤\폴더"

    state.save()  # 인자 없이 호출

    assert isolated.is_file()
    reloaded = AppState.load(path=isolated)
    assert reloaded.target_folder == r"C:\어떤\폴더"


def test_bare_constructor_defaults_to_real_state_path():
    """`AppState()`를 바로 만들면(로드 경유 없이) 여전히 기본 `STATE_PATH`를
    기억한다 — `load()`로 명시적 경로를 거쳤을 때만 그 경로가 우선한다.
    실제 앱(`MainWindow()` 기본 생성)이 기대하는 동작이다.
    """
    from ui.state import STATE_PATH

    assert AppState()._path == STATE_PATH


def test_explicit_save_path_still_overrides_remembered_path(tmp_path):
    """`save(path=...)`로 명시하면 기억해둔 경로보다 그쪽이 우선해야 한다."""
    remembered = tmp_path / "remembered.json"
    explicit = tmp_path / "explicit.json"
    state = AppState.load(path=remembered)

    state.save(explicit)

    assert explicit.is_file()
    assert not remembered.is_file()

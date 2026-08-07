"""앱 상태 영속화 — 대상 폴더·모델 프로파일 (T4.17).

Windows의 `QSettings` 기본 포맷은 레지스트리를 쓴다. TECH 9.1의 포터블
원칙(레지스트리 등 설치 PC 종속 요소 배제)에 어긋나므로 쓰지 않는다.
대신 프로젝트 상대 경로의 JSON 파일에 저장한다 — 폴더째 옮겨도 그대로
따라온다.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from config.settings import LIGHT, PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
STATE_PATH = DATA_DIR / "app_state.json"
DB_PATH = DATA_DIR / "index.sqlite3"


@dataclass
class AppState:
    target_folder: str | None = None
    model_profile: str = LIGHT.key

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> "AppState":
        """저장된 상태를 읽는다. 파일이 없거나 손상됐으면 기본값으로 시작한다."""
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()

        known_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        try:
            return cls(**filtered)
        except TypeError:
            return cls()

    def save(self, path: Path = STATE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

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

from config.settings import DEFAULT_SLM_PROFILE, LIGHT, PROJECT_ROOT

DATA_DIR = PROJECT_ROOT / "data"
STATE_PATH = DATA_DIR / "app_state.json"
DB_PATH = DATA_DIR / "index.sqlite3"
RECENT_SEARCHES_LIMIT = 10


@dataclass
class AppState:
    target_folder: str | None = None
    model_profile: str = LIGHT.key
    # AI 챗봇은 **기본 OFF**다 — 추출형 검색이 기본값이라는 설계 원칙
    # (PRD/DESIGN §1, TECH 5.2)이고, 명시적으로 켜야 하는 옵션이다.
    # Phase 7.6에서 `ai_summary_enabled`(자동 1회 요약)를 `ai_chat_enabled`
    # (챗봇 모드)로 리네임했다. 필드명이 바뀌어도 별도 마이그레이션은
    # 필요 없다 — `_load_raw()`가 미지 키를 걸러내므로 옛 값은 조용히
    # 기본값(False)으로 시작한다.
    ai_chat_enabled: bool = False
    slm_profile: str = DEFAULT_SLM_PROFILE
    # 실시간 폴더 감시(T8.5)도 **기본 OFF**다 — TECH 문서가 "리소스 부담
    # 고려, 최소 사양 기본 OFF 검토"로 명시한 옵트인 기능이다.
    folder_watch_enabled: bool = False
    # 사이드바 "최근 검색" 목록(Phase 7.7). 최신이 맨 앞, 최대
    # RECENT_SEARCHES_LIMIT건 — 목록 갱신은 ui.state 밖(MainWindow)에서 한다.
    recent_searches: list[str] = dataclasses.field(default_factory=list)
    # Phase 11: 사이드바 "검색/대화" 옆 확장 영역(문서 형식 · AI 챗봇 사용)의
    # 펼침 상태. 기본은 접힘 — 평소 화면을 비워두자는 것이 이 배치의 목적이다.
    search_expanded: bool = False
    # Phase 11: UI에서 뺐지만 **기능은 살아 있는** 검색 옵션(DESIGN §14.7).
    # `hybrid_search()`의 인자는 그대로 두고 값만 여기서 읽는다 — 이 파일을
    # 직접 고치면 켤 수 있고, 나중에 UI를 되살릴 때도 배선이 남아 있다.
    # 기본값은 둘 다 꺼짐으로, UI가 있던 시절의 기본 상태와 같다.
    case_sensitive: bool = False
    exact_word: bool = False

    def __post_init__(self) -> None:
        # 데이터클래스 필드가 아니라(asdict()에 안 실린다) — save()를 인자
        # 없이 불렀을 때 어디에 쓸지 기억하는 용도다. load()가 이 값을
        # 실제 읽은 경로로 덮어쓴다.
        self._path = STATE_PATH

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> "AppState":
        """저장된 상태를 읽는다. 파일이 없거나 손상됐으면 기본값으로 시작한다.

        반환된 인스턴스는 `path`를 기억해 뒀다가 `save()`를 인자 없이 불러도
        같은 파일에 저장한다 — 테스트가 격리된 경로로 `load()`해놓고
        `save()`만 인자 없이 부르면 실제 `STATE_PATH`를 덮어쓰던 문제를
        막는다(실측 확인: 테스트 스위트가 진짜 `data/app_state.json`을
        pytest 임시 경로로 오염시켰다).
        """
        instance = cls._load_raw(path)
        instance._path = path
        return instance

    @classmethod
    def _load_raw(cls, path: Path) -> "AppState":
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

    def add_recent_search(self, query: str) -> None:
        """검색어를 최근 검색 맨 앞에 올린다. 중복은 앞으로 옮기고,
        최대 `RECENT_SEARCHES_LIMIT`건에서 오래된 것부터 잘라낸다."""
        query = query.strip()
        if not query:
            return
        self.recent_searches = [query] + [q for q in self.recent_searches if q != query]
        del self.recent_searches[RECENT_SEARCHES_LIMIT:]

    def save(self, path: Path | None = None) -> None:
        target = path if path is not None else self._path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

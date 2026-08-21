from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fixtures.generate_samples import generate_all  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HWP_SAMPLE_ENV = "HWP_SAMPLE_PATH"


@pytest.fixture(autouse=True)
def _isolate_assets_dir(monkeypatch, tmp_path_factory):
    """`index_folder()`를 부르는 모든 테스트를 실제 `data/assets/`에서 격리한다 (Phase 11-D).

    🔴 `indexer/pipeline.py`는 `from config.settings import ASSETS_DIR`로
    값을 **임포트 시점에 지역 이름으로 복사**해 온다 — 그래서
    `config.settings.ASSETS_DIR`만 패치해서는 파이프라인에 안 먹고,
    `indexer.pipeline.ASSETS_DIR`을 직접 바꿔야 한다.

    이 fixture가 없으면 `index_folder()`를 부르는 모든 테스트가 이 PC의
    실제 프로젝트 `data/assets/`에 doc_id 폴더를 만든다 — 실측: 전체
    스위트 한 번에 31개 폴더·159KB가 생겼다. Phase 7.7의
    `data/app_state.json` 오염(테스트가 `AppState.save()`를 인자 없이 불러
    실제 상태 파일을 덮어쓴 문제), T10.5의 `data/index.sqlite3` 오염과
    같은 종류의 함정이다.
    """
    import indexer.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "ASSETS_DIR", tmp_path_factory.mktemp("assets_isolated"))


@pytest.fixture(autouse=True)
def _isolate_indexing_log(monkeypatch, tmp_path_factory):
    """`index_folder()`를 부르는 테스트를 실제 `data/logs/`에서 격리한다 (T10.36).

    `_isolate_assets_dir`와 같은 함정 — `get_logger()`는 `logging.getLogger()`가
    이름으로 전역 싱글턴을 돌려주는 데다 핸들러를 한 번만 붙이므로, 격리 없이
    두면 스위트에서 가장 먼저 로거를 만든 테스트가 실제 `data/logs/` 경로를
    영구히 고정해버린다. 매 테스트마다 핸들러를 지워 다음 `get_logger()`
    호출이 격리된 경로로 다시 만들게 한다.
    """
    import logging

    import indexer.index_log as index_log_module

    monkeypatch.setattr(index_log_module, "LOGS_DIR", tmp_path_factory.mktemp("logs_isolated"))
    logger = logging.getLogger(index_log_module._LOGGER_NAME)
    logger.handlers.clear()
    yield
    logger.handlers.clear()


def find_hwp_sample() -> Path | None:
    """.hwp는 코드로 생성할 수 없어 실제 파일에 의존한다.

    HWP_SAMPLE_PATH 환경변수 > 프로젝트 루트에 놓인 .hwp 순으로 찾는다.
    """
    from os import environ

    configured = environ.get(HWP_SAMPLE_ENV)
    if configured and Path(configured).is_file():
        return Path(configured)
    return next(iter(sorted(PROJECT_ROOT.glob("*.hwp"))), None)


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> dict[str, Path]:
    """세션당 한 번만 샘플 문서를 생성한다."""
    return generate_all(tmp_path_factory.mktemp("samples"))


@pytest.fixture(scope="session")
def sample_hwp() -> Path:
    path = find_hwp_sample()
    if path is None:
        pytest.skip(
            f"유효한 .hwp 샘플 없음 — 프로젝트 루트에 두거나 {HWP_SAMPLE_ENV} 환경변수로 지정하세요"
        )
    return path


@pytest.fixture(scope="session")
def embedder():
    """임베딩 모델은 용량이 커서 저장소에 없다 — 없으면 사유와 함께 스킵한다.

    LibreOffice·hwp 샘플과 같은 방식이다(Phase 1 패턴). 세션당 한 번만 만들어
    ONNX 세션 생성 비용을 나눠 쓴다.
    """
    from config.settings import get_profile

    profile = get_profile()
    if not profile.is_installed():
        pytest.skip(
            f"임베딩 모델 미설치 ({profile.local_dir}) — "
            f"`python -m indexer.vector.download` 실행 후 재시도"
        )

    from indexer.vector.embedder import Embedder

    return Embedder(profile)


@pytest.fixture(scope="session")
def heavy_embedder():
    """KURE-v1(권장 모드) 전용 — 없으면 스킵한다 (Phase 7.5).

    `embedder`는 기본 프로파일(LIGHT)에 고정돼 있어 CLS 풀링·8192 truncation
    처럼 HEAVY에서만 갈리는 동작은 별도 픽스처가 필요하다.
    """
    from config.settings import HEAVY
    from indexer.vector.embedder import Embedder

    if not HEAVY.is_installed():
        pytest.skip(
            f"KURE-v1 미설치 ({HEAVY.local_dir}) — "
            "`.venv-convert`에서 `python -m scripts.convert_kure` 실행 후 재시도"
        )
    return Embedder(HEAVY)


@pytest.fixture(scope="session")
def sample_txt(samples) -> Path:
    return samples["sample.txt"]


@pytest.fixture(scope="session")
def sample_pdf(samples) -> Path:
    return samples["sample.pdf"]


@pytest.fixture(scope="session")
def sample_docx(samples) -> Path:
    return samples["sample.docx"]


@pytest.fixture(scope="session")
def sample_xlsx(samples) -> Path:
    return samples["sample.xlsx"]


@pytest.fixture(scope="session")
def sample_pptx(samples) -> Path:
    return samples["sample.pptx"]


@pytest.fixture(scope="session")
def sample_hwpx(samples) -> Path:
    return samples["sample.hwpx"]


class FakeOpenFileWorker(QObject):
    """T10.1: `ui.open_file_worker.OpenFileWorker`를 대신하는 스파이.

    실제 스레드/COM을 돌리지 않는다 — `start()`는 시작 여부만 기록하고,
    테스트가 `failed`/`finished`를 직접 쏴서 배선(버튼 비활성화·재활성화,
    `open_failed` 릴레이)만 검증한다. `ui.widgets.card_common.OpenFileWorker`를
    이 클래스로 monkeypatch해서 쓴다.
    """

    failed = Signal(str)
    finished = Signal()
    instances: list["FakeOpenFileWorker"] = []

    def __init__(self, file_path, plan, parent=None) -> None:
        super().__init__(parent)
        self.file_path = file_path
        self.plan = plan
        self.started = False
        type(self).instances.append(self)

    def start(self) -> None:
        self.started = True

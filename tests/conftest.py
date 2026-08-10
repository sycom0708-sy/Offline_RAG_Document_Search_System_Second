from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fixtures.generate_samples import generate_all  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HWP_SAMPLE_ENV = "HWP_SAMPLE_PATH"


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
    """KURE-v1(고성능 모드) 전용 — 없으면 스킵한다 (Phase 7.5).

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

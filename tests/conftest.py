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

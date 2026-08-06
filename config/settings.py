"""사양별 모델 프로파일과 검색 상수 (T3.7).

TECH 8장의 "설정 파일로 경량/고성능 모드를 토글하는 단일 코드베이스" 요구를
구현한다. 모든 경로는 **프로젝트 루트 기준 상대 경로**로 계산해 TECH 9.1의
포터블 원칙(폴더 통째로 옮겨도 동작)을 지킨다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 이 파일 기준으로 프로젝트 루트를 잡는다 (절대 경로를 코드에 박지 않는다).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = PROJECT_ROOT / "models"

# 유사도 임계값 — 여러 곳에서 쓰이므로 여기 한 곳에서만 정의한다.
#   · DESIGN §5.6: 이 값 미만이면 결과 카드를 흐리게 + "관련성 낮음" 표시
#   · TECH 5.3 1단계 안전장치: Phase 7에서 이 값 미만이면 sLM을 호출하지 않음
SIMILARITY_THRESHOLD = 0.5

# 벡터 재순위에 넘길 FTS5 후보 개수. 크면 정확하지만 임베딩 조회 비용이 는다.
DEFAULT_CANDIDATE_LIMIT = 100


@dataclass(frozen=True)
class ModelProfile:
    """임베딩 모델 하나의 배포 단위."""

    key: str
    label: str
    repo_id: str
    onnx_file: str  # 레포 내 경로
    dim: int
    max_seq_length: int
    # 다운로드해야 하는 파일 목록 (레포 내 경로 → 로컬 저장 파일명)
    files: tuple[tuple[str, str], ...]

    @property
    def local_dir(self) -> Path:
        return MODELS_DIR / self.key

    @property
    def onnx_path(self) -> Path:
        return self.local_dir / "model.onnx"

    @property
    def tokenizer_path(self) -> Path:
        return self.local_dir / "tokenizer.json"

    def is_installed(self) -> bool:
        return self.onnx_path.is_file() and self.tokenizer_path.is_file()


# 경량 모드 (최소 사양 8GB, 기본값)
# int8 양자화 ONNX를 레포가 이미 제공하므로 torch 없이 바로 추론할 수 있다.
LIGHT = ModelProfile(
    key="ko-sroberta-multitask",
    label="경량 모드 (최소 사양)",
    repo_id="jhgan/ko-sroberta-multitask",
    onnx_file="onnx/model_qint8_avx512_vnni.onnx",
    dim=768,
    max_seq_length=128,
    files=(
        ("onnx/model_qint8_avx512_vnni.onnx", "model.onnx"),
        ("tokenizer.json", "tokenizer.json"),
    ),
)

# 고성능 모드 (권장 사양 16GB)
# KURE-v1은 ONNX를 제공하지 않아 torch 기반 변환이 선행돼야 한다(원본 2.27GB,
# int8 변환 후에도 약 570MB). TECH 9.2의 임베딩 예산(100~250MB)을 넘으므로
# TECH 9.3의 sLM과 동일하게 **분리 다운로드** 대상으로 둔다.
# Phase 3에서는 토글 구조만 열어두고 실검증은 경량 모델로 수행한다.
HEAVY = ModelProfile(
    key="KURE-v1",
    label="고성능 모드 (권장 사양)",
    repo_id="nlpai-lab/KURE-v1",
    onnx_file="onnx/model_int8.onnx",
    dim=1024,
    max_seq_length=512,
    files=(
        ("onnx/model_int8.onnx", "model.onnx"),
        ("tokenizer.json", "tokenizer.json"),
    ),
)

PROFILES: dict[str, ModelProfile] = {LIGHT.key: LIGHT, HEAVY.key: HEAVY}

# UI 콤보박스(DESIGN §4.3)가 쓰는 표시용 순서
PROFILE_ORDER = (LIGHT.key, HEAVY.key)

_ENV_PROFILE = "RAG_MODEL_PROFILE"


def get_profile(key: str | None = None) -> ModelProfile:
    """활성 모델 프로파일을 반환한다.

    우선순위: 인자 > 환경변수(RAG_MODEL_PROFILE) > 경량 모드(기본값).
    최소 사양을 기본으로 두는 것이 안전하다.
    """
    resolved = key or os.environ.get(_ENV_PROFILE) or LIGHT.key
    if resolved not in PROFILES:
        valid = ", ".join(PROFILES)
        raise ValueError(f"알 수 없는 모델 프로파일: {resolved} (사용 가능: {valid})")
    return PROFILES[resolved]

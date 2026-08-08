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


# --- sLM (AI 요약용 소형 언어 모델) — Phase 6 후보, Phase 7 채택 -----------

SLM_DIR = MODELS_DIR / "slm"


@dataclass(frozen=True)
class SlmProfile:
    """sLM 후보 하나의 배포 단위 (GGUF 단일 파일)."""

    key: str
    label: str
    repo_id: str
    gguf_file: str  # 레포 내 경로
    size_gb: float  # HF에서 확인한 실제 크기 (표시·용량 계획용)
    note: str = ""
    # Qwen3 계열의 thinking 모드처럼 측정 전에 꺼야 하는 옵션이 있으면 여기에.
    extra_server_args: tuple[str, ...] = ()

    @property
    def local_path(self) -> Path:
        return SLM_DIR / f"{self.key}.gguf"

    def is_installed(self) -> bool:
        return self.local_path.is_file()


# 후보 4종 — 2026-08 시점에 HF에서 실제 파일 크기를 확인한 것들이다.
# TASK 문서 원안(Qwen2.5-1.5B/7B, EXAONE-3.5, Phi-3.5-mini)은 2024년 기준이라
# 갱신했다. 특히 EXAONE-4.0-1.2B(0.81GB)는 문서 작성 시점에 없던 선택지로,
# 8GB 최소 사양에서 한국어 특화 모델을 쓸 수 있는지를 바꿔놓는다.
SLM_CANDIDATES: tuple[SlmProfile, ...] = (
    SlmProfile(
        key="exaone-4.0-1.2b",
        label="EXAONE 4.0 1.2B",
        repo_id="LGAI-EXAONE/EXAONE-4.0-1.2B-GGUF",
        gguf_file="EXAONE-4.0-1.2B-Q4_K_M.gguf",
        size_gb=0.81,
        note="LG AI Research 공식 GGUF, 한국어 특화. 최소 사양 주력 후보",
    ),
    SlmProfile(
        key="phi-4-mini",
        label="Phi-4-mini-instruct",
        repo_id="unsloth/Phi-4-mini-instruct-GGUF",
        gguf_file="Phi-4-mini-instruct-Q4_K_M.gguf",
        size_gb=2.49,
        note="Phi-3.5-mini 후속. 크기 대비 지시 준수가 강점으로 알려짐",
    ),
    SlmProfile(
        key="qwen3.5-4b",
        label="Qwen3.5 4B",
        repo_id="unsloth/Qwen3.5-4B-GGUF",
        gguf_file="Qwen3.5-4B-Q4_K_M.gguf",
        size_gb=2.74,
        note="Apache 2.0. thinking 모드가 기본 활성이라 --reasoning off로 끈다",
        # 실측: 끄지 않으면 300토큰을 전부 사고에 쓰고 130초 만에 **빈 응답**을
        # 돌려준다. `--reasoning-budget 0`은 효과가 없었고 `--reasoning off`만
        # 통했다(템플릿에 빈 <think></think>가 삽입되며 8토큰으로 정상 응답).
        extra_server_args=("--reasoning", "off"),
    ),
    SlmProfile(
        key="exaone-3.5-7.8b",
        label="EXAONE 3.5 7.8B",
        repo_id="lmstudio-community/EXAONE-3.5-7.8B-Instruct-GGUF",
        gguf_file="EXAONE-3.5-7.8B-Instruct-Q4_K_M.gguf",
        size_gb=4.77,
        note="한국어 특화. 권장 사양(16GB) 주력 후보",
    ),
)

SLM_PROFILES: dict[str, SlmProfile] = {p.key: p for p in SLM_CANDIDATES}
SLM_ORDER = tuple(p.key for p in SLM_CANDIDATES)


def get_slm_profile(key: str) -> SlmProfile:
    if key not in SLM_PROFILES:
        valid = ", ".join(SLM_ORDER)
        raise ValueError(f"알 수 없는 sLM 프로파일: {key} (사용 가능: {valid})")
    return SLM_PROFILES[key]

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

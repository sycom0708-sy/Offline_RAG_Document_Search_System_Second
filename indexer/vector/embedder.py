"""ONNX 기반 문장 임베딩 (T3.1, T3.2).

torch 없이 `onnxruntime` + `tokenizers`만으로 동작한다. 사용하는 모델이 int8
양자화 ONNX를 이미 배포하고 있어 변환 단계가 필요 없고, torch를 빼면 런타임이
117MB 가벼워져 TECH 9.2의 인스톨러 예산에 들어간다.

모델은 `last_hidden_state`만 내주므로 문장 벡터는 직접 만들어야 한다
(`modules.json` 기준 mean pooling):

    1. attention_mask로 패딩 토큰을 제외한 **가중 평균**
    2. L2 정규화 — 저장 시점에 해두면 검색 때 코사인 유사도가 내적 한 번으로 끝난다

## 양자화로 인한 재현성 한계

int8 **동적** 양자화 모델이라 활성값 스케일을 배치 텐서 전체로 잡는다. 그래서
같은 문장이라도 어떤 배치에 실려 들어갔느냐에 따라 벡터가 달라진다. 패딩 탓이
아니다 — 패딩이 0인 문장도 갈리고, 패딩을 늘리면 오히려 편차가 줄었다(실측).

**편차 크기는 CPU에 따라 다르다.** 자기 자신과의 코사인 유사도가 AVX-512/VNNI가
있는 Ultra 5 125U에서는 0.98대인데, VNNI가 없는 i5-8265U(최소 사양 기준기)에서는
실문서 300청크 기준 중앙 0.940 / 최소 0.868까지 벌어진다.

그래도 **검색 순위는 유지된다** — 실문서 300청크·질의 4건에서 배치 저장 벡터와
단건 벡터의 top1이 전부 같았고 top10 겹침은 8~10/10이었다.

🔴 **다른 PC에서 만든 인덱스를 가져다 쓰면 안 된다.** CPU가 다르면 같은 배치
경로로 만든 벡터끼리도 중앙 0.707(최소 0.556)까지 벌어져, 배치 편차(0.940)보다
훨씬 크다. 이 상태로 검색하면 top10 겹침이 4~8/10으로 무너진다. 인덱스는
**그 인덱스를 쓸 PC에서 생성**해야 한다(상세는 PLAN Phase 3).

**벡터는 비트 단위로 재현되지 않으므로** 저장 벡터를 해시로 비교해 변경을
감지하는 설계는 하면 안 된다(Phase 8 증분 갱신 시 주의).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from config.settings import ModelProfile, get_profile

# 🔴 모델이 지원하는 max_seq_length를 그대로 토크나이저 truncation에 쓰면 안
# 된다 — Phase 7.5에서 KURE-v1(max_seq_length=8192)로 실제 청크를 재인덱싱하다
# 실측했다. 표 청크는 chunker.py가 **의도적으로** 분할하지 않는다(구조 보존,
# Phase 1 결정) — 그래서 239행짜리 시트 하나가 13,625자 청크 하나로 그대로
# 들어간다. LIGHT(max_seq_length=128)에서는 이게 자동으로 128토큰까지만
# 잘려 문제가 드러나지 않았을 뿐이다. KURE-v1의 진짜 한계(8192)를 그대로 쓰면
# `tokenizer.enable_padding()`이 배치 안의 가장 긴 시퀀스에 맞춰 전부 패딩하므로,
# 표 청크 하나가 배치 전체를 8192로 끌어올려 어텐션 행렬이 수십 GB를 요구한다
# (실측: batch=16에서 68,719,476,736 bytes 요청, 메모리 할당 실패).
#
# 이 상한은 모델 성능과 무관하다 — 8192토큰짜리 표 하나를 벡터 하나로
# 뭉개는 것 자체가 검색 품질에도 의미가 없다(재순위는 세밀한 매칭이 목적).
# `ModelProfile.max_seq_length`는 모델의 실제 능력을 정확히 표시하는 메타데이터로
# 그대로 두고, 인코딩에 실제로 쓰는 길이만 이 상한으로 낮춘다.
_MAX_SAFE_ENCODE_TOKENS = 512


class ModelNotInstalledError(RuntimeError):
    """모델 파일이 없다."""


class Embedder:
    """문장 → 정규화된 벡터.

    ONNX 세션과 토크나이저를 처음 쓸 때 한 번만 만든다 — 세션 생성이 비싸서
    질의마다 새로 만들면 검색 응답이 눈에 띄게 느려진다.
    """

    def __init__(self, profile: ModelProfile | None = None, num_threads: int | None = None) -> None:
        self.profile = profile or get_profile()
        self._num_threads = num_threads
        self._session = None
        self._tokenizer = None

    # --- 지연 초기화 -------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return

        profile = self.profile
        if not profile.is_installed():
            raise ModelNotInstalledError(
                f"임베딩 모델이 없습니다: {profile.local_dir}\n"
                f"인터넷이 되는 PC에서 `python -m indexer.vector.download --profile {profile.key}`를 "
                "실행하거나, 받아둔 models/ 폴더를 복사하세요."
            )

        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        if self._num_threads:
            options.intra_op_num_threads = self._num_threads

        self._session = ort.InferenceSession(
            str(profile.onnx_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

        tokenizer = Tokenizer.from_file(str(profile.tokenizer_path))
        # 배치로 넣으려면 길이를 맞춰야 한다. 잘림 한도는 모델 사양을 따르되,
        # 메모리 안전 상한을 넘지 않는다 (모듈 상단 _MAX_SAFE_ENCODE_TOKENS 참고).
        tokenizer.enable_truncation(
            max_length=min(profile.max_seq_length, _MAX_SAFE_ENCODE_TOKENS)
        )
        tokenizer.enable_padding()
        self._tokenizer = tokenizer

    # --- 공개 API ---------------------------------------------------

    @property
    def dim(self) -> int:
        return self.profile.dim

    def count_tokens(self, text: str) -> int:
        """청커에 넘겨 토큰 기준으로 자르게 하는 용도 (특수 토큰 포함)."""
        self._ensure_loaded()
        return len(self._tokenizer.encode(text).ids)

    def encode(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        """문장 목록을 (n, dim) 정규화 벡터 배열로 만든다."""
        self._ensure_loaded()
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)

        outputs = [
            self._encode_batch(texts[i : i + batch_size])
            for i in range(0, len(texts), batch_size)
        ]
        return np.vstack(outputs)

    def encode_one(self, text: str) -> np.ndarray:
        """단일 문장 → (dim,) 벡터. 검색 질의용."""
        return self.encode([text])[0]

    # --- 내부 -------------------------------------------------------

    def _encode_batch(self, batch: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(batch)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)

        (last_hidden_state,) = self._session.run(
            ["last_hidden_state"],
            {"input_ids": input_ids, "attention_mask": attention_mask},
        )

        return pool_and_normalize(
            last_hidden_state, attention_mask, self.profile.pooling
        )


def pool_and_normalize(
    last_hidden_state: np.ndarray,
    attention_mask: np.ndarray,
    pooling: str,
) -> np.ndarray:
    """토큰 벡터 → 문장 벡터. 방식은 **프로파일이 정한다**.

    🔴 풀링 방식은 모델마다 다르고, 틀려도 예외가 나지 않는다 — 그럴듯한
    벡터가 나오되 검색 품질만 조용히 나빠진다. 그래서 프로파일에 명시된 값만
    받고, 모르는 값이면 **차라리 실패한다**(기본값으로 넘어가면 잘못된 인덱스가
    조용히 만들어진다).
    """
    if pooling == "mean":
        pooled = _mean_pool(last_hidden_state, attention_mask)
    elif pooling == "cls":
        pooled = _cls_pool(last_hidden_state)
    else:
        raise ValueError(
            f"알 수 없는 풀링 방식: {pooling!r} (사용 가능: 'mean', 'cls'). "
            "모델 레포의 1_Pooling/config.json을 확인하세요."
        )
    return _l2_normalize(pooled)


def _mean_pool(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """attention_mask 가중 평균.

    패딩 토큰까지 평균에 넣으면 짧은 문장일수록 벡터가 0쪽으로 끌려가 유사도가
    왜곡된다. 실제 토큰 개수로만 나눠야 한다.
    """
    mask = attention_mask.astype(np.float32)[..., None]  # (batch, seq, 1)
    summed = (last_hidden_state * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)  # 0으로 나누기 방지
    return summed / counts


def _cls_pool(last_hidden_state: np.ndarray) -> np.ndarray:
    """첫 토큰([CLS]) 벡터만 취한다 (KURE-v1·bge-m3 계열).

    토크나이저가 항상 특수 토큰을 붙이므로 0번이 [CLS]다. 패딩은 뒤에 붙어
    첫 토큰을 침범하지 않으므로 attention_mask가 필요 없다.
    """
    return last_hidden_state[:, 0, :]


def _l2_normalize(pooled: np.ndarray) -> np.ndarray:
    """L2 정규화 — 저장 시점에 해두면 검색 때 코사인 유사도가 내적 한 번으로 끝난다."""
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return (pooled / np.clip(norms, a_min=1e-12, a_max=None)).astype(np.float32)


def vector_to_blob(vector: np.ndarray) -> bytes:
    """float32 little-endian 바이트로 직렬화 (SQLite BLOB 저장용)."""
    return np.asarray(vector, dtype="<f4").tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")

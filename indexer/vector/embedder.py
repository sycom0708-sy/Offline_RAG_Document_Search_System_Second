"""ONNX 기반 문장 임베딩 (T3.1, T3.2).

torch 없이 `onnxruntime` + `tokenizers`만으로 동작한다. 사용하는 모델이 int8
양자화 ONNX를 이미 배포하고 있어 변환 단계가 필요 없고, torch를 빼면 런타임이
117MB 가벼워져 TECH 9.2의 인스톨러 예산에 들어간다.

모델은 `last_hidden_state`만 내주므로 문장 벡터는 직접 만들어야 한다
(`modules.json` 기준 mean pooling):

    1. attention_mask로 패딩 토큰을 제외한 **가중 평균**
    2. L2 정규화 — 저장 시점에 해두면 검색 때 코사인 유사도가 내적 한 번으로 끝난다

## 양자화로 인한 재현성 한계

int8 **동적** 양자화 모델이라 활성값 스케일을 배치 단위로 잡는다. 그래서 같은
문장이라도 어떤 배치에 함께 실려 들어갔느냐에 따라 벡터가 미세하게 달라진다
(실측: 자기 자신과의 코사인 유사도 약 0.985, 길이가 같아 패딩이 없어도 동일).

검색 품질에는 영향이 없다 — 관련 문서와 무관 문서의 유사도 격차(0.65 vs 0.07)가
이 노이즈(±0.03)보다 훨씬 크고, 배치 크기를 바꿔도 순위가 유지되는 것을 테스트로
확인했다. 다만 **벡터가 비트 단위로 재현되지는 않으므로**, 저장된 벡터를 해시로
비교해 변경을 감지하는 식의 설계는 하면 안 된다(Phase 8 증분 갱신 시 주의).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from config.settings import ModelProfile, get_profile


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
        # 배치로 넣으려면 길이를 맞춰야 한다. 잘림 한도는 모델 사양을 따른다.
        tokenizer.enable_truncation(max_length=profile.max_seq_length)
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

        return _mean_pool_and_normalize(last_hidden_state, attention_mask)


def _mean_pool_and_normalize(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """attention_mask 가중 평균 후 L2 정규화.

    패딩 토큰까지 평균에 넣으면 짧은 문장일수록 벡터가 0쪽으로 끌려가 유사도가
    왜곡된다. 실제 토큰 개수로만 나눠야 한다.
    """
    mask = attention_mask.astype(np.float32)[..., None]  # (batch, seq, 1)
    summed = (last_hidden_state * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)  # 0으로 나누기 방지
    pooled = summed / counts

    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return (pooled / np.clip(norms, a_min=1e-12, a_max=None)).astype(np.float32)


def vector_to_blob(vector: np.ndarray) -> bytes:
    """float32 little-endian 바이트로 직렬화 (SQLite BLOB 저장용)."""
    return np.asarray(vector, dtype="<f4").tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")

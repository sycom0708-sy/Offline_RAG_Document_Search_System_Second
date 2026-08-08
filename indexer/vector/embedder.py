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

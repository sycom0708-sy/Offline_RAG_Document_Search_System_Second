"""청크 벡터 저장·조회 (T3.3, T3.4).

TECH 5.1이 ChromaDB를 지목했지만, 이 파이프라인은 ANN 인덱스를 쓰지 않는다 —
FTS5가 좁힌 후보의 벡터만 `chunk_id`로 꺼내 직접 코사인을 계산한다. 그 용도에는
같은 SQLite 파일에 두는 편이 더 맞는다(별도 프로세스·별도 저장소 동기화가
없고, 문서 삭제 시 CASCADE로 함께 정리된다).
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence

import numpy as np

from indexer.vector.embedder import Embedder, blob_to_vector, vector_to_blob

# SQLite 기본 변수 한도(999)를 넘지 않도록 IN 절을 나눠 넣는다.
_SQL_VAR_LIMIT = 900


def store_vectors(
    conn: sqlite3.Connection,
    chunk_ids: Sequence[str],
    vectors: np.ndarray,
    model_key: str,
) -> None:
    """벡터를 저장한다(같은 chunk_id가 있으면 교체)."""
    if len(chunk_ids) != len(vectors):
        raise ValueError(f"개수 불일치: chunk_ids {len(chunk_ids)} vs vectors {len(vectors)}")

    conn.executemany(
        "INSERT OR REPLACE INTO chunk_vectors(chunk_id, model, dim, vector) VALUES (?,?,?,?)",
        [
            (chunk_id, model_key, int(vector.shape[0]), vector_to_blob(vector))
            for chunk_id, vector in zip(chunk_ids, vectors)
        ],
    )
    conn.commit()


def fetch_vectors(
    conn: sqlite3.Connection,
    chunk_ids: Sequence[str],
    model_key: str,
) -> dict[str, np.ndarray]:
    """chunk_id → 벡터. 다른 모델로 만든 벡터는 제외한다.

    모델이 바뀌면 차원과 의미 공간이 모두 달라져 비교 자체가 성립하지 않으므로,
    조용히 섞이지 않도록 `model`이 일치하는 것만 돌려준다.
    """
    if not chunk_ids:
        return {}

    result: dict[str, np.ndarray] = {}
    for start in range(0, len(chunk_ids), _SQL_VAR_LIMIT):
        batch = list(chunk_ids[start : start + _SQL_VAR_LIMIT])
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT chunk_id, vector FROM chunk_vectors "
            f"WHERE model = ? AND chunk_id IN ({placeholders})",
            [model_key, *batch],
        ).fetchall()
        for row in rows:
            result[row["chunk_id"]] = blob_to_vector(row["vector"])
    return result


def missing_vector_count(conn: sqlite3.Connection, model_key: str) -> int:
    """이 모델 기준으로 벡터가 없는 청크 수 (T10.26).

    `missing_chunk_ids()`와 같은 조건이지만 id 목록을 만들지 않는다 — UI가
    "이 모드로 검색이 되는 상태인가"만 확인하는 용도라 개수면 충분하고,
    청크가 수만 개인 인덱스에서 목록을 통째로 들고 올 이유가 없다.
    """
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM chunks c
        LEFT JOIN chunk_vectors v
               ON v.chunk_id = c.chunk_id AND v.model = ?
        WHERE v.chunk_id IS NULL
        """,
        (model_key,),
    ).fetchone()
    return int(row[0])


def missing_chunk_ids(conn: sqlite3.Connection, model_key: str) -> list[str]:
    """현재 모델 기준으로 벡터가 없는 청크 목록."""
    rows = conn.execute(
        """
        SELECT c.chunk_id
        FROM chunks c
        LEFT JOIN chunk_vectors v
               ON v.chunk_id = c.chunk_id AND v.model = ?
        WHERE v.chunk_id IS NULL
        ORDER BY c.id
        """,
        (model_key,),
    ).fetchall()
    return [r["chunk_id"] for r in rows]


def embed_missing(
    conn: sqlite3.Connection,
    embedder: Embedder | None = None,
    batch_size: int = 16,
    on_progress=None,
) -> int:
    """벡터가 없는 청크를 찾아 임베딩하고 저장한다. 처리한 개수를 반환.

    인덱싱 직후뿐 아니라 모델을 바꾼 뒤에도 그대로 호출하면 된다 — 새 모델
    기준으로 비어 있는 청크만 다시 계산한다.
    """
    embedder = embedder or Embedder()
    model_key = embedder.profile.key

    pending = missing_chunk_ids(conn, model_key)
    if not pending:
        return 0

    done = 0
    for start in range(0, len(pending), batch_size):
        batch_ids = pending[start : start + batch_size]
        texts = _fetch_contents(conn, batch_ids)
        vectors = embedder.encode(texts, batch_size=batch_size)
        store_vectors(conn, batch_ids, vectors, model_key)

        done += len(batch_ids)
        if on_progress is not None:
            on_progress(done, len(pending))

    return done


def _fetch_contents(conn: sqlite3.Connection, chunk_ids: Sequence[str]) -> list[str]:
    """chunk_id 순서를 그대로 유지한 content 목록."""
    placeholders = ",".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"SELECT chunk_id, content FROM chunks WHERE chunk_id IN ({placeholders})",
        list(chunk_ids),
    ).fetchall()
    by_id = {r["chunk_id"]: r["content"] for r in rows}
    return [by_id[chunk_id] for chunk_id in chunk_ids]


def vector_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """모델별 저장된 벡터 수 (상태 표시·디버깅용)."""
    rows = conn.execute(
        "SELECT model, COUNT(*) AS n FROM chunk_vectors GROUP BY model"
    ).fetchall()
    return {r["model"]: r["n"] for r in rows}

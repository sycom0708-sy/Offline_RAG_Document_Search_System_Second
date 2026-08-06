"""BM25 키워드 검색 (T2.7).

FTS5 `unicode61`은 색인 시점에 대소문자를 항상 접으므로(실측 확인 — "API"로
MATCH하면 "api"도 걸림), **"대/소문자 구분"은 FTS5 레벨에서 구현할 수 없다.**
1차 MATCH는 그대로 두고, `chunks.content`에 보존된 원문을 Python에서 그대로
비교해 후처리 필터링한다.

**"일치되는 단어"**는 검색어를 큰따옴표로 감싸 리터럴 취급한 뒤, 접두 매칭이
필요하면 닫는 따옴표 뒤에 `*`를 붙이는 방식으로 구현한다(`"api"*`는 FTS5에서
유효한 접두 검색 문법 — 실측 확인). 큰따옴표로 감싸면 하이픈·괄호 등
FTS5 연산자로 오인될 수 있는 특수문자도 함께 안전하게 처리된다. 한글은
조사가 붙으면 하나의 토큰으로 묶이므로(예: "계약서") **기본값은 접두 매칭**이다.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Sequence

from parser.schema import ChunkType

_TERM_SPLIT = re.compile(r"\s+")

# caption 컬럼에 압도적 가중치를 줘 표의 캡션·헤더 매치가 본문보다 우선 노출되게 한다 (TECH 4.3절).
_BM25_WEIGHTS = (1.0, 2.0, 1.0, 5.0)  # content, file_name, keywords, caption


@dataclass
class SearchResult:
    chunk_id: str
    doc_id: str
    file_path: str
    file_name: str
    type: ChunkType
    page_or_slide: int | None
    content: str
    caption: str
    score: float
    table_json: str | None = None
    image_json: str | None = None


def _split_terms(query: str) -> list[str]:
    return [t for t in _TERM_SPLIT.split(query.strip()) if t]


def _quote_term(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def build_match_query(terms: Sequence[str], exact_word: bool) -> str:
    """검색어 목록을 FTS5 MATCH 표현식으로 변환한다."""
    parts = []
    for term in terms:
        quoted = _quote_term(term)
        if not exact_word:
            quoted += "*"
        parts.append(quoted)
    return " AND ".join(parts)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    case_sensitive: bool = False,
    exact_word: bool = False,
    types: Sequence[str] | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """BM25 랭킹 순으로 검색 결과를 반환한다."""
    terms = _split_terms(query)
    if not terms:
        return []

    match_query = build_match_query(terms, exact_word)

    # 대소문자 구분 후처리 필터링으로 결과가 줄어들 것을 감안해 여유 있게 조회한다.
    fetch_limit = limit * 5 if case_sensitive else limit

    sql = """
        SELECT chunks.chunk_id, chunks.doc_id, chunks.file_path, chunks.file_name,
               chunks.type, chunks.page_or_slide, chunks.content, chunks.caption,
               chunks.table_json, chunks.image_json,
               bm25(chunks_fts, ?, ?, ?, ?) AS score
        FROM chunks
        JOIN chunks_fts ON chunks.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ?
    """
    params: list = [*_BM25_WEIGHTS, match_query]

    if types:
        sql += f" AND chunks.type IN ({','.join('?' for _ in types)})"
        params.extend(types)

    sql += " ORDER BY score LIMIT ?"
    params.append(fetch_limit)

    rows = conn.execute(sql, params).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        if case_sensitive and not all(term in row["content"] for term in terms):
            continue
        results.append(
            SearchResult(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                file_path=row["file_path"],
                file_name=row["file_name"],
                type=ChunkType(row["type"]),
                page_or_slide=row["page_or_slide"],
                content=row["content"],
                caption=row["caption"],
                score=row["score"],
                table_json=row["table_json"],
                image_json=row["image_json"],
            )
        )
        if len(results) >= limit:
            break

    return results

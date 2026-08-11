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

# 한국어 조사·어미. 검색어에는 붙어 있고 문서에는 없는 경우가 많다
# ("계약서를" 로 검색 → 문서의 "계약서" 와 매칭 실패).
# FTS5 접두 매칭은 문서 쪽 토큰이 더 길 때만 도움이 되므로, 반대 방향은
# 검색어에서 어미를 떼어 보완한다. 형태소 분석 없이 하는 근사치라 원형도
# 함께 OR로 남겨 과도한 제거로 결과를 잃지 않게 한다.
_KOREAN_SUFFIXES = (
    "으로부터", "이라고", "에서는", "에게서", "이라는", "으로써", "으로서",
    "에서의", "에게는", "부터", "까지", "에서", "에게", "이나", "라도",
    "처럼", "보다", "조차", "마저", "으로", "이랑", "하는", "합니다",
    "했다", "하고", "하여", "해서", "이다", "이며",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과",
    "도", "만", "로", "랑", "한", "할", "함", "됨", "된",
)

# 어간이 이보다 짧아지면 잘못 자른 것으로 보고 버린다 ("도로" → "도" 같은 오탐 방지).
_MIN_STEM_LENGTH = 2

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


def _strip_korean_suffix(term: str) -> str | None:
    """조사·어미를 뗀 어간을 반환한다. 뗄 게 없거나 너무 짧아지면 None."""
    for suffix in _KOREAN_SUFFIXES:
        if len(term) > len(suffix) and term.endswith(suffix):
            stem = term[: -len(suffix)]
            if len(stem) >= _MIN_STEM_LENGTH:
                return stem
            return None
    return None


def query_term_variants(query: str) -> list[tuple[str, ...]]:
    """검색어별 매칭 후보(원형 + 조사를 뗀 어간)를 검색어 순서대로 반환한다.

    `build_match_query()`가 FTS5 표현식을 만들 때 쓰는 것과 **같은 변형 규칙**이다.
    재순위 단계에서 "이 청크가 검색어를 실제로 포함하는가"를 셀 때 이 함수를
    재사용해야 FTS5가 매치시킨 것과 판정이 어긋나지 않는다 — 두 곳에 따로
    구현하면 조사 처리가 갈리는 순간 "전체 일치인데 상단에 안 온다"가 된다.
    """
    variants: list[tuple[str, ...]] = []
    for term in _split_terms(query):
        forms = [term]
        stem = _strip_korean_suffix(term)
        if stem:
            forms.append(stem)
        variants.append(tuple(forms))
    return variants


def build_match_query(
    terms: Sequence[str],
    exact_word: bool,
    require_all: bool = True,
) -> str:
    """검색어 목록을 FTS5 MATCH 표현식으로 변환한다.

    각 검색어는 원형과 (있으면) 조사를 뗀 어간을 OR로 묶는다.
    `require_all=False`면 검색어끼리도 OR로 이어 **재현율**을 높인다 —
    자연어 질문은 문서에 없는 단어를 많이 포함하므로 AND면 0건이 되기 쉽다.
    """
    parts = []
    for term in terms:
        variants = [term]
        stem = _strip_korean_suffix(term)
        if stem:
            variants.append(stem)

        rendered = []
        for variant in variants:
            quoted = _quote_term(variant)
            if not exact_word:
                quoted += "*"
            rendered.append(quoted)

        parts.append(rendered[0] if len(rendered) == 1 else "(" + " OR ".join(rendered) + ")")

    joiner = " AND " if require_all else " OR "
    return joiner.join(parts)


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    case_sensitive: bool = False,
    exact_word: bool = False,
    types: Sequence[str] | None = None,
    limit: int = 50,
    require_all: bool = True,
    fallback_to_any: bool = True,
) -> list[SearchResult]:
    """BM25 랭킹 순으로 검색 결과를 반환한다.

    기본은 모든 검색어를 요구하는 AND다(정밀도 우선). 다만 AND로 0건이면
    **자동으로 OR로 완화**한다 — 자연어 질문에는 문서에 없는 단어가 섞이기
    마련이라(DESIGN §3.1의 "계약서 검토 기준이 뭐였지"), AND만 고집하면
    사용자는 빈 화면만 보게 된다. BM25가 관련도 순으로 정렬해주므로 OR로
    넓혀도 상위 결과의 품질은 유지된다.

    `require_all=False`는 처음부터 OR로 간다. 하이브리드 검색의 1단계처럼
    뒤에 벡터 재순위가 붙는 경우에 쓴다.
    """
    terms = _split_terms(query)
    if not terms:
        return []

    results = _run_search(
        conn, terms, exact_word, case_sensitive, types, limit, require_all
    )
    if results or not (require_all and fallback_to_any and len(terms) > 1):
        return results

    return _run_search(
        conn, terms, exact_word, case_sensitive, types, limit, require_all=False
    )


def _run_search(
    conn: sqlite3.Connection,
    terms: list[str],
    exact_word: bool,
    case_sensitive: bool,
    types: Sequence[str] | None,
    limit: int,
    require_all: bool,
) -> list[SearchResult]:
    match_query = build_match_query(terms, exact_word, require_all)

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

    # 대소문자 후처리 조건은 MATCH 조건과 같은 논리를 따라야 한다.
    # AND로 찾았으면 모든 검색어가, OR로 찾았으면 하나라도 원문 대소문자까지
    # 일치해야 한다 — 어긋나면 MATCH된 결과가 필터에서 통째로 사라진다.
    match_case = all if require_all else any

    results: list[SearchResult] = []
    for row in rows:
        if case_sensitive and not match_case(term in row["content"] for term in terms):
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

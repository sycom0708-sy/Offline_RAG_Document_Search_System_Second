# PLAN: RAG와 sLM을 활용한 오프라인 문서 검색 시스템 — 실행 계획 기록

| 항목 | 내용 |
|---|---|
| 문서 버전 | v1.0 |
| 기준 문서 | TASK(작업 분해·DoD) / TECH(기술 설계) / DESIGN(UI 명세) |
| 문서 목적 | Phase별 **실행 계획**과 **계획 대비 실제 결과**를 누적 기록. TASK가 "무엇을 하는가(체크리스트·DoD)"라면, 이 문서는 "**어떤 순서·구조로 하는가**"와 "**실제로 어떻게 됐는가**"를 남긴다 |
| 갱신 규칙 | 각 Phase 착수 전 계획을 확정하고, 완료 시 §실행 결과를 채운다 |

> TASK 문서와 중복되는 체크리스트는 옮기지 않는다. 이 문서에는 **모듈 구조 / 구현 순서와 그 근거 / 설계 결정 / 계획과 달라진 점 / 다음 Phase로 넘긴 과제**만 기록한다.

---

## 진행 현황 요약

| Phase | 내용 | 상태 | 결과 |
|---|---|---|---|
| **Phase 1** | 문서 파서 모듈 | ✅ **완료** | 테스트 126 passed / 0 skipped, 9종 전부 실검증 |
| **Phase 2** | 폴더 스캔 + FTS5 키워드 인덱싱 | ✅ **완료** | 테스트 177 passed / 0 skipped (누적), CLI로 실검증 |
| Phase 3 | 임베딩 연동 + 벡터 재순위 | ⏭️ **다음 차례** | — |
| Phase 4 | 추출형 검색 UI | 대기 | **MVP 완료 지점** |
| Phase 5 | 표/이미지 카드 렌더러 | 대기 | — |
| Phase 6 | sLM 후보군 실측 검증 | 대기 | — |
| Phase 7 | sLM 답변 생성 옵션 모드 | 대기 | — |
| Phase 8 | 증분 인덱싱 / 폴더 감시 | 대기 | — |
| Phase 9 | exe 패키징 및 배포 테스트 | 대기 | — |

**커밋 이력**
```
(Phase 2 완료 커밋 예정)
e5beff7  Phase별 Sonnet/Opus 모델 추천을 PROMPTS 문서에 기록
a33131d  개발 환경 기준을 Python 3.14.6으로 변경
1c33bb8  다른 PC 이전 시 필요한 환경 정보를 README에 기록
257b2f7  Phase별 실행 계획 기록 문서(PLAN) 추가
486e5d8  사용하지 않는 나눔고딕 웨이트 2종 제거
f41a9d5  Phase 1: 9종 문서 파서 모듈 구현 및 UI 디자인 명세서 작성
```

---

# Phase 1: 문서 파서 모듈 ✅ 완료

## 1-A. 착수 시점 원 계획 (전문)

> 아래는 착수 전 승인받은 계획 원문이다. 실제 결과와의 차이는 §1-B에 정리한다.

### Context
오프라인 RAG 시스템의 착수 Phase. 아직 코드가 전혀 없는 빈 프로젝트(마크다운 기획 문서 3종만 존재)이며, Python 3.10.6 환경에 관련 라이브러리는 아직 미설치, LibreOffice(`soffice`)도 PATH에 없는 상태를 확인했다. 이후 Phase 2(FTS5 인덱싱)가 이 Phase의 파서 출력 스키마에 의존하므로, 모든 형식별 파서가 동일한 청크 스키마(TECH 4.2절)를 따르는 것이 이번 작업의 핵심 제약이다.

### 모듈 구조 (계획)

```
offline_rag_search/
├── parser/
│   ├── __init__.py
│   ├── schema.py            # Chunk, ParsedDocument, TableData, ImageData 데이터클래스 (TECH 4.2절 스키마)
│   ├── base.py               # BaseParser ABC + ParserError 계열 예외
│   ├── registry.py           # 확장자 → 파서 매핑, parse_file(path) 진입점
│   ├── utils/
│   │   ├── ids.py              # doc_id/chunk_id 생성 (경로 기반 uuid5)
│   │   ├── hashing.py           # source_hash (SHA256)
│   │   ├── encoding.py           # chardet 기반 텍스트 디코딩 (CP949/EUC-KR 대응)
│   │   └── libreoffice.py        # 헤드리스 변환 wrapper + 예외 클래스
│   └── formats/
│       ├── txt_parser.py
│       ├── pdf_parser.py         # PyMuPDF: 텍스트/표(find_tables)/이미지/벡터도형 페이지 렌더링
│       ├── docx_parser.py        # python-docx: 문단/표/이미지 + 벡터도형은 LO 변환 캡처로 보완
│       ├── xlsx_parser.py        # openpyxl: 시트=표, 임베디드 이미지
│       ├── pptx_parser.py        # python-pptx: 슬라이드 텍스트/표/이미지 + 벡터도형 슬라이드 캡처
│       ├── hwp_parser.py         # pyhwp(hwp5txt): 텍스트 위주 (표/이미지 지원 한계 명시)
│       ├── hwpx_parser.py        # zip+XML 자체 파싱 (OOXML과 유사 구조)
│       └── legacy_parser.py      # doc/xls/ppt → LibreOffice 변환 후 위 파서에 위임
├── tests/
│   ├── conftest.py
│   ├── fixtures/generate_samples.py   # docx/xlsx/pptx/pdf/txt 샘플을 코드로 생성
│   └── test_*.py (형식별 + schema + registry)
├── requirements.txt
├── pyproject.toml            # 패키지 메타 + pytest 설정
└── CLAUDE.md                 # 진행 상황 포인터 (작업 완료 후 갱신)
```

### 공통 스키마 설계 (계획, TECH 4.2절 반영)

`schema.py`에 정의:
- `Chunk`: `chunk_id, doc_id, file_path, file_name, type(text|table|image), page_or_slide, content, keywords, embedding_vector(=None, Phase 3에서 채움), created_at, source_mtime, source_hash`
- `type=table`인 청크는 `content`에 행·열 구조(`list[list[str]]`)를 JSON 직렬화 가능한 형태로 담고, 문단 텍스트와 절대 병합하지 않음 (TECH 3.1절)
- `type=image`인 청크는 `content`에 이미지 파일 경로(캡처/추출본 저장 위치)를 담고, `keywords`에 "이미지(텍스트 미인식 가능)" 플래그성 메타를 남겨 Phase 5 렌더러가 안내 문구를 붙일 수 있게 함
- `ParsedDocument`: `doc_id, file_path, title, chunks: list[Chunk], parse_status(ok|partial|failed), errors: list[str]` — 구버전 포맷 변환 실패 등을 부분 실패로 표현하기 위함

### 구현 순서 (계획, Task 번호는 TASK 문서 기준·의존성 우선으로 재배열)

1. **T1.1** 스캐폴딩 — `venv` 생성 안내, `requirements.txt`(pymupdf, python-docx, openpyxl, python-pptx, chardet, pyhwp), `pyproject.toml`(pytest 설정), 디렉토리 골격, `.gitignore`
2. **T1.12** 공통 스키마 + `BaseParser` 먼저 정의 (다른 모든 파서가 이걸 참조하므로 순서를 앞당김 — TASK 문서 원래 순번과 다르지만 의존관계상 필요)
3. **T1.4** TXT 파서 — chardet로 인코딩 감지 후 kss 없이 일단 원문 청크 하나로 반환(문장 분리는 Phase 2 T2.4 소관, 여기서는 파서 출력까지만)
4. **T1.2** PDF 파서 — PyMuPDF `get_text()`(본문), `find_tables()`(표), `get_images()`(삽입 이미지 추출), 텍스트 없는 페이지는 벡터도형으로 간주해 페이지 렌더링 캡처
5. **T1.3** DOCX 파서 — 문단(헤딩 태그로 제목 후보 추출)/표(`table.rows`)/이미지(`inline_shapes`) 분리 추출. 벡터 도형(SmartArt 등)은 별도 감지 로직 없이 "LibreOffice 변환 캡처로 보완 가능" 지점을 훅으로 남기고 T1.9에서 연결
6. **T1.5** XLSX 파서 — 시트별 표 청크(헤더 행 포함), 임베디드 이미지 추출
7. **T1.6** PPTX 파서 — 슬라이드별 텍스트/표/이미지, 벡터 도형은 DOCX와 동일하게 T1.9 캡처 훅
8. **T1.7** HWP 파서 — `hwp5txt` CLI 또는 `pyhwp` API로 텍스트 추출. 표/이미지 구조 추출은 라이브러리 한계로 텍스트만 지원함을 README/제한사항 문서에 명시
9. **T1.8** HWPX 파서 — zip 열어 `Contents/section*.xml` 파싱, 문단/표 태그 매핑 (OOXML 유사 구조)
10. **T1.9** LibreOffice 헤드리스 변환 파이프라인 — `soffice --headless --convert-to` wrapper, `LibreOfficeNotFoundError`/`ConversionTimeoutError`/`ConversionFailedError` 명확히 구분. doc→docx, xls→xlsx, ppt→pptx 변환 후 기존 파서에 위임하는 `legacy_parser.py`. 변환 실패 시 `parse_status="failed"` + `errors`에 사유 기록 (예외를 삼키지 않고 상위로 상태 전파)
11. **T1.10 / T1.11** 표/이미지 분리 저장 규칙 — 각 파서 구현 시 이미 반영하지만, 전체 파서 대상으로 마지막에 일괄 검토 패스 진행
12. **T1.13** 샘플 문서 + 단위 테스트 — `tests/fixtures/generate_samples.py`로 샘플을 코드로 생성해 각 파서 테스트

### 착수 시점에 공유한 제약
- **LibreOffice 미설치**: T1.9 파이프라인은 코드로 구현하지만 이 환경에서 실제 변환 동작은 검증 불가
- **hwp 실제 샘플 부재**: 유효한 바이너리 `.hwp`를 코드로 생성할 수 없어 실제 파일로 테스트 불가. pyhwp 설치 자체가 실패할 가능성도 고려
- 위 두 항목은 DoD("9종 형식 오류 없이 파싱") 완전 충족을 위해 별도 환경에서 확인 필요

### 검증 방법 (계획)
- `pytest tests/` 전체 실행, txt/pdf/docx/xlsx/pptx/hwpx 파서는 실제 통과 확인
- legacy(doc/xls/ppt) 및 hwp는 스킵 사유가 명확히 출력되는지 확인
- 각 파서 출력이 스키마 검증을 통과하는지, 표/이미지 청크가 텍스트 청크와 분리되어 있는지 assert
- 완료 후 TASK 체크박스 갱신 + CLAUDE.md 한 줄 요약

---

## 1-B. 실행 결과 — 계획 대비 변경점

**결과: DoD 100% 충족.** 테스트 **126 passed / 0 skipped**, 9종 전부 실검증(hwp는 실문서, doc·xls·ppt는 LibreOffice 실변환).

### ① 스키마 — 구조를 `content`가 아닌 전용 필드로 분리 **(계획 변경)**

계획은 표의 행·열 구조와 이미지 경로를 `content`에 담는 것이었으나, 실제로는 **전용 필드로 분리**했다.

```python
class Chunk:
    ...
    content: str                    # 검색·인덱싱용 평문
    table: TableData | None = None  # 행·열 구조 원형
    image: ImageData | None = None  # 경로·출처·캡션
```

**이유**: `content`에 구조를 넣으면 FTS5 인덱싱(Phase 2)과 카드 렌더링(Phase 5)이 같은 필드를 서로 다르게 해석해야 한다. 분리하니 `content`는 검색용 평문(`TableData.to_text()`가 캡션·헤더를 앞세워 생성 — TECH 4.3절 가중 반영), 구조는 렌더러 전용으로 역할이 명확해졌다. `__post_init__`에서 타입과 필드 일치를 강제한다.

부수적으로 `parse_status` → `status`로 이름을 줄이고, 이미지 안내 문구용 `keywords` 플래그는 `ImageData.origin`(`extracted`/`rendered`)으로 대체했다.

### ② HWP가 계획보다 많이 됐다 **(범위 확대)**

계획은 "pyhwp는 텍스트 위주, 표·이미지는 라이브러리 한계로 미지원"이었으나, `hwp5.xmlmodel`로 XML 트리를 직접 순회해 **표와 이미지까지 추출**했다. 실문서 검증에서 표 3개·이미지 38개를 얻었다.

### ③ 계획에 없던 모듈 2개 추가

| 모듈 | 추가 이유 |
|---|---|
| `parser/utils/imaging.py` | HWP BinData가 `.tmp` 확장자로 저장돼 **확장자 판별이 불가능**했다. 매직 넘버(시그니처) 기반 형식 판별이 필요해졌다 |
| `parser/utils/render.py` | 페이지 렌더링 캡처 로직이 PDF·docx·pptx 3곳에서 중복돼 분리 |

### ④ 실환경 검증에서 잡은 버그 3건

**합성 샘플로는 전부 통과했고, 실제 문서·실제 LibreOffice를 붙이고 나서야 드러났다.**

| # | 버그 | 원인 | 수정 |
|---|---|---|---|
| 1 | HWP 이미지 **38개 전량 누락** | BinData가 `.tmp` 확장자라 확장자 화이트리스트에 하나도 안 걸림 | 시그니처 판별로 전환 (`imaging.py`) |
| 2 | 1행짜리 표의 **데이터 소실** | 첫 행을 무조건 헤더로 승격해 `rows`가 빔 → 표 카드가 빈 표로 렌더링될 상황 | `TableData.from_rows()` 공통 팩토리로 6개 파서 통일. 2행 이상일 때만 헤더 승격 |
| 3 | 변환 실패 **사유 유실** | 한국어 Windows의 soffice는 CP949로 출력하는데 `subprocess(text=True)`가 UTF-8 고정 디코딩 → 리더 스레드에서 예외 | 바이트 수신 후 다단계 디코딩 |

> **교훈**: 3건 모두 "코드가 만든 샘플"이 아니라 "현실의 파일·현실의 외부 도구"에서만 나왔다. 이후 Phase에서도 실데이터 검증 단계를 생략하지 않는다.

### ⑤ 의존성 추가

- `six` — pyhwp가 선언하지 않은 런타임 의존성
- `reportlab`, `Pillow` — 테스트 샘플(PDF·이미지) 생성용

### ⑥ 최종 산출 구조 (실제)

```
parser/
├── schema.py          Chunk / ParsedDocument / TableData / ImageData
├── base.py            BaseParser ABC + 예외
├── registry.py        확장자 → 파서 매핑, parse_file() 진입점
├── README.md          형식별 지원 범위·성능 특성·제한사항
├── utils/  ids · hashing · encoding · imaging · libreoffice · render
└── formats/ txt · pdf · docx · xlsx · pptx · hwp · hwpx · legacy
tests/  conftest · fixtures/generate_samples · test_{parsers,schema,registry,imaging,legacy_and_hwp}
```

### ⑦ 다음 Phase로 넘긴 과제

| 과제 | 넘긴 곳 | 내용 |
|---|---|---|
| 구버전 변환 성능 | Phase 2 | 건당 **2.47초** (순정 docx 0.01초의 약 250배). 파일마다 soffice 프로세스를 새로 띄우는 구조 |
| 벡터 캡처 범위 | Phase 5 | docx/pptx는 도형 유무 사전 판별 수단이 없어 **전 페이지 캡처**(PDF는 선별). 페이지 수에 비례해 비용 증가 |
| pyhwp 핸들 | Phase 8 | BinData 언팩 후 디렉터리 핸들을 바로 놓지 않아 캐시 삭제가 잠깐 막힘. 재파싱은 덮어쓰기라 무해하나 증분 갱신 시 확인 필요 |

---

# Phase 2: 폴더 스캔 + FTS5 키워드 인덱싱 ✅ 완료

**목표**: sLM·임베딩 없이 키워드 검색만으로 동작을 확인한다.
**입력**: `parser.parse_file()`이 반환하는 `ParsedDocument.chunks`

## 2-A. 착수 시점 계획

> 아래는 착수 전 세운 계획이다. 실제 결과와의 차이는 §2-B에 정리한다.

## 계획 모듈 구조

```
indexer/
├── __init__.py
├── scanner.py        # 폴더 재귀 탐색, 지원 확장자 필터 (T2.1)
├── chunker.py        # kss 문장 분리 기반 청킹 (T2.4)
├── keywords.py       # 제목·키워드 추출 (T2.5)
└── fts5/
    ├── schema.sql    # 문서·청크 테이블 + FTS5 가상 테이블 (T2.2)
    ├── store.py      # 파서 출력 → DB 저장 (T2.3, T2.6)
    └── search.py     # BM25 검색 쿼리 (T2.7)
search/
└── (Phase 3에서 hybrid_search.py 추가)
```

## 착수 전 반드시 결정할 것

### 🔴 대/소문자 구분 — 인덱스 재생성이 걸린 문제

FTS5 기본 토크나이저 `unicode61`은 **색인 시점에 대소문자를 접는다(case-fold).** 따라서 DESIGN §4.2의 "대/소문자 구분" 토글은 **쿼리 옵션만으로 구현할 수 없다.**

선택지:
1. `unicode61 remove_diacritics 0` + 별도 대소문자 보존 컬럼 병행
2. 원문을 별도 컬럼에 보관하고 후처리 필터링
3. 커스텀 토크나이저

**이 결정을 Phase 4까지 미루면 인덱스를 통째로 다시 만들어야 한다.** T2.2 스키마 설계 시점에 확정한다.

### 🟡 구버전 포맷 배치 변환

Phase 1에서 넘어온 과제. 구버전 문서 1,000건이면 최초 인덱싱에만 **40분 이상**이다. 접근안:
- soffice에 여러 파일을 한 번에 전달 (프로세스 기동 비용 1회로 축소)
- LibreOffice 리스너 데몬 상주 후 재사용
- 인덱싱 파이프라인이 어차피 백그라운드 스레드(T2.8)이므로, 구버전만 별도 큐로 분리하는 것도 방법

**주의**: 최소 사양(8GB)에서 데몬 상주는 메모리 부담이 될 수 있다. 실측 후 결정한다.

### 🟡 kss 도입 비용

TECH 7장이 `kss`를 지정했으나 의존성이 무겁고 초기 로딩이 느릴 수 있다. 최소 사양·오프라인 패키징(Phase 9) 제약을 함께 보고, 무거우면 규칙 기반 분리로 대체할지 T2.4에서 실측 판단한다.

## 검증 방법 (계획)
- 샘플 폴더 전체 인덱싱 후 키워드 검색만으로 관련 청크가 조회되는지 확인
- 표 청크가 캡션·헤더 가중(TECH 4.3절)으로 상위 노출되는지 확인
- 백그라운드 인덱싱 중 UI 프리즈가 없는지 (T2.8)
- **한글 검색어**로 BM25 랭킹이 정상 동작하는지

## 2-B. 실행 결과 — 계획 대비 변경점

**결과: DoD 충족.** 테스트 **177 passed / 0 skipped**(Phase 1의 126 + 이번 51), CLI로 인덱싱·검색을 실제 눈으로 확인.

### ① 착수 전 결정 사항 — 실측으로 확정 (계획대로)

**대/소문자 구분**: 착수 전 우려대로 FTS5 레벨에서는 불가능함을 실측 재확인("API"로 MATCH하면 "api"도 걸림). §2-A 선택지 2(원문 별도 컬럼 + 후처리 필터링)를 채택해 `chunks.content`에 원문을 그대로 보관하고, `search.py`가 매치 결과를 Python에서 대소문자까지 비교해 필터링한다. 선택지 1("remove_diacritics 0")은 발음 구별 기호 전용 옵션이라 대소문자와 무관함을 뒤늦게 확인 — **계획 문서의 이 부분이 부정확했다.**

**구버전 포맷 배치 변환**: 계획대로 이번 Phase에서 최적화하지 않고 Phase 9로 이월. `IndexingThread`가 실제로 메인 스레드와 분리되어 동작함을 테스트로 확인했다(건당 2.47초가 그대로 background thread 안에서 소요될 뿐, 메인 스레드는 막히지 않음).

**kss 도입 비용**: 실제 설치해 확인 — `numpy`, `scipy`, `networkx`, `pecab` 등 약 15개 의존성을 끌고 온다. 계획대로 TECH 7장 지정을 따라 채택하되, 문장 분리 실패 시 정규식 폴백으로 인덱싱이 죽지 않게 방어했다.

### ② 계획에 없던 새 발견 — 스키마 구현 중 실측

**FTS5 가상 테이블에 별칭을 쓰면 `MATCH`가 깨진다.** `SELECT ... FROM chunks_fts f WHERE f MATCH '...'`처럼 별칭(`f`)을 쓰면 `no such column: f` 오류가 난다. `search.py`의 쿼리 빌더는 항상 `chunks_fts`라는 원래 테이블명을 그대로 쓰도록 고정했다 — 이 특성을 모르고 짜면 검색 함수 자체가 동작하지 않는다.

**"일치되는 단어" 토글이 처음 계획보다 단순하게 구현됐다.** 별도 형태소 분석 없이, 검색어를 큰따옴표로 감싼 뒤(`"api"`) 접두 매칭이 필요하면 닫는 따옴표 뒤에 `*`를 붙이는(`"api"*`) FTS5 표준 문법만으로 해결됐다. 이 방식은 하이픈·괄호 등 FTS5 연산자로 오인될 수 있는 특수문자까지 함께 안전하게 처리해줘서, 별도 이스케이프 로직이 필요 없어졌다.

**한글+영문이 공백 없이 붙으면 하나의 토큰으로 묶인다.** 예: "API문서"는 통째로 한 토큰이라 완전 토큰 매치 `API`로는 안 잡히고, 접두 매치 `API*`로는 잡힌다. 이 특성이 **"일치되는 단어" 기본값을 접두 매칭(OFF)으로 정한 근거를 한글 조사 문제뿐 아니라 한영 혼용 문서에서도 재확인**시켜줬다.

**kss는 자식 프로세스로 백엔드를 초기화해 `sys.stdout` 리다이렉션으로 첫 실행 메시지를 억제할 수 없다.** `contextlib.redirect_stdout`은 파이썬 레벨 트릭이라 자식 프로세스가 상속한 원래 stdout에는 영향을 주지 못한다. OS 파일디스크립터를 직접 바꾸는 방법도 있지만 이식성·안전성 트레이드오프가 커서 채택하지 않고, "최초 1회 나오는 정보성 메시지"로 남겨뒀다.

### ③ 계획에 없던 설계 결정 — 구현 중 확정

- **청크 그룹핑 크기**: 계획에는 "문장 분리로 청킹"까지만 있었고 구체적 크기가 없었다. 문장 하나씩 청크로 만들면 문맥이 사라지고 문단 전체를 하나로 두면 검색 신호가 희석되므로, **문장 경계를 지키며 400자 내외로 묶는 방식**(`chunker.chunk_text`)으로 확정했다. 이 값은 Phase 3 임베딩 청크 단위에도 그대로 이어지므로 재조정 시 영향 범위가 넓다.
- **재인덱싱 정책**: 같은 `doc_id`로 다시 저장하면 기존 문서+청크를 삭제 후 통째로 다시 넣는 방식(`DELETE` → `INSERT`)으로 구현했다. Phase 8의 진짜 증분 갱신(변경분만 재처리) 전까지의 임시 정책이며, 멱등성은 테스트로 확인했다.
- **표/이미지 청크는 재분할하지 않음**을 store.py 레벨에서 명시적으로 강제했다(TECH 3.1절) — text 타입만 `chunk_text()`를 거치고 나머지는 원형 그대로 저장, 테스트로 회귀 방지.

### 다음 Phase로 넘긴 과제

| 과제 | 넘긴 곳 | 내용 |
|---|---|---|
| kss 의존성 무게 | Phase 9 | numpy/scipy/networkx 등 약 15개 패키지. 인스톨러 용량 실측 시 재평가 |
| 구버전 변환 성능 | Phase 9 (Phase 1에서 이월) | 여전히 미해결, 배치 변환·데몬 재사용 검토 필요 |
| 재인덱싱을 "삭제 후 재삽입"으로 처리 | Phase 8 | 진짜 증분 갱신(mtime/해시 비교로 변경분만) 구현 시 이 임시 정책을 대체 |
| 청크 400자 그룹핑 크기 | Phase 3 | 임베딩 재순위 품질을 보고 조정 여지 있음 |

**산출물**: `indexer/` 모듈(`scanner.py`, `chunker.py`, `pipeline.py`, `cli.py`, `fts5/{schema,store,search}.py`), 단위 테스트 51건

---

# Phase 3: 임베딩 연동 + 벡터 재순위

**목표**: FTS5 후보군에 대해 코사인 유사도 기반 2단계 재순위를 얹는다.

## 계획 모듈 구조
```
indexer/vector/
├── embedder.py     # ONNX + int8 양자화 추론 (T3.1, T3.2)
└── store.py        # ChromaDB 컬렉션, chunk_id 기반 ID 조회 (T3.3, T3.4)
search/
└── hybrid_search.py  # FTS5 후보 → 벡터 재순위 통합 (T3.5, T3.6)
config/
└── settings.py     # 경량/고성능 모드 토글 (T3.7)
```

## 핵심 설계 결정 (TECH 5.1절)
**ANN이 아니라 후보 내 직접 코사인 유사도 계산**이다. 그래서 FAISS가 아닌 ChromaDB를 쓴다 — ID 기반 정밀 조회에 적합하기 때문. 이 전제가 바뀌면 벡터 저장소 선택도 재검토해야 한다.

## 유의점
- **임계값 0.5**(TECH 5.3절)는 DESIGN §5.6의 "관련성 낮음" 흐림 처리와 Phase 7의 sLM 호출 차단에 **모두** 쓰인다. 한 곳에서 상수로 관리한다
- `Chunk.embedding_vector` 필드는 Phase 1에서 이미 자리를 잡아뒀다
- 8GB 환경 벤치마크(T3.8)는 최소 사양 실기 또는 동급 조건에서 측정

---

# Phase 4: 추출형 검색 UI — MVP 완료 지점

**기준 문서**: `DESIGN_오프라인RAG시스템.md` (목업 v3 확정 명세)

## 최우선 결정: T4.0 UI 프레임워크
**이후 모든 컴포넌트 작업의 전제**다. 웹 임베디드(pywebview/Flet) vs 네이티브(PySide6)를 다음 기준으로 판단한다.

| 기준 | 근거 문서 |
|---|---|
| 카드형 UI 구현 난이도 | DESIGN §5 (3종 카드, 표 렌더링) |
| 8GB·CPU-only 자원 부담 | PRD 4장 |
| PyInstaller 패키징 용량 | TECH 9.2 (인스톨러 800MB~1.2GB 목표) |
| 나눔고딕 런타임 로딩 | DESIGN §10.4 (OS 설치 금지, 상대 경로) |

## 착수 전 정리 필요 — DESIGN §8 문서 간 불일치 4건

| # | 항목 | PRD/TECH | 목업 v3 | 영향 |
|---|---|---|---|---|
| 1 | AI 요약 토글 위치 | 검색바 옆 | 사이드바 | 소 (v3 채택) |
| 2 | **좌측 필터 범위** | 형식+폴더+기간 | **형식만** | **대** — 기간 필터 제외 시 MVP 범위 축소 |
| 3 | 이미지 카드 원문 열기 | 공통 요소 | 없음 | 중 |
| 4 | 관련성 낮음 카드 원문 열기 | 공통 요소 | 라벨이 대체 | 중 |

## 구현 순서
레이아웃 셸 → 검색바 → 사이드바(필터·토글·콤보박스) → 결과 카드 → 상태바

## Phase 1·2 결과물과 연결되는 지점
- **한글 IME**: 검색 debounce는 `compositionend` 기준. 조합 중 검색하면 "ㄱㅖ" 같은 질의가 나감 (DESIGN §3.2)
- **하이라이트 일관성**: 대소문자·일치단어 옵션이 검색 조건과 하이라이트에 **동일하게** 적용돼야 함. 어긋나면 사용자가 결과를 불신 (DESIGN §5.3)
- **폰트**: 400·700 두 웨이트만 존재. 다른 값 지정 시 가짜 볼드 합성 (DESIGN §10.2)

---

# Phase 5: 표 카드 / 이미지 카드 렌더러

**의존**: Phase 4(카드 프레임) + Phase 1(표·이미지 파싱 결과)

## Phase 1 스키마와 연결 — 놓치기 쉬운 2가지

**① xlsx 위치 표기는 `page_or_slide`가 아니다**
목업은 `Sheet2`를 표시한다. `page_or_slide`는 시트 **인덱스**(정수)이므로, 시트명은 `TableData.caption`에서 가져와야 한다. 모르고 만들면 "2페이지"로 나온다. (DESIGN §5.2)

**② `header_row`가 빈 표를 반드시 처리**
Phase 1의 `from_rows()`는 1행짜리 표에서 헤더를 비워 둔다(데이터 소실 방지 때문). 렌더러가 헤더 존재를 전제하면 깨진다. (DESIGN §5.4)

## 그 외
- 썸네일 캐시 300px (TECH 4.4) — Phase 8 증분 갱신과 연동
- `ImageData.origin`으로 삽입 이미지/렌더링 캡처 구분 가능
- 벡터 캡처 전 페이지 문제(Phase 1 이월)를 이 시점에 재검토

---

# Phase 6: sLM 후보군 실측 검증

**목표**: "문서에 없으면 모른다고 답하는가"(근거 강제 프롬프트 준수율)를 후보 모델별로 **실측**해 선정한다.

- 후보: Qwen2.5-1.5B/7B, EXAONE, Phi-3.5-mini (GGUF Q4/Q4_K_M)
- 측정: 준수율 / 응답 속도 / 메모리 — 최소·권장 사양 각각
- 테스트셋은 **근거 있는 질문 / 없는 질문**을 나눠 구성. 후자가 핵심 지표

> TECH 10장이 오픈 이슈로 명시한 항목이다. **설계로 보장할 수 없어 실측이 유일한 방법**이다. Phase 7의 안전장치 2번이 여기 결과에 직접 의존한다.

---

# Phase 7: sLM 답변 생성 옵션 모드

**의존**: Phase 6(모델 선정), Phase 4(검색 결과 파이프라인)

## 4단계 안전장치 (TECH 5.3절)
1. 유사도 임계값 미달 시 **sLM 호출 자체를 생략** — Phase 3의 임계값 상수 재사용
2. 근거 강제 프롬프트 + low temperature(0~0.2)
3. 문장 단위 출처 표기 `[파일명, 페이지/슬라이드]` — `Chunk.file_name` + `page_or_slide` 사용
4. 답변-근거 겹침도 사후 계산 → "확인 필요" 표시 (권장 사양 전용)

## 함께 구현
모델 매니저 화면 + 다운로드 안내 팝업(SHA256) + 폴더 열기 + 새로고침 검증 (TECH 9.3)

DESIGN §4.2의 placeholder 토글을 실제 동작으로 교체한다(T4.7 → T7.5).

---

# Phase 8: 증분 인덱싱 / 폴더 감시

**의존**: Phase 2(인덱싱), Phase 5(썸네일 캐시)

- `Chunk.source_mtime` / `source_hash`는 **Phase 1에서 이미 채워지고 있다** — 이 값을 그대로 비교에 쓴다
- 썸네일 캐시도 함께 증분 갱신
- watchdog 실시간 감시는 옵션. 최소 사양에서는 기본 OFF 검토
- Phase 1 이월: pyhwp 핸들 미해제로 캐시 삭제가 막히는 현상 확인

---

# Phase 9: exe 패키징 및 배포 테스트

**의존**: Phase 1~8 전체

## 포터블 구조 원칙 (TECH 9.1) — 전 Phase에 걸친 제약
실행·모델·인덱스·캐시 경로를 **전부 상대 경로**로. 레지스트리 미사용. 폴더 압축 이동만으로 동일 동작.

**이미 지켜지고 있는 것**: 나눔고딕을 OS에 설치하지 않고 `font/`에서 런타임 로딩 (DESIGN §10.4)

**확인 필요**: LibreOffice 포터블 번들 경로. `parser/utils/libreoffice.py`가 `vendor/LibreOfficePortable/`을 탐색 경로에 이미 포함하고 있다

## 배포 시나리오
| 시나리오 | sLM | 크기 |
|---|---|---|
| 최소(추출형만) | 미포함 | 800MB~1.2GB |
| 권장(Phi-3.5-mini) | 2.3GB | 3.5~4.5GB |
| 권장(Qwen2.5-7B/EXAONE) | 4.5GB | 5.5~6.5GB |

## 라이선스 동봉 (미해결)
나눔고딕 OFL 라이선스 전문을 `font/`에 추가해야 한다. 사내 팀원 재배포가 전제이므로 해당된다. (DESIGN §13-6)

---

## 부록: Phase 간 의존관계

```
Phase 1 (파서) ✅
   └─▶ Phase 2 (FTS5 인덱싱) ⏭️
          └─▶ Phase 3 (벡터 재순위)
                 └─▶ Phase 4 (추출형 UI) ◀── MVP 완료
                        ├─▶ Phase 5 (표/이미지 카드)  ◀── Phase 1 결과물도 필요
                        ├─▶ Phase 6 (sLM 검증)
                        │      └─▶ Phase 7 (sLM 옵션 모드)
                        └─▶ Phase 8 (증분 인덱싱)  ◀── Phase 2, 5 결과물도 필요
                               └─▶ Phase 9 (패키징/배포)  ◀── 전체 Phase 선행
```

## 부록: 미해결 결정 사항 (전체)

| # | 항목 | 결정 시점 | 미루면 생기는 비용 |
|---|---|---|---|
| 1 | **대/소문자 구분 FTS5 구현 방식** | **Phase 2 T2.2** | 인덱스 전체 재생성 |
| 2 | 구버전 배치 변환 방식 | Phase 2 | 최초 인덱싱 40분+ |
| 3 | kss 도입 여부 | Phase 2 T2.4 | 패키징 용량·로딩 속도 |
| 4 | UI 프레임워크 | **Phase 4 T4.0** | 전 컴포넌트 재작업 |
| 5 | DESIGN §8 불일치 4건 (특히 기간 필터) | Phase 4 착수 전 | MVP 범위 변동 |
| 6 | AI 요약 토글 Phase 4 처리 (비활성 vs 안내) | Phase 4 | 사용자 혼란 |
| 7 | 디자인 토큰 실제 값 (색상·간격) | Phase 4 | 재작업 |
| 8 | OFL 라이선스 파일 동봉 | Phase 9 이전 | 재배포 요건 미충족 |

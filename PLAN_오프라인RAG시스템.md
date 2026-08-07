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
| **Phase 2** | 폴더 스캔 + FTS5 키워드 인덱싱 | ✅ **완료** | 테스트 177 passed (누적). *Phase 3에서 결함 2건 발견·수정* |
| **Phase 3** | 임베딩 연동 + 벡터 재순위 | ✅ **완료** | 테스트 233 passed / 0 skipped (누적), 검색 지연 7~14ms |
| Phase 4 | 추출형 검색 UI | ⏭️ **다음 차례** | **MVP 완료 지점** |
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

# Phase 3: 임베딩 연동 + 벡터 재순위 ✅ 완료

**목표**: FTS5 후보군에 대해 코사인 유사도 기반 2단계 재순위를 얹는다.

## 3-A. 착수 시점 계획

```
indexer/vector/
├── embedder.py     # ONNX + int8 양자화 추론 (T3.1, T3.2)
└── store.py        # ChromaDB 컬렉션, chunk_id 기반 ID 조회 (T3.3, T3.4)
search/
└── hybrid_search.py  # FTS5 후보 → 벡터 재순위 통합 (T3.5, T3.6)
config/
└── settings.py     # 경량/고성능 모드 토글 (T3.7)
```

**핵심 설계 결정 (TECH 5.1절)**: ANN이 아니라 후보 내 직접 코사인 유사도 계산이다. 그래서 FAISS가 아닌 ChromaDB를 쓴다 — ID 기반 정밀 조회에 적합하기 때문. **이 전제가 바뀌면 벡터 저장소 선택도 재검토해야 한다.**

**유의점**: 임계값 0.5는 한 곳에서 상수로 관리 / `Chunk.embedding_vector`는 Phase 1에서 자리 잡음 / 벤치마크는 최소 사양 기준

## 3-B. 실행 결과 — 계획 대비 변경점

**결과: DoD 충족.** 테스트 **233 passed / 0 skipped**(Phase 1·2의 177 + 이번 56). 검색 지연 7~14ms.

### ① 벡터 저장소를 ChromaDB → SQLite BLOB으로 변경 (사용자 승인)

착수 전 계획이 스스로 남긴 단서("이 전제가 바뀌면 재검토")를 실제로 따져본 결과다. **전제는 그대로였지만 결론이 틀렸다** — ANN을 안 쓴다면 필요한 건 `chunk_id → 벡터` 조회 하나뿐인데, ChromaDB는 그 하나를 위해 **79개 패키지·75MB**(`kubernetes`, `grpcio`, `bcrypt`, onnxruntime 중복본 포함)를 끌고 온다.

기존 `index.sqlite3`에 `chunk_vectors` 테이블을 두니 ① 추가 의존성 0개 ② 후보 조회가 같은 커넥션에서 끝남 ③ 문서 삭제 시 CASCADE로 벡터까지 자동 정리. **TECH 5.1·10장과 기술 스택 표를 함께 정정했다.**

### ② ONNX 변환 파이프라인이 불필요해짐

`jhgan/ko-sroberta-multitask`가 **int8 양자화 ONNX를 이미 배포**하고 있었다(`onnx/model_qint8_avx512_vnni.onnx`, 111MB). 덕분에 T3.2의 변환 파이프라인을 만들 필요가 없어졌고, **런타임에서 torch(117MB)를 완전히 뺄 수 있었다**. 런타임은 onnxruntime + tokenizers + numpy(35MB)뿐이라 TECH 9.2의 인스톨러 예산에 여유가 생겼다.

### ③ 고성능 모드(KURE-v1)는 구조만 (사용자 승인)

원본 2.27GB에 ONNX 미제공이라 변환에 torch가 필요하고, int8 후에도 ~570MB로 TECH 9.2 예산(100~250MB)을 넘는다. 토글·추론 코드는 완성했고 실검증은 경량 모델로 했다. TECH 9.3의 sLM과 동일한 **분리 다운로드** 대상.

### ④ 🔴 Phase 2 결함 2건을 발견해 수정 — 둘 다 실데이터에서만 드러남

**(1) kss 문장 분리가 실사용 불가 — 53자/초**

실제 hwp 문서(8,267자)에 **157초**가 걸렸다. 10만 자 문서 하나면 31분으로, PRD 5.1의 "최초 실행 시 전체 인덱싱"이 성립하지 않는다. Phase 2 테스트는 샘플이 수백 자라 못 잡았다. 원인은 kss가 C++ 백엔드(mecab) 없이 순수 파이썬 백엔드로 폴백한 것 — 설치 시 나오던 안내 메시지가 실은 성능 경고였다.

정규식 분리로 전환해 **157초 → 0.4ms**. mecab 설치는 Windows에서 까다롭고 PRD 4장(관리자 권한 불필요)·Phase 9 포터블 배포와 충돌해 채택하지 않았다.

> 구현 중 한국어 종결어미 규칙("~합니다" 뒤에서 끊기)을 추가했다가 **제거**했다. 마침표가 있으면 기존 규칙이 이미 처리해 중복이고, 오히려 먼저 발동해 닫는 따옴표를 다음 문장으로 떠넘기는 버그를 만들었다.

**(2) 자연어 질의가 0건을 반환**

DESIGN §3.1의 placeholder인 "계약서 검토 기준이 뭐였지"로 검색하면 결과가 없었다. 원인이 둘 겹쳐 있었다:
- **AND 조건**: 질문에 섞인 "뭐였지" 같은 단어까지 전부 있어야 매치
- **한국어 조사**: "계약서**를**"로 검색하면 문서의 "계약서"와 매칭 실패. FTS5 접두 매칭은 *문서* 쪽 토큰이 길 때만 도움이 되고, 이 경우는 반대 방향이다

검색어에서 조사·어미를 떼어 원형과 OR로 묶고(`("계약서를" OR "계약서")`), AND로 0건이면 OR로 자동 완화하도록 고쳤다. AND로 결과가 나오는 질의는 그대로 둬서 정밀도는 유지된다.

### ⑤ 청크 크기를 문자 수 → **토큰 수** 기준으로 변경

계획서에는 "400자 → 300자로 낮춤"으로 적었으나, 실측해보니 **문자 수 자체가 잘못된 대리 지표**였다. 자/토큰 비율이 **0.50~2.70으로 5배 넘게** 흔들린다(한국어 산문은 느슨, 번호·기호·영문 섞인 표는 조밀). 300자로 낮춰도 실문서에서 30개 청크 중 **27개가 128토큰을 초과**했다.

`chunk_text(count_tokens=...)`로 토큰 수 기준 분할로 바꿔 **초과 0개**가 됐다. 토크나이저 의존성은 주입 방식이라 Phase 2 단독 실행 경로는 그대로 문자 기준으로 동작한다.

### ⑥ int8 양자화의 재현성 한계 (문서화만)

같은 문장이라도 **어떤 배치에 실려 들어갔느냐에 따라 벡터가 미세하게 달라진다**(자기 자신과의 코사인 유사도 약 0.985). 패딩 때문이 아니라 — 길이가 같아도 발생 — 동적 양자화가 활성값 스케일을 배치 단위로 잡기 때문이다.

검색 품질에는 영향이 없다(관련/무관 격차 0.65 vs 0.07이 노이즈 ±0.03보다 훨씬 크고, 배치 크기를 바꿔도 순위 동일함을 테스트로 확인). 다만 **벡터가 비트 단위로 재현되지 않으므로 해시 비교로 변경을 감지하는 설계는 불가** — Phase 8에서 주의.

### 실측 데이터

| 항목 | 값 |
|---|---|
| 모델 최초 로딩 | 651ms (1회성) |
| 임베딩 처리량 | 38 청크/초 |
| 검색 지연 (키워드) | 0.4~1.2ms |
| 검색 지연 (하이브리드) | 7~14ms |
| DoD 확인 | 자연어 질의에서 키워드 단독 0건 vs 하이브리드 정답 1위(0.655) |

> 측정 PC는 8GB 최소 사양기가 아니다. 절대값은 참고치이며, 최소 사양 실측은 Phase 9에서 수행한다.

### 다음 Phase로 넘긴 과제

| 과제 | 넘긴 곳 | 내용 |
|---|---|---|
| 임베딩 처리량 38청크/초 | Phase 8·9 | 10만 청크면 약 44분. 최초 인덱싱은 백그라운드라 견딜 만하나 대량 환경에서 재검토 |
| 양자화 재현성 | Phase 8 | 벡터 해시 비교로 증분 판단 불가 — mtime/내용 해시로 판단할 것 |
| KURE-v1 분리 다운로드 UI | Phase 7 | 모델 매니저 화면에 sLM과 함께 노출 |
| 최소 사양 실측 | Phase 9 | 8GB 실기에서 검색 지연·메모리 재측정 |

**산출물**: `config/settings.py`, `indexer/vector/{embedder,store,download}.py`, `search/hybrid_search.py`, `scripts/benchmark_search.py`, 테스트 56건

---

# Phase 4: 추출형 검색 UI — MVP 완료 지점

**기준 문서**: `DESIGN_오프라인RAG시스템.md` (목업 v3 확정 명세)

## 4-A. 착수 전 확정 사항 (Opus 세션에서 결정 완료)

### ① T4.0 UI 프레임워크 = **PySide6**

실측 후보 비교:

| 후보 | 다운로드 | 배포 가능성 |
|---|---|---|
| **PySide6** | 237MB (essentials 212MB, **위젯만 번들 시 50~70MB**) | **완전 자족** — 외부 런타임 0 |
| pywebview | 2.6MB | Edge **WebView2**가 대상 PC에 있어야 실행 |
| Flet | 3MB | Flutter 런타임 별도 확보 필요 |

**용량이 아니라 배포 방식이 결정 근거다.** TECH 9.4는 "압축 해제 후 즉시 실행"을 약속하는데, pywebview는 WebView2에 의존한다 — Win11에는 기본 포함이나 구형 Win10·관리된 사내 PC에서는 보장되지 않고, 오프라인 설치본(~150MB)을 번들하는 순간 그 약속이 깨진다. Flet은 런타임을 따로 받아야 해 오프라인 전제(PRD 6장)와 더 멀다.

용량 부담은 Phase 3에서 이미 상쇄됐다 — **ChromaDB 75MB + torch 117MB 절감분**이 PySide6를 덮는다. 8GB 제약에도 유리하다(WebView2는 Chromium이라 프로세스가 여럿 뜨지만 Qt 위젯 앱은 단일 프로세스).

구현 시: `PySide6-Essentials`만 설치하고 PyInstaller에서 Quick/QML/Designer/3D/OpenGL 소프트웨어 렌더러를 제외한다. 카드 UI는 QSS(CSS와 유사), 하이라이트는 `QLabel` 리치 텍스트, 폰트는 `QFontDatabase.addApplicationFont`. 상세는 DESIGN §0.

### ② DESIGN §8 문서 간 불일치 4건 — 모두 확정

| # | 항목 | 확정 |
|---|---|---|
| 1 | AI 요약 토글 위치 | **v3(사이드바) 채택** |
| 2 | 좌측 필터 범위 | **MVP는 형식만.** 폴더·기간은 Phase 5 이후로 이월하되, **검색 함수에 파라미터는 미리 열어둔다** |
| 3 | 이미지 카드 원문 열기 | **추가** (확대와 병기) |
| 4 | 관련성 낮음 카드 원문 열기 | **추가** (라벨과 병기) |

2번은 버리는 게 아니라 미루는 것이다(PRD 5.2 요구사항). 목업에 없는 UI를 즉흥 설계하면 "확정 목업 기준"이라는 전제가 무너진다. 백엔드 데이터는 이미 있으므로(폴더 = `documents.file_path`, 기간 = `source_mtime`/`indexed_at`) 파라미터만 열어두면 나중에 UI만 붙이면 된다.

3·4번은 같은 근거다 — "사용자가 즉시 검증할 수 있는 구조"(TECH 5.3 설계 철학)가 이 제품의 핵심인데 특정 카드에서만 원문 접근을 막을 이유가 없다.

## 4-B. 구현 계획

**구현 순서**: 레이아웃 셸 → 검색바 → 사이드바(필터·토글·콤보박스) → 결과 카드 → 상태바

**백엔드 연결**: `search.hybrid_search.hybrid_search()`를 그대로 호출한다. 반환되는 `HybridResult`에 `similarity`, `is_low_relevance`가 이미 들어 있어 DESIGN §5.6의 흐림 처리에 바로 쓸 수 있다. 인덱싱 상태바(§6)는 `IndexReport.indexed`/`embedded`와 `documents` 테이블 카운트를 쓴다.

**Qt 특유의 주의점**
- 인덱싱은 `IndexingThread`(Phase 2)가 이미 백그라운드로 돈다. **Qt 위젯은 워커 스레드에서 직접 건드리면 안 되므로**, 진행 콜백을 시그널로 바꿔 메인 스레드에서 UI를 갱신해야 한다
- sqlite3 커넥션은 스레드 간 공유 불가 — `IndexingThread`가 이미 자기 스레드에서 여는 구조라 그대로 두면 된다

## Phase 1·2 결과물과 연결되는 지점
- **한글 IME**: 검색 debounce는 `compositionend` 기준. 조합 중 검색하면 "ㄱㅖ" 같은 질의가 나감 (DESIGN §3.2)
- **하이라이트 일관성**: 대소문자·일치단어 옵션이 검색 조건과 하이라이트에 **동일하게** 적용돼야 함. 어긋나면 사용자가 결과를 불신 (DESIGN §5.3)
- **폰트**: 400·700 두 웨이트만 존재. 다른 값 지정 시 가짜 볼드 합성 (DESIGN §10.2)

## 4-C. 모델 관리 ↔ PC 성능 선택 연동 (착수 전 추가 확정)

다른 PC에서 이미 동작 중인 모델 매니저 화면(Phase 7 T7.6 범위, sLM 다운로드용)을 검토하다가, TECH 9.3의 "임베딩 모델은 항상 설치됨" 가정이 **Phase 3 이후로 더 이상 사실이 아님**을 발견했다 — 경량(`ko-sroberta`, 내장)과 고성능(`KURE-v1`, 별도 다운로드) 두 프로파일로 갈라졌기 때문이다. 시안을 만들어 검토했다([artifact](https://claude.ai/code/artifact/af6d783e-7ca1-499d-be2f-be4c9a52ba4e)).

**결정 1 — 연동 방식은 Option A (모델 관리가 단일 진입점)**: 사이드바 `PC 성능 선택` 콤보박스는 선택 트리거만 담당하고, 실제 프로파일 전환은 모델 관리 팝업에서만 일어난다. 콤보박스에서 미설치 옵션을 고르면 모델 관리가 자동으로 열리고 해당 행에 포커스된다. "설정은 고성능인데 실제로는 경량으로 검색되는" 어긋난 상태 자체를 차단하는 게 목적이다.

재인덱싱은 새 로직이 필요 없다 — 프로파일 전환 후 `indexer.vector.store.embed_missing(conn, new_embedder)`를 그대로 호출하면, 모델 키(→차원)가 달라진 모든 청크가 자동으로 "미완료"로 잡혀 재계산된다(`fetch_vectors`가 이미 `model` 컬럼으로 걸러내는 설계였기 때문에 공짜로 따라온다).

**결정 2 — KURE-v1 실다운로드는 이번에 구현하지 않는다**: 허깅페이스 레포에 ONNX가 전혀 없다(`safetensors`만 제공, Phase 4 착수 전 재확인). `config/settings.py`의 `HEAVY.files`가 `onnx/model_int8.onnx`를 가리키는 건 **존재하지 않는 파일**이라, 지금 다운로드 버튼을 만들면 그대로 실패한다. Phase 4에서는 모델 관리에 KURE-v1 행을 넣되 **"준비 중" 배지 + 비활성 버튼**으로만 노출한다. safetensors→ONNX 변환 파이프라인(Phase 3에서 미룬 T3.2에 해당) 구현은 별도 Phase로 재검토한다.

**TASK 문서 반영**: T4.11a/T4.11b 신설(모델 관리 임베딩 섹션 + 재인덱싱 트리거를 Phase 4로 앞당김), T7.6~T7.7 각주 갱신, TECH 9.3 문구 정정.

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

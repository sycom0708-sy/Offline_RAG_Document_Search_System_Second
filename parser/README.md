# parser/ — 문서 파서 모듈 (Phase 1)

지원 형식별 파서를 제공하며, 모든 파서는 TECH 문서 4.2절의 공통 청크 스키마로 결과를 반환한다.

## 사용법

```python
from parser import parse_file, ChunkType

document = parse_file("보고서.pdf")
print(document.title, document.status)

for chunk in document.chunks_of(ChunkType.TABLE):
    print(chunk.table.header_row, chunk.table.rows)
```

## 형식별 지원 범위

| 형식 | 파서 | 텍스트 | 표 | 이미지 |
|---|---|---|---|---|
| txt/md/csv/log | `TxtParser` | O (chardet 인코딩 감지) | - | - |
| pdf | `PdfParser` | O | O (`find_tables`) | O (삽입 + 벡터 도형 페이지 렌더링) |
| docx | `DocxParser` | O | O | O (삽입, 벡터 도형은 옵션) |
| xlsx/xlsm | `XlsxParser` | - (시트 전체가 표) | O (시트별) | O |
| pptx | `PptxParser` | O (슬라이드별) | O | O (삽입, 벡터 도형은 옵션) |
| hwp | `HwpParser` | O | O | O (BinData, 시그니처 판별) |
| hwpx | `HwpxParser` | O | O | O |
| doc/xls/ppt/rtf | `LegacyOfficeParser` | LibreOffice 변환 후 위임 | 〃 | 〃 |

## 표/이미지 분리 규칙 (TECH 3.1절)

- 표는 `type=table` 청크로 분리되며 `chunk.table.rows`에 행·열 구조를 그대로 보존한다. 표 영역 텍스트는 본문(`type=text`) 청크에 포함하지 않는다.
- 캡션·헤더는 `chunk.keywords`에도 담아 FTS5 키워드 매칭 가중에 쓴다 (TECH 4.3절).
- 이미지는 `type=image` 청크로 분리되며 `chunk.image.origin`이 `extracted`(문서 삽입 이미지)와 `rendered`(벡터 도형 페이지 캡처)를 구분한다.

## 환경 의존 기능

### LibreOffice (구버전 포맷 + 벡터 도형 캡처)

`doc/xls/ppt` 파싱과 docx/pptx의 벡터 도형 캡처에는 LibreOffice가 필요하다. 탐색 순서는 `SOFFICE_PATH` 환경변수 → `PATH` → Windows 기본 설치 경로 → `vendor/LibreOfficePortable`이다.

미설치 시 `LibreOfficeNotFoundError`가 발생하며, `LegacyOfficeParser`는 이를 예외로 던지지 않고 `ParsedDocument.status = FAILED` + `errors`로 전파해 인덱서가 개별 파일 실패로 처리할 수 있게 한다.

docx/pptx의 벡터 도형 캡처는 변환 비용이 커서 기본 OFF이며 `capture_vector_shapes=True`로 켠다.

```python
from parser.formats.pptx_parser import PptxParser
PptxParser(capture_vector_shapes=True).parse("발표자료.pptx")
```

> PDF는 도형이 있는 페이지만 선별해 캡처하지만, docx/pptx는 **전 페이지를 캡처**한다. PDF처럼 도형 유무를 사전 판별할 수단이 없기 때문이다. 장수가 많은 문서에 켜면 캡처 수와 소요 시간이 페이지 수에 비례해 늘어난다.

### 성능 특성

구버전 포맷은 파일마다 `soffice` 프로세스를 새로 띄우므로 순정 포맷보다 훨씬 느리다 (측정: doc 2.47초 vs docx 0.01초). 대량 인덱싱 시에는 배치 변환이나 LibreOffice 데몬 재사용을 검토해야 한다 (Phase 2 과제).

### pyhwp

`pyhwp`는 런타임 의존성 `six`를 선언하지 않으므로 함께 설치해야 한다 (`requirements.txt`에 포함).

HWP의 `BinData` 항목은 확장자가 `.tmp`로 저장되므로 파일명이 아닌 **시그니처(매직 넘버)로 실제 형식을 판별**한다 (`parser/utils/imaging.py`). 확장자만 보고 거르면 이미지가 전량 누락된다.

## 테스트

샘플 문서는 저장소에 커밋하지 않고 `tests/fixtures/generate_samples.py`가 코드로 생성한다.

`.hwp`만은 바이너리 OLE 구조라 코드로 만들 수 없어 실제 파일에 의존한다. **프로젝트 루트에 `.hwp` 파일을 두면 자동으로 탐색**하며, `HWP_SAMPLE_PATH` 환경변수로 다른 경로를 지정할 수도 있다. 둘 다 없으면 해당 테스트는 스킵된다.

```bash
pytest -q
```

### 환경별 예상 결과

스킵은 실패가 아니라 **환경 의존 테스트**가 사유를 출력하고 넘어가는 것이다. 전체를 갖추면 **233 passed / 0 skipped**이며, 아래 항목이 빠지면 그만큼 스킵된다.

| 빠진 것 | 스킵되는 테스트 | 갖추는 방법 |
|---|---|---|
| LibreOffice | doc/xls/ppt 변환, 벡터 도형 캡처 (8건) | 설치 후 `SOFFICE_PATH` 지정(기본 경로는 자동 탐색) |
| `.hwp` 샘플 | hwp 실문서 파싱 (5건) | 프로젝트 루트에 `.hwp` 파일 두기 |
| 임베딩 모델 | 임베딩·하이브리드 검색 (22건) | `python -m indexer.vector.download` |

`.hwp` 파싱에 **한/글 설치는 불필요하다** — pyhwp가 단독으로 동작하므로 `.hwp` 파일만 있으면 된다.

### 다른 PC로 옮길 때

`.venv`는 절대 경로가 박혀 있어 이식되지 않는다. 압축에서 제외하고 옮긴 PC에서 새로 만든다.

```bash
python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

**개발 환경 기준: Python 3.14.6** / PyMuPDF 1.28.0 / lxml 6.1.1 / Pillow 12.3.0 / chardet 7.4.3

Python 3.10.6에서도 동일하게 126 passed를 확인했고 **설치되는 패키지 버전도 같다**. 다만 3.10은 2026년 10월 EOL이므로 3.14를 기준으로 삼는다. `pyproject.toml`의 `requires-python`은 코드가 실제로 요구하는 하한선(3.10)을 그대로 둔다 — 기준 버전과 호환 하한선은 다른 값이다.

Python 버전이 다르면 컴파일된 패키지(lxml·Pillow·chardet)의 해당 버전 wheel이 필요하다. PyMuPDF·cryptography는 `abi3` wheel이라 상위 호환된다. 설치 로그에 `Building wheel for lxml...`처럼 **소스 빌드**가 뜨면 맞는 wheel이 없다는 뜻이며, Windows에서는 빌드 도구가 없어 대개 실패한다 — 이 경우 동작이 확인된 버전(3.14.6 또는 3.10.6)을 별도 설치해 venv를 만드는 편이 빠르다.

> `pyhwp`는 순수 파이썬(`py3-none-any`) 패키지라 설치 시 `Building wheel for pyhwp`가 뜨는 것이 정상이며, C 컴파일이 아니므로 실패 신호가 아니다.

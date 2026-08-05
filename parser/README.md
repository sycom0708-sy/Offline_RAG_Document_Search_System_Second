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

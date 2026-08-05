# CLAUDE.md — 오프라인 RAG 문서 검색 시스템

기획 문서: `PRD_오프라인RAG시스템.md`(제품 요구사항) / `TECH_오프라인RAG시스템.md`(기술 설계) / `TASK_오프라인RAG시스템.md`(작업 분해·진행 상황) / `DESIGN_오프라인RAG시스템.md`(UI 디자인 명세, 목업 v3 기준 — Phase 4~5 구현 기준 문서)

## 개발 환경

```bash
./.venv/Scripts/python.exe -m pytest -q
```

## 진행 상황

- **Phase 1 (문서 파서 모듈) — 완료 (DoD 충족)**: 9종 형식 파서(`parser/`)를 TECH 4.2절 공통 청크 스키마로 통일해 구현, 표는 행·열 구조 보존 + 이미지는 삽입/렌더링 캡처 구분해 텍스트와 분리 저장. 테스트 126 passed / 0 skipped, 9종 전부 실검증(hwp는 실문서, doc·xls·ppt는 LibreOffice 실변환). 실환경 검증에서 버그 3건(HWP 이미지 전량 누락, 단일행 표 데이터 소실, soffice CP949 출력 유실)을 잡았다.
- **알려진 성능 이슈**: 구버전 포맷은 건당 2.47초(순정 0.01초) — Phase 2 대량 인덱싱에서 배치 변환 검토 필요. 상세는 `parser/README.md`.
- **Phase 2 (FTS5 키워드 인덱싱) — 다음 차례**: `parser.parse_file()`이 반환하는 `ParsedDocument.chunks`를 입력으로 사용한다.

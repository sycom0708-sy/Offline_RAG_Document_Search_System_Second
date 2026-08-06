# CLAUDE.md — 오프라인 RAG 문서 검색 시스템

기획 문서: `PRD_오프라인RAG시스템.md`(제품 요구사항) / `TECH_오프라인RAG시스템.md`(기술 설계) / `TASK_오프라인RAG시스템.md`(작업 분해·진행 상황) / `DESIGN_오프라인RAG시스템.md`(UI 디자인 명세, 목업 v3 기준 — Phase 4~5 구현 기준 문서) / `PLAN_오프라인RAG시스템.md`(Phase별 실행 계획·계획 대비 결과 누적 기록) / `PROMPTS_오프라인RAG시스템.md`(Phase별 실행 프롬프트 + 모델 추천)

**Phase 착수 시 `PLAN` 문서를 먼저 확인한다.** 각 Phase의 모듈 구조·선결 결정 사항·이전 Phase에서 넘어온 과제가 정리되어 있다. Phase 완료 시 해당 Phase의 "실행 결과" 절을 채우고 다음 Phase 계획을 갱신한다.

**Phase 착수 직전에는 `PROMPTS` 문서의 "모델 추천" 표를 확인해 사용자에게 알려준다.** 해당 Phase에 Opus 5 전환이 권장되는 결정 지점이 있으면 먼저 짚어주고, 그 결정 이후 구현은 Sonnet 5로 이어가도록 안내한다.

## 개발 환경

**Python 3.14.6 기준.** venv 재생성이 필요하면 `py -3.14 -m venv .venv` 후 `requirements.txt`를 설치한다.

```bash
./.venv/Scripts/python.exe -m pytest -q
```

전체 통과 시 **126 passed**. LibreOffice나 `.hwp` 샘플이 없는 환경에서는 일부가 스킵되며, 이는 실패가 아니다 (환경별 예상 결과는 `parser/README.md` 참고).

## 진행 상황

- **Phase 1 (문서 파서 모듈) — 완료 (DoD 충족)**: 9종 형식 파서(`parser/`)를 TECH 4.2절 공통 청크 스키마로 통일해 구현, 표는 행·열 구조 보존 + 이미지는 삽입/렌더링 캡처 구분해 텍스트와 분리 저장. 테스트 126 passed / 0 skipped, 9종 전부 실검증(hwp는 실문서, doc·xls·ppt는 LibreOffice 실변환). 실환경 검증에서 버그 3건(HWP 이미지 전량 누락, 단일행 표 데이터 소실, soffice CP949 출력 유실)을 잡았다.
- **알려진 성능 이슈**: 구버전 포맷은 건당 2.47초(순정 0.01초) — Phase 2 대량 인덱싱에서 배치 변환 검토 필요. 상세는 `parser/README.md`.
- **Phase 2 (FTS5 키워드 인덱싱) — 다음 차례**: `parser.parse_file()`이 반환하는 `ParsedDocument.chunks`를 입력으로 사용한다.

# CLAUDE.md — 오프라인 RAG 문서 검색 시스템

기획 문서: `PRD_오프라인RAG시스템.md`(제품 요구사항) / `TECH_오프라인RAG시스템.md`(기술 설계) / `TASK_오프라인RAG시스템.md`(작업 분해·진행 상황) / `DESIGN_오프라인RAG시스템.md`(UI 디자인 명세, 목업 v3 기준 — Phase 4~5 구현 기준 문서) / `PLAN_오프라인RAG시스템.md`(Phase별 실행 계획·계획 대비 결과 누적 기록) / `PROMPTS_오프라인RAG시스템.md`(Phase별 실행 프롬프트 + 모델 추천)

**Phase 착수 시 `PLAN` 문서를 먼저 확인한다.** 각 Phase의 모듈 구조·선결 결정 사항·이전 Phase에서 넘어온 과제가 정리되어 있다. Phase 완료 시 해당 Phase의 "실행 결과" 절을 채우고 다음 Phase 계획을 갱신한다.

**Phase 착수 직전에는 `PROMPTS` 문서의 "모델 추천" 표를 확인해 사용자에게 알려준다.** 해당 Phase에 Opus 5 전환이 권장되는 결정 지점이 있으면 먼저 짚어주고, 그 결정 이후 구현은 Sonnet 5로 이어가도록 안내한다.

## 개발 환경

**Python 3.14.6 기준.** venv 재생성이 필요하면 `py -3.14 -m venv .venv` 후 `requirements.txt`를 설치한다.

```bash
./.venv/Scripts/python.exe -m pytest -q
```

전체 통과 시 **233 passed**. LibreOffice·`.hwp` 샘플·임베딩 모델이 없는 환경에서는 일부가 스킵되며, 이는 실패가 아니다 (환경별 예상 결과는 `parser/README.md` 참고).

임베딩 모델은 용량 때문에 저장소에 없다. 인터넷이 되는 PC에서 한 번 받아둔다:

```bash
./.venv/Scripts/python.exe -m indexer.vector.download
```

## 진행 상황

- **Phase 1 (문서 파서 모듈) — 완료 (DoD 충족)**: 9종 형식 파서(`parser/`)를 TECH 4.2절 공통 청크 스키마로 통일해 구현, 표는 행·열 구조 보존 + 이미지는 삽입/렌더링 캡처 구분해 텍스트와 분리 저장. 테스트 126 passed / 0 skipped, 9종 전부 실검증(hwp는 실문서, doc·xls·ppt는 LibreOffice 실변환). 실환경 검증에서 버그 3건(HWP 이미지 전량 누락, 단일행 표 데이터 소실, soffice CP949 출력 유실)을 잡았다.
- **알려진 성능 이슈**: 구버전 포맷은 건당 2.47초(순정 0.01초) — Phase 9 패키징 시 배치 변환 검토 필요. 상세는 `parser/README.md`.
- **Phase 2 (FTS5 키워드 인덱싱) — 완료 (DoD 충족)**: `indexer/` 모듈로 폴더 스캔→청킹(kss+정규식 폴백)→FTS5 저장(external content 트리거 동기화)→BM25 검색까지 구현. "대/소문자 구분"은 원문 보존 후처리 필터링으로, "일치되는 단어"는 접두(`"어절"*`)/완전 토큰 매치 전환으로 해결(둘 다 DESIGN §4.2 토글과 실제 연동 확인). 표 청크는 캡션·헤더를 별도 컬럼에 담아 BM25 가중치 5배로 상위 노출. `IndexingThread`로 백그라운드 인덱싱 + 진행 콜백. 임시 CLI(`python -m indexer.cli index|search`)로 실검증. 테스트 51건 추가(누적 177 passed / 0 skipped). 상세는 `PLAN_오프라인RAG시스템.md` Phase 2 절 참고.
- **Phase 3 (임베딩 연동 + 벡터 재순위) — 완료 (DoD 충족)**: `ko-sroberta-multitask` int8 ONNX(111MB)로 torch 없이 추론(`indexer/vector/`), FTS5 후보 → 코사인 재순위(`search/hybrid_search.py`). **벡터 저장소는 ChromaDB 대신 SQLite BLOB** — ANN을 안 써서 ID 조회만 필요한데 ChromaDB는 79개 패키지를 끌고 오기 때문(TECH 5.1 정정 완료). 검색 지연 7~14ms. 테스트 233 passed / 0 skipped.
- **Phase 3에서 Phase 2 결함 2건 발견·수정**: ① `kss`가 53자/초로 실사용 불가(8,267자에 157초) → 정규식 분리로 전환 ② 자연어 질의가 0건 반환(AND 조건 + 한국어 조사 미처리) → 조사 제거 + OR 폴백. 청킹도 문자 수 → **토큰 수** 기준으로 변경(자/토큰 비율이 0.50~2.70으로 흔들려 문자 수로는 모델 한계를 지킬 수 없음).
- **Phase 4 (추출형 검색 UI) — 다음 차례, 선결 결정 완료**: `search.hybrid_search()`를 백엔드로 쓴다. **UI 프레임워크는 PySide6로 확정**(외부 런타임 의존 없이 포터블 배포가 가능한 유일한 후보 — DESIGN §0), **DESIGN §8 불일치 4건도 모두 확정**(폴더·기간 필터는 MVP 제외하되 검색 함수 파라미터는 미리 열어둠). 구현 착수 준비가 끝난 상태이며 상세는 `PLAN` 문서 §4-A/4-B 참고.
- **모델 관리 ↔ PC 성능 선택 연동도 확정 (PLAN §4-C)**: 콤보박스는 선택 트리거만, 실제 전환은 모델 관리 팝업에서(Option A). 모델 관리에 임베딩 섹션을 이번 Phase로 앞당겨 넣되(원래 Phase 7 범위), **`KURE-v1`은 ONNX 미제공이라 "준비 중"으로만 노출**하고 실다운로드는 구현하지 않는다. TECH 9.3의 "임베딩 항상 설치됨" 문구는 더 이상 사실이 아니므로 정정됨.

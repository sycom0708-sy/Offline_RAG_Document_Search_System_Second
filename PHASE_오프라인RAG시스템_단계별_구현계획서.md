# Phase 단계별 구현 계획서

> 이 문서는 Claude Code **plan 모드**로 각 Phase 착수 전에 작성한 계획 원문을 그대로 보관하는 아카이브다. **승인 시점 스냅샷**이므로 이후 실제 구현이 달라진 부분(버그 발견, 설계 변경 등)은 반영되지 않는다 — 계획 대비 실제로 어떻게 됐는지는 `PLAN_오프라인RAG시스템.md`의 각 Phase "실행 결과" 절을 참고한다. Phase가 완료될 때마다 그 시점의 plan 파일 내용을 이 문서 위쪽에 이어붙인다(최신이 위).

---

<!-- 출처: sequential-snacking-puddle.md · DESKTOP-V42GJBP · 작성 2026-08-11 14:26 · 아카이브 2026-08-11 15:15 -->

# Phase 7.6: AI 요약을 AI 챗봇으로 교체 (2단 응답 구조)

## Context

Phase 7의 "AI 요약 보기"(검색마다 자동 1회 요약)를 실사용해보니 필요성이 불분명했다. 사용자가 원한 건 검색 결과를 근거로 후속 질문을 이어가는 챗봇이었는데, 설계 중 **속도 실측이 발목을 잡았다** — LLM 생성 답변은 발췌 4~5개·긴 출력 기준 11~79초(모델에 따라)가 걸린다. "은행 앱 챗봇처럼 빠르게"라는 요구와 정면 충돌했다.

**해법은 사용자가 목업 2장으로 직접 제시했다: 응답을 2단으로 나눈다.**

1. **즉시 응답(기본, LLM 미사용)** — 질문을 치면 `hybrid_search()`가 이미 하고 있는 그대로, **가장 관련 높은 검색 발췌를 원문 그대로** 채팅 말풍선에 띄운다. 측정된 검색 지연은 7~14ms — 은행 앱보다 빠르다.
2. **AI 요약 보기(선택, LLM 사용)** — 그 말풍선 아래 버튼을 **눌렀을 때만** LLM이 돌아가 생성 요약을 만든다. 느릴 걸 스스로 선택한 것이므로 대기가 자연스럽다.
3. **파일 열기** — 발췌의 원문 위치를 연다(현재는 파일만 열림, 위치 이동은 T10.1 백로그 — 목업의 "할 수 있다면"과 일치, 이번 범위 아님).

[사용자 확정, 2026-08-11] 이 2단 구조로 확정. "AI 챗봇 사용" 토글이 "AI 요약 보기"를 대체하고(추가 아님), 켜면 검색 결과 영역 전체가 채팅 화면이 된다. 메시지마다 독립 처리(stateless) — 매번 전체 문서 범위로 새로 검색한다.

---

## 왜 새 백엔드 코드가 거의 필요 없는가

두 워커가 이미 정확히 이 모양으로 나뉘어 있다:

| 단 | 재사용할 것 | 트리거 |
|---|---|---|
| ① 즉시 발췌 | `ui/search_worker.py:SearchWorker` (**무수정**) | 메시지 전송 시 |
| ② AI 요약 | `ui/summary_worker.py:SummaryWorker` (**무수정**) — `SummaryWorker(question, results, service, request_id)`가 이미 검색 결과를 인자로 받지 직접 검색하지 않는다 | 그 말풍선의 "AI 요약 보기" 클릭 시, ①에서 이미 받아온 `results`를 그대로 넘긴다 |

즉 **②를 위해 새 검색을 다시 할 필요가 없다** — ①의 결과를 들고 있다가 버튼 클릭 시 그대로 `SummaryWorker`에 넘기면 끝이다. `slm/prompt.py`의 3문장 제한 프롬프트도 그대로 둔다 — 전체 내용은 이미 ①에서 원문으로 보여줬으니, ②는 "짧게 정리"가 오히려 맞는 역할이다. 새 프롬프트(`build_chat_messages`)는 **불필요, 계획에서 뺀다.**

---

## 1. 토글 — `ui/widgets/search_options.py`, `ui/state.py`

- 라벨 "AI 요약 보기" → **"AI 챗봇 사용"**. 시그널명 `ai_summary_changed`는 유지(배선 최소 변경)
- `AppState.ai_summary_enabled` → **`ai_chat_enabled`로 리네임**. `_load_raw()`가 이미 미지 키를 걸러내므로 옛 값은 조용히 기본 False로 시작 — 마이그레이션 불필요

---

## 2. `ui/widgets/chat_panel.py` (신설)

```
┌─ 검색 결과 영역 전체 ──────────────────────────┐
│ [나] 코치인증자격시험 응시방법 찾아줘            │
│ [AI] 1. 시험단계: 서류전형 ---> 필기전형 --->    │  ← ① 즉시, 원문 그대로
│      실기전형 → 최종합격                        │     (SearchWorker 결과의
│      2. 시험방법 1)서류전형: ...                │      top-1 content)
│      [AI 요약 보기]  [파일 열기 ↗]              │
│                                                │
│      ┄┄┄ (버튼 클릭 후) ┄┄┄                    │
│      ┌ AI 요약 ──────────────────────┐         │
│      │ 응시방법은…입니다. [1]  확인필요│         │  ← ② 클릭 시에만
│      └──────────────────────────────┘         │     (SummaryWorker)
│                                    (스크롤)     │
├────────────────────────────────────────────┤
│ [질문 입력                          ] [보내기] │
└────────────────────────────────────────────┘
```

**턴(turn) 단위 구조**: 메시지 하나 = 사용자 말풍선 + AI 말풍선(발췌 + 두 버튼 + 요약 자리). 각 턴이 자기 `results`(①에서 받은 것)와 `request_id`를 들고 있어야 ②를 그 턴에 정확히 반영할 수 있다.

- **말풍선 본문은 PlainText**(요약 카드와 같은 이유 — 문서 내용에 `<`가 섞일 수 있다). `ui/highlight.py:highlighted_excerpt()` 재사용 검토(질의어 강조) — 우선순위 낮음, 시간 되면
- **비-텍스트 청크(표/이미지)가 top-1일 때**: 표는 `TableData`를 텍스트로 평탄화해 보여주거나 "표 형식입니다 — 파일 열기로 확인하세요"로 대체, 이미지는 파일명 + 안내 문구. 구현 중 실제로 몇 번이나 나오는지 보고 정한다 — 지금은 텍스트 청크만 상세 설계
- **"AI 요약 보기" 클릭** → 그 턴의 `results`로 `SummaryWorker` 기동, 버튼을 "생성 중…"으로 바꾸고 완료 시 `Summary` 상태 렌더링(정상/기권/근거없음/실패 + "확인 필요" 배지) — `summary_card.py`의 상태 분기 로직을 그대로 옮겨온다
- **"파일 열기"** → top-1 발췌의 `file_path`로 `card_common.open_source_file()` 그대로 호출. 실패 시 사유를 그 턴 안에 표시(기존 `open_failed` relay 패턴)

### `ResultList` — 기존 "특수 상태" 패턴 확장

`show_chat_mode(panel: ChatPanel)` 추가 — `_clear()` 후 카드 대신 `ChatPanel` 하나로 레이아웃을 채운다. `show_results()` 등 카드 경로는 무수정. `card_count()`는 `ChatPanel`이 `objectName("ResultCard")`를 안 쓰므로 영향 없음.

---

## 3. 워커 배선 — `ui/main_window.py`

- `_on_ai_summary_toggled` → `_on_ai_chat_toggled`로 리네임. 켜지면 `result_list.show_chat_mode(panel)`, 검색어를 그대로 첫 메시지로 자동 전송(패널이 비어있지 않게). 끄면 `show_results()`로 복귀
- **① 즉시 발췌**: `ChatPanel`이 메시지를 보내면 `MainWindow`(또는 패널 자신)가 `SearchWorker(db_path, question, request_id, embedder=self._embedder, profile=...)`를 그대로 기동 — 🔴 **`profile=` 반드시 포함**(T10.9 재발 방지, 이미 `SearchWorker`에 고쳐져 있으니 그대로 쓰면 자동으로 지켜짐). `succeeded` → 그 턴에 top-1 발췌 렌더링
- **② AI 요약**: 그 턴의 "AI 요약 보기" 클릭 → `SummaryWorker(question, turn.results, self._slm_service, request_id)` 기동(무수정) → `succeeded`/`failed` → 그 턴 안에 요약 표시
- **워커 참조 보관**: `_active_workers`(검색)·`_active_summary_workers`(요약) 두 세트 **그대로 재사용** — 이제 "검색마다 하나"가 아니라 "턴마다 여러 개 누적"될 수 있으므로 discard 타이밍만 확인(기존 `finished` 람다 패턴이 이미 이 문제를 다룬다 — set이라 여러 개 동시 보관 가능)
- `closeEvent`: 두 세트 모두 대기 후 `_slm_service.shutdown()` — 순서 기존과 동일

---

## 4. 테스트

| 계층 | 대상 |
|---|---|
| UI | 토글 ON→`show_chat_mode`+첫 메시지 자동 전송, 메시지 전송→`SearchWorker` 기동→턴에 발췌 렌더링(LLM 미호출 확인), "AI 요약 보기" 클릭→그 턴 `results`로 `SummaryWorker` 기동→상태 렌더링, 여러 턴 연속 생성해도 워커 세트가 GC 크래시 없이 버티는지, "파일 열기"가 top-1 경로로 동작 |
| 회귀 | 🔴 `SearchWorker`/`SummaryWorker`에 `profile=` 전달이 챗봇 경로에서도 유지되는지(T10.9) |
| 종단(`slow`) | 실물 llama-server로 "AI 요약 보기" 실제 클릭 1건 |

새 로직이 거의 배선(기존 두 워커를 턴 단위로 조합)이라 `slm/prompt.py`·`search/hybrid_search.py`는 **테스트 변경 없음**. `tests/test_ui_summary.py`는 챗봇 패널 테스트로 이름·대상만 옮긴다.

---

## 5. 검증

1. `pytest -q -m "not slow"` — 592 passed 유지 + 신규 통과
2. **목표 시나리오**: "코치인증자격시험 응시방법 찾아줘" → 즉시 원문 발췌 확인(체감 무지연) → "AI 요약 보기" 클릭 → 생성 요약 확인
3. 표/이미지 청크가 top-1으로 걸리는 질의로 렌더링 확인(§2의 미결 사항)
4. 여러 턴 연속 + 여러 "AI 요약 보기" 동시/연속 클릭 → 크래시 없음
5. 채팅 중 창 닫기 → llama-server 고아 프로세스 없음

---

## 6. 모델 전환 지점

- **여기까지(2단 구조 설계)**: Opus
- ⬅️ **지금부터 Sonnet 가능** — §1~4가 전부 기존 워커·패턴을 조합하는 배선 작업이다. 새 아키텍처 결정이 안 남았다
- 실측(§5)에서 예상 밖 문제(표/이미지 렌더링이 지저분하다 등)가 나오면 그 판단만 다시 Opus 고려

## 7. 문서 갱신

TASK에 Phase 7.6 절 신설, PLAN에 실행 결과, CLAUDE.md 한 줄 요약, DESIGN §5.8(AI 요약 카드) → 챗봇 패널 명세로 교체, PROMPTS에 Phase 7.6 항목, `archive_plan --all`. 커밋은 저사양 PC에서.

---

<!-- 출처: sequential-snacking-puddle.md · DESKTOP-V42GJBP · 작성 2026-08-10 15:48 · 아카이브 2026-08-10 17:02 -->

# Phase 7.5: KURE-v1 임베딩 변환 파이프라인

## Context

`KURE-v1`(고성능 모드 임베딩)은 Phase 3에서 "허깅페이스 레포에 ONNX가 없다"는 이유로 변환을 미뤘고, Phase 4에서 모델 관리 화면에 **"준비 중" 배지 + 비활성 버튼**으로만 노출했다. 이후 Phase 6·7 내내 "별도 Phase에서 재검토"로만 언급되며 Phase 10 백로그에도 못 오른 채 방치됐다. Phase 7을 마친 뒤 사용자가 "고성능 모드는 아직도 준비 중으로 나오는데?"라고 지적해 **Phase 8보다 우선순위를 올려 신설**했다.

목표는 safetensors → ONNX(int8) 변환 파이프라인을 만들어 고성능 모드를 실제로 쓸 수 있게 만들고, Phase 4부터 검증 불가 상태로 남아 있던 **T4.11b(고성능 전환 → 재인덱싱 종단 검증)**를 닫는 것이다.

### 착수 전 조사에서 드러난 것 (2026-08-10)

| 항목 | 기존 문서/코드 | 실제 (HF 재조회) | 영향 |
|---|---|---|---|
| **풀링 방식** | (미확인, mean 가정) | 🔴 **CLS 풀링** (`1_Pooling/config.json`) | **런타임 코드 재사용 불가** — 아래 §2 |
| `max_seq_length` | `settings.py` = 512 | **8192** | 프로파일 정정 |
| 원본 용량 | PLAN §4-C = "2.27GB" | **568MB** (`model.safetensors`) | 문서 오기 — 정정 |
| 베이스 | (미기재) | `BAAI/bge-m3` 파생, XLM-RoBERTa | 토크나이저·입력 형식 확인 필요 |

🔴 **가장 중요한 발견은 풀링 방식이다.** 기존 `Embedder`는 `ko-sroberta-multitask`의 `modules.json`에 맞춰 **mean pooling을 코드에 하드코딩**해뒀다(`indexer/vector/embedder.py:_mean_pool_and_normalize`). KURE-v1에 그대로 쓰면 **예외 없이 조용히 잘못된 벡터**가 나와 검색 품질만 나빠진다 — 착수 전 조사에서 잡지 못했다면 "고성능 모드가 왜 더 나쁘지?"로 한참 헤맸을 종류의 결함이다.

**변환 방식은 직접 구축으로 확정** [사용자 확정, 2026-08-10].

검토 과정에서 커뮤니티 int8 변환본(`challychoi/KURE-v1-onnx-int8`, 591MB)을 발견했고 이걸 그대로 쓰면 구현량이 크게 줄지만, **채택하지 않았다.** 다운로드 34회짜리 개인 재업로드라 변환이 제대로 됐는지 확인할 근거가 없고, 임베딩 모델이 조용히 틀리면 예외 없이 **검색 품질 저하로만** 나타나 원인을 찾기 어렵다. 사내 문서 검색 시스템에는 출처가 명확하고 우리가 직접 재현·검증할 수 있는 경로가 맞다고 판단했다.

→ 공식 `nlpai-lab/KURE-v1` safetensors에서 우리가 직접 변환하고, `sentence-transformers` 정식 추론 결과를 참조 벡터로 삼아 변환 정확성을 수치로 검증한다(§4).

---

## 1. 변환 파이프라인 — `scripts/convert_kure.py` (신설)

**torch·optimum은 이 스크립트 전용 빌드타임 의존성이다.** Phase 3에서 어렵게 걷어낸 117MB를 런타임에 다시 들이면 안 된다(TECH 9.2 인스톨러 예산).

**격리 방식**: 프로젝트 `.venv`를 건드리지 않고 **별도 `.venv-convert`**(gitignore 대상)를 만들어 거기서만 변환을 돌린다. 끝나면 통째로 지운다.

```
py -3.14 -m venv .venv-convert
.venv-convert/Scripts/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv-convert/Scripts/pip install optimum[onnxruntime] sentence-transformers
```

변환 흐름:

1. `optimum`의 `ORTModelForFeatureExtraction.from_pretrained(export=True)`로 fp32 ONNX 추출
2. `onnxruntime.quantization.quantize_dynamic`으로 int8 양자화
3. `tokenizer.json`을 같은 폴더에 배치 → `models/KURE-v1/{model.onnx, tokenizer.json}` (기존 `ModelProfile` 구조 그대로)
4. **검증용 참조 벡터**를 `sentence-transformers` 정식 추론으로 뽑아 `.npy`로 저장 — 변환 venv를 지운 뒤에도 프로젝트 venv에서 대조할 수 있어야 한다

**⚠ 예상 함정 — 2GB protobuf 한계**: fp32 export가 약 2.3GB라 단일 `.onnx`에 안 들어가 `model.onnx` + `model.onnx_data`(external data)로 쪼개진다(커뮤니티 fp32 변환본 `NetMD/KURE-v1-onnx`가 정확히 이 구조다). 양자화 단계가 external data를 제대로 읽는지, 결과물은 단일 파일로 나오는지 확인해야 한다.

**디스크**: 현재 여유 16GB(91% 사용). 피크 사용량 ≈ 툴체인 3GB + safetensors 0.6GB + fp32 2.3GB ≈ 6GB. 변환 후 중간물·`.venv-convert`를 정리해 최종 570MB만 남긴다.

---

## 2. 🔴 풀링 방식 분기 — `config/settings.py` + `indexer/vector/embedder.py`

지금 구조는 모델마다 다른 풀링을 표현할 방법이 없다.

- `ModelProfile`에 **`pooling: str = "mean"`** 필드 추가 → `LIGHT`는 `"mean"`(현행 유지), `HEAVY`는 **`"cls"`**
- `Embedder._encode_batch()`가 프로파일을 보고 분기. `_mean_pool_and_normalize()`는 그대로 두고 `_cls_pool_and_normalize()`(첫 토큰 + L2 정규화)를 추가
- `HEAVY.max_seq_length` 512 → **8192**, `files` 튜플은 변환 산출물 구조에 맞게 정정

**청킹은 이번 Phase에서 바꾸지 않는다** [결정]. `DEFAULT_MAX_TOKENS=120`은 ko-sroberta의 128 한계에 맞춘 값이라 KURE-v1의 8192를 못 살리지만, 재청킹하면 `chunk_id`가 전부 바뀌어 **T10.5에서 겪은 것처럼 Phase 6 sLM 테스트셋이 또 무효화**된다. 청크 크기는 검색 입도(granularity) 문제라 모델 한계와 별개이기도 하다 — 별도 과제로 남긴다.

---

## 3. 배포 경로 — 다운로드가 아니라 "변환 후 복사"

우리가 만든 아티팩트는 허깅페이스에 없으므로 `download_profile()`로 받을 수 없다. **sLM·LibreOffice와 같은 방침**을 따른다(TECH 9.1/9.3):

- 인터넷 되는 PC에서 변환 스크립트 1회 실행 → `models/KURE-v1/` 생성
- 오프라인 PC로는 `models/` 폴더째 복사 (PRD 6장의 기존 배포 경로 그대로)
- 모델 관리 화면(`ui/widgets/model_manager_dialog.py`)의 KURE-v1 행: "준비 중" → **설치 상태 실검사 + 안내 팝업**("변환 스크립트를 실행하거나 models/ 폴더를 복사하세요"). Phase 7에서 만든 `_SlmRow`의 안내 팝업 패턴을 그대로 본뜬다

---

## 4. 검증 (T7.5.3·T7.5.4·T7.5.6·T7.5.7)

| 항목 | 방법 | 판단 기준 |
|---|---|---|
| 변환 정확성 | ONNX 벡터 vs `sentence-transformers` 참조 벡터(§1-4) 코사인 대조 | fp32는 >0.999, int8 열화폭을 수치로 기록 |
| 양자화 재현성 | Phase 3과 같은 측정 — 배치 vs 단건, 자기 유사도 | Phase 3 선례(ko-sroberta는 CPU 따라 0.94~0.98)와 비교 |
| **처리량** | 실문서 재인덱싱 시간 측정 | 🔴 **의사결정 지점** — 아래 |
| T4.11b 종단 | 고성능 전환 → `embed_missing()` → 검색 | 1024차원 벡터가 `chunk_vectors`에 정상 저장·조회 (스키마는 이미 `(chunk_id, model, dim, vector)`라 변경 불필요) |
| 검색 품질 | 같은 질의 세트로 경량 vs 고성능 재순위 비교 | Phase 3의 "벡터 재순위가 항상 개선은 아니다" 선례 참고 |

🔴 **처리량이 의사결정 지점이다.** KURE-v1은 568M 파라미터로 ko-sroberta(~110M)의 5배이고 차원도 768→1024다. 권장 사양에서 ko-sroberta가 38청크/초였는데 KURE-v1이 5~10배 느리면 **문서 1천 개(사용자 계획) 인덱싱이 몇 시간 단위**가 된다. 측정 후 "품질 개선폭이 이 비용을 정당화하는가"를 사용자와 확정한다 — 안 되면 고성능 모드는 만들어두되 기본값은 경량 유지.

---

## 5. 테스트

기존 패턴(`tests/test_vector_embedder.py`)을 따른다. **모델 없이 도는 것과 모델이 필요한 것을 나눈다**(`conftest.py`의 `embedder` 픽스처가 이미 미설치 시 skip 처리).

- 순수 로직: `_cls_pool_and_normalize()` 정확성(첫 토큰 선택·정규화), 프로파일 `pooling` 필드 분기
- 🔴 **회귀 방지**: `LIGHT`가 여전히 mean 풀링을 쓰는지 — 분기를 잘못 넣어 경량 모델까지 CLS로 바뀌면 **기존 인덱스 전체가 조용히 무효**가 된다
- 모델 필요(skip 가능): KURE-v1 실추론, 차원 1024 확인, 참조 벡터 대조

---

## 6. 실행 순서와 모델 전환 지점

1. **T7.5.1~T7.5.4 (Opus 구간)** — 변환 스크립트, 풀링 분기, 정확성·재현성 검증. 설계 판단이 몰려 있다
2. ⬅️ **여기서 Sonnet으로 전환 가능** (아티팩트·풀링이 확정된 뒤)
3. **T7.5.5~T7.5.7 (Sonnet 구간)** — 모델 관리 UI 연동, T4.11b 종단 검증, 품질·처리량 비교 측정

## 7. 문서 갱신 (Phase 완료 체크리스트)

TASK T7.5.1~T7.5.7 체크박스, PLAN Phase 7.5 실행 결과, CLAUDE.md 한 줄 요약, **PLAN §4-C의 "원본 2.27GB" 오기 정정**, `python -m scripts.archive_plan --all`.

**커밋은 이 PC에서 하지 않는다** — 저사양 PC에서 진행한다.

---

<!-- 출처: sequential-snacking-puddle.md · DESKTOP-V42GJBP · 작성 2026-08-10 14:19 · 아카이브 2026-08-10 15:07 -->

# Phase 7: sLM 답변 생성 옵션 모드

## Context

Phase 4에서 사이드바에 "AI 요약 보기" 토글을 만들었지만 **비활성 + "Phase 7에서 지원 예정" 툴팁**으로 두었다(`ui/widgets/search_options.py:33-35`). Phase 6에서 후보 4종을 실측해 **권장 사양 채택 모델을 Qwen3.5-4B로 확정**했고(2026-08-10, 메모리 4.8GB·중앙 지연 18.3초), 이제 그 토글을 실제 동작으로 바꾼다.

핵심은 기능 추가가 아니라 **할루시네이션 억제**다. TECH 5.2가 "추출형 검색이 기본값"인 이유가 그것이고, Phase 6 실측에서 채택 모델조차 객관식 발췌에 대해 **발췌에 없는 답을 근거 번호까지 붙여 지어내는** 실패를 보였다(26문항 중 2건). 그래서 TECH 5.3의 4단계 안전장치를 전부 구현하되, 설계 철학은 "할루시네이션을 100% 막는다"가 아니라 **"사용자가 즉시 검증할 수 있는 구조"**로 간다.

착수 시 확정한 결정 3건 [사용자 확정, 2026-08-10]:

| 결정 | 채택 | 이유 |
|---|---|---|
| sLM 서버 수명주기 | **유휴 5분 후 자동 종료** | 이 PC(16GB)에서 안드로이드 스튜디오와 동시 작업이 전제 — 안 쓰는 동안 4.8GB를 물고 있으면 안 된다 |
| 4단계 적용 범위 | **항상 켜기** (사양 분기 없음) | 겹침도는 문자열 연산이라 비용 ≈ 0이고, 애초에 sLM을 켤 수 있는 PC면 이미 권장 사양이다 |
| 요약 출력 위치 | **결과 목록 맨 위 요약 카드** | DESIGN에 출력 명세가 없어 새로 정함. 기존 `ResultList` QVBoxLayout에 그대로 들어가고, 요약과 근거 카드를 한 화면에서 대조할 수 있다 |

---

## 1. 서버 수명주기 — `slm/runtime.py` 리팩터 + `slm/service.py` 신설

현재 `runtime.llama_server()`는 `@contextmanager`다(`slm/runtime.py:191-258`). 블록을 벗어나면 죽으므로 "띄워두고 재사용"에 맞지 않는다.

**리팩터**: `_start_server() -> (ServerHandle, Popen)` / `_stop_server(popen)`로 쪼개고, 기존 `llama_server()` 컨텍스트 매니저는 이 둘을 감싸는 얇은 래퍼로 남긴다 — `scripts/benchmark_slm.py:174-177`과 `tests/test_slm_runtime.py`가 그대로 통과해야 한다.

**`slm/service.py` (신설)** — 프로세스를 하나만 유지하는 서비스:

```
SlmService
  ensure_ready()      기동돼 있으면 즉시, 아니면 서버를 올린다 (4.7초)
  summarize(...)      요약 1건 생성. 호출할 때마다 유휴 타이머 리셋
  shutdown()          서버 종료 (앱 종료 시 필수)
  is_running / status  UI 표시용
```

- **유휴 타이머**: `threading.Timer`로 마지막 요청 + `SLM_IDLE_TIMEOUT_SEC`(기본 300초) 뒤 `shutdown()`. 새 요청이 오면 타이머 취소 후 재설정.
- **`threading.Lock`으로 직렬화**: UI 워커 스레드에서 호출되고, 검색이 겹치면 동시 진입한다. 기동 중 두 번째 요청이 들어와 서버를 두 번 띄우는 것을 막는다.
- **`profile.extra_server_args`를 반드시 넘긴다** — Qwen3.5는 `--reasoning off`가 없으면 300토큰을 사고에 쓰고 **빈 응답**을 준다(Phase 6 실측, `config/settings.py:152`).
- 기동 실패(모델 없음·메모리 부족 등)는 예외를 삼키지 않고 사유 문자열로 올려 요약 카드에 그대로 보여준다.

**🔴 앱 종료 시 반드시 `shutdown()`** — 안 하면 4.8GB짜리 llama-server 프로세스가 고아로 남는다. `MainWindow.closeEvent`(`ui/main_window.py:194-211`)에 추가한다. Phase 4·6·T10.4에서 반복해 밟은 자리다.

---

## 2. 4단계 안전장치

### 1단계 — 유사도 임계값 (T7.1) → `slm/summarize.py`
`HybridResult.similarity`가 이미 계산돼 있다(`search/hybrid_search.py:143-146`). `SIMILARITY_THRESHOLD`(0.5, `config/settings.py:21`)를 **재사용**한다 — DESIGN §5.6의 "관련성 낮음" 기준과 같은 상수여야 화면과 요약이 어긋나지 않는다.

- `similarity >= 0.5`인 결과만 발췌 후보로 삼는다 (= `not is_low_relevance`)
- **`similarity is None`(임베딩 미사용/실패)은 부적격으로 본다** — 판단 근거가 없는데 요약하면 1단계 취지가 무너진다
- 적격 발췌 0건이면 **sLM을 호출하지 않고** "관련 문서를 찾을 수 없습니다"를 반환

### 2단계 — 근거 강제 프롬프트 (T7.2) → 기존 자산 재사용
`slm/prompt.py`의 `SYSTEM_PROMPT` / `build_messages()`를 **그대로 쓴다**. Phase 6에서 4회 반복해 다듬고 26문항으로 검증한 것이고, 규칙을 user 메시지에 싣는 이유(EXAONE 템플릿이 system을 버림)도 거기 문서화돼 있다. `LlamaClient.chat(temperature=0.0)`이 이미 기본값이라 low temperature 요구도 충족한다.

### 3단계 — 문장 단위 출처 표기 (T7.3) → `expand_citations()`
> **설계 판단 [제안]**: TECH는 `[파일명, 페이지/슬라이드]`를, 기존 프롬프트는 `[N]` 번호를 쓴다. **모델에게 파일명을 직접 쓰게 하지 않는다** — 4B급에 파일명을 인라인으로 적게 하면 그 파일명 자체를 지어낼 여지가 생긴다. Phase 6 실측에서 두 모델 모두 `[1]`~`[3]`을 안정적으로 출력했으므로, **번호는 모델이 달고 표시 단계에서 결정론적으로 치환**한다.

`slm/prompt.py`에 추가: `expand_citations(answer, excerpts) -> str`
- `\[(\d+)\]` → `[{file_name}, {location}]`. `location`은 `search/chunk_view.py:format_location()` 재사용 (결과 카드와 같은 규칙 — `tests/test_slm_prompt.py:43-50`이 이 일치를 이미 지키고 있다)
- 발췌 범위를 벗어난 번호(예: 발췌 3건인데 `[5]`)는 치환하지 않고 **검증 실패 신호로 넘긴다** → 4단계가 "확인 필요"로 표시

### 4단계 — 답변-근거 겹침도 (T7.4) → `slm/verify.py` (신설)
순수 로직이라 모델 없이 테스트된다.

- 답변을 문장 단위로 자른다 (`indexer`의 정규식 분리 방식 재사용 — `kss`는 Phase 3에서 성능 문제로 걷어냈다)
- 문장별 **문자 bigram 겹침 비율**을 발췌 전체 텍스트 대비 계산. 한국어는 조사·어미가 붙어 어절 단위 매칭이 잘 안 걸려 문자 n-gram이 낫다. `slm/prompt.py:_normalize()`(공백 제거)를 재사용
- 한 문장이라도 `SLM_OVERLAP_THRESHOLD`(**0.6 [제안]** — 구현 중 실제 답변으로 조정) 미만이면 카드에 **"확인 필요"** 배지
- 범위 밖 인용 번호가 있어도 "확인 필요"
- 기권 응답(`is_abstention()`)은 검증 대상에서 제외

---

## 3. UI

### 요약 카드 — `ui/widgets/summary_card.py` (신설)
`objectName`은 **`"AiSummaryCard"`** — `"ResultCard"`로 하면 `ResultList.card_count()`(`result_list.py:99`)에 잡혀 기존 테스트 전부가 1씩 밀린다.

표시해야 하는 상태:

| 상태 | 문구 |
|---|---|
| 서버 기동 중 | "AI 모델을 준비하는 중입니다…" (첫 요청 4.7초) |
| 생성 중 | "AI 요약을 만드는 중입니다…" |
| 정상 | 답변 + 문장별 `[파일명, 위치]` + (필요 시) **확인 필요** 배지 |
| 기권 | "문서에서 찾을 수 없습니다." |
| 1단계 차단 | "관련 문서를 찾을 수 없습니다" |
| 실패 | 사유 그대로 (모델 미설치 / 기동 실패 등) |

QSS는 `ui/qss/app.qss`에 `#AiSummaryCard` 계열 셀렉터를 추가한다(기존 `#ResultCard` 스타일을 본뜬다).

### 워커 — `ui/summary_worker.py` (신설)
`ui/search_worker.py`를 그대로 본뜬다: `QThread`, `succeeded = Signal(int, object)` / `failed = Signal(int, str)`, `request_id`로 늦게 온 결과 폐기.

**🔴 `MainWindow._active_workers`와 같은 방식으로 참조를 붙들어야 한다** — 한 자리에만 두면 다음 요청이 덮어쓰는 순간 실행 중인 QThread가 GC돼 앱이 통째로 죽는다(0xC0000409, Phase 6에서 실측·수정한 그 버그).

### 배선 — `ui/main_window.py`, `ui/widgets/search_options.py`, `ui/state.py`
- `SearchOptions`: 토글 활성화 + `ai_summary_changed` 시그널 추가. **sLM 미설치면 계속 비활성**, 툴팁을 "모델 관리에서 AI 요약 모델을 설치하세요"로 교체
- `AppState`: `ai_summary_enabled: bool = False`(기본 OFF — PRD/DESIGN §1), `slm_profile: str` 필드 추가
- `MainWindow`: 검색 성공 후 토글 ON이면 요약 시작 / 토글을 끄면 요약 카드 제거 / 토글을 켜면 마지막 결과로 즉시 생성 / `closeEvent`에서 `SlmService.shutdown()`
- `ResultList`: `show_summary(state)` · `clear_summary()` — `insertWidget(0, ...)`으로 맨 위에 넣고, `_clear()`가 요약 카드도 함께 지우도록 한다

---

## 4. 모델 매니저 (T7.6~T7.10) — `ui/widgets/model_manager_dialog.py`

현재 임베딩 섹션만 있고 sLM은 안내 문구 한 줄이다(`:38, :70-72`). 그 자리에 실제 섹션을 넣는다.

> **제품이 제공하는 모델은 2종뿐이다.** `SLM_CANDIDATES` 4종은 Phase 6 **측정 하네스가 계속 참조**하므로(`scripts/benchmark_slm.py --models`) 그대로 두되, 제품 UI에 노출할 목록을 `SLM_OFFERED`(가칭)로 따로 뺀다:
>
> | 모델 | 제품 노출 | 근거 |
> |---|---|---|
> | `qwen3.5-4b` | ✅ 권장 사양 | 2026-08-10 채택 확정 |
> | `exaone-4.0-1.2b` | ✅ 최소 사양 | "켠다면 이것" (PLAN §6-B) |
> | `exaone-3.5-7.8b` | ❌ | 측정만 하고 Qwen에 밀려 탈락 — 노출하면 "메모리 때문에 Qwen을 골랐다"는 결정과 어긋나는 4.77GB 선택지를 다시 권하는 꼴 |
> | `phi-4-mini` | ❌ | 준수율 문제로 전 사양 제외 |

- **T7.6 sLM 섹션**: `_ModelRow`를 본떠 `_SlmRow`를 만든다 (`SlmProfile`은 `local_dir`이 아니라 `local_path`, 크기 표시 필요). 상태는 항상 실제 파일을 검사해 판정 — 하드코딩 금지(기존 주석 `:10-11`의 원칙)
- **T7.7 다운로드 안내 팝업**: 링크·파일명·용량·**SHA256**·저장 위치. `SlmProfile`에 `sha256` 필드를 추가하고, **제품 노출 2종(qwen3.5-4b·exaone-4.0-1.2b)의 실제 해시를 이 PC의 파일에서 계산해 기록**한다(둘 다 이미 있다). 나머지 2종은 빈 값 → 검증 생략
- **T7.8 폴더 열기**: 기존 `_open_folder()`(`:88-92`) 재사용
- **T7.9 새로고침 검증**: 크기 검사는 즉시(기존 `download._verify()` 재사용), **SHA256은 GB 단위라 백그라운드 스레드**로 돌리고 진행 표시 — UI 블로킹 금지
- **T7.10 사양별 모델**: 위 2종 매핑(권장=`qwen3.5-4b` / 최소=`exaone-4.0-1.2b`)을 `config/settings.py`에 두고 선택값은 `AppState`에 저장. 기본값은 **권장=Qwen3.5-4B**(이 PC 기준)

---

## 5. 테스트 (T7.11 포함)

기존 3계층 패턴을 그대로 따른다:

| 계층 | 방식 | 대상 |
|---|---|---|
| 순수 로직 | 모델·I/O 없음 | 1단계 필터, `expand_citations()`, `slm/verify.py` 겹침도, 문장 분리 |
| 서비스 | `ThreadingHTTPServer` 스텁(`tests/test_slm_runtime.py:126-171`) + monkeypatch | `SlmService` 기동/재사용/**유휴 타임아웃 종료**/동시 호출 직렬화 |
| UI | pytest-qt (`qtbot`, `findChild`) | 요약 카드 6개 상태, 토글 ON/OFF 흐름, `card_count()` 불변 확인 |
| 종단 | `@pytest.mark.slow` + 모델/바이너리 없으면 skip | 실제 요약 1~2건 |

**T7.11 회귀 테스트**: Phase 6 테스트셋(`data/slm_testset.json`, 26문항)을 **앱의 요약 경로(1~4단계 전부 통과)로** 돌려 Phase 6 순수 추론 결과(기권정확도 81.8% / 응답정확도 80.0%)와 대조한다. 안전장치가 정확도를 떨어뜨리지 않았는지, 특히 **Phase 6에서 지어냈던 객관식 2건을 4단계가 "확인 필요"로 잡아내는지**가 관전 포인트다.

---

## 6. 검증 (자동화 테스트만으로는 부족)

Phase 4·5·6·10에서 **자동화 테스트를 전부 통과한 채로 남아 있던 버그를 실제 앱을 띄워서야 잡았다**(QSS 폴리시 타이밍, 신호 미수신, 겹친 검색 크래시, 인덱싱 후 닫기 `AttributeError`). 이번에도 실행 검증을 DoD로 취급한다:

1. `./.venv/Scripts/python.exe -m pytest -q` — 기존 488 passed 유지 + 신규 통과
2. 실제 앱 실행 후 육안 확인:
   - 토글 OFF→ON 첫 요약 (서버 기동 4.7초 안내가 뜨는지)
   - 연속 요약 (재기동 없이 바로 응답하는지)
   - **5분 방치 후 서버가 실제로 내려가 메모리가 반환되는지** (작업 관리자로 확인)
   - 요약 생성 중 다른 검색을 던져 겹치게 하기 (크래시·잔상 확인)
   - 요약 생성 중/후 창 닫기 → **llama-server 고아 프로세스가 남지 않는지**
   - 모델 미설치 상태에서 토글 (비활성 + 안내가 맞는지)
3. `python -m scripts.benchmark_slm --rescore` 계열로 T7.11 회귀 수치 대조

## 7. 문서 갱신 (Phase 완료 체크리스트)

TASK T7.1~T7.11 체크박스, PLAN §7 실행 결과, CLAUDE.md 한 줄 요약, **DESIGN에 요약 카드 명세 신규 절**(지금 문서에 출력 영역 명세가 아예 없다). `python -m scripts.archive_plan --list`로 이 계획 원문 아카이브.

**커밋은 이 PC에서 하지 않는다** — 저사양 PC에서 진행한다.

---

# Phase 6: sLM 후보군 실측 검증 구현 계획

## Context

Phase 5까지 완료돼 추출형 검색(376 passed)은 끝났다. Phase 7의 "AI 요약" 옵션 모드를 붙이려면 **어떤 sLM을 쓸지** 먼저 정해야 하는데, TECH 10장이 이 항목을 오픈 이슈로 명시했다 — **"문서에 없으면 모른다고 답하는가"는 설계로 보장할 수 없어 실측이 유일한 방법**이다. Phase 7의 안전장치 2번(근거 강제 프롬프트)이 이 결과에 직접 의존한다.

이번 Phase의 산출물은 모델 하나가 아니라 **재현 가능한 측정 하네스 + 비교 데이터**다. 하네스를 제대로 만들면 나중에 후보를 갈아끼우는 비용이 거의 없다.

## 사전 조사 결과 (이 PC에서 직접 실측·확인)

| 항목 | 확인 내용 |
|---|---|
| `llama-cpp-python` | **Python 3.14용 사전 빌드 휠 없음**(`pip install --only-binary=:all:` 실패). 소스 컴파일에 CMake+MSVC 필요 → PRD 4장 "관리자 권한 불필요"와 충돌 |
| llama.cpp 공식 바이너리 | `llama-b10299-bin-win-cpu-x64.zip` **18MB**, 컴파일 불필요 |
| 이 PC 사양 | RAM **15.6GB**(≈권장 사양), Intel Core Ultra 5 125U, 12코어/14스레드, D: 여유 52.8GB. **현재 여유 RAM은 3GB뿐** |
| 인덱스 실문서 | `data/index.sqlite3`에 **17개 문서** — 구버전(.doc/.xls), .xlsx/.hwpx, .hwp, 생성 샘플이 고루 섞여 있다. 실제 업무 문서라 문서명·기관명은 남기지 않는다 |

**후보 모델 (사용자 확정: 2026 최신 위주, HF에서 실파일 크기 확인 완료)**

| 모델 | Q4_K_M 크기 | 배포처 | 역할 |
|---|---|---|---|
| EXAONE-4.0-1.2B | **0.81 GB** | LGAI-EXAONE(공식) | 최소 사양 주력, 한국어 특화 |
| Phi-4-mini-instruct | 2.49 GB | unsloth | 중간 |
| Qwen3.5-4B | 2.74 GB | unsloth | 중간, Apache 2.0 |
| EXAONE-3.5-7.8B-Instruct | 4.77 GB | lmstudio-community | 권장 사양 주력, 한국어 특화 |

합계 약 **10.8GB**. 문서 원안(Qwen2.5-1.5B/7B, EXAONE-3.5, Phi-3.5-mini)은 2024년 기준이라 갱신했다 — 특히 **0.81GB짜리 한국어 특화 모델(EXAONE-4.0-1.2B)은 문서 작성 시점에 없던 선택지**로, 8GB 최소 사양의 실현 가능성을 크게 바꾼다.

## 설계 결정

### ① 추론 런타임 — llama.cpp 사전 빌드 바이너리 + 서브프로세스

`llama-cpp-python`은 위 표대로 **선택지가 아니다**(휠 없음). 공식 사전 빌드 바이너리(18MB)를 `vendor/llamacpp/`에 두고 서브프로세스로 호출한다 — 이 프로젝트가 이미 두 번 쓴 패턴이다(LibreOffice `soffice`, Phase 10 조사의 PowerShell/Word COM). 탐색 로직은 `parser/utils/libreoffice.py`의 `find_soffice()`(환경변수 → PATH → `vendor/` 순)를 그대로 본뜬다.

**`llama-cli`가 아니라 `llama-server`를 쓴다.** 4개 모델 × 약 30문항 = 120회 추론인데, `llama-cli`는 매 호출마다 모델을 다시 로딩해(7.8B는 수 초) 순수 로딩에만 10분 넘게 낭비된다. 더 중요한 건 **측정 타당성**이다 — Phase 7의 실제 앱은 모델을 한 번 올려두고 재사용하므로, 웜 상태의 요청당 지연시간이 실사용에 더 가깝다. 모델 로딩 시간은 별도 지표로 1회씩만 측정한다.

### ② 준수율 지표 — 한 숫자로 보면 안 된다

**무조건 "모른다"만 답하는 모델은 기권율 100%지만 쓸모가 없다.** 정밀도/재현율과 같은 트레이드오프이므로 두 값을 반드시 함께 본다.

| 지표 | 정의 | 방향 |
|---|---|---|
| **기권 정확도** | 근거 **없는** 질문 중 올바르게 "모른다"고 답한 비율 | 높을수록 좋음 (핵심 지표) |
| **과잉 기권율** | 근거 **있는** 질문 중 잘못 기권한 비율 | 낮을수록 좋음 |
| 응답 정확도 | 근거 있는 질문에서 정답 키워드를 포함한 비율 | 높을수록 좋음 |

자동 채점은 **기권 판정**(프롬프트가 지시한 정확한 문구가 답변에 있는지)과 **키워드 포함 여부**로 한다. 이건 근사치이므로, **최종 선정 전에 표본을 사람이 눈으로 확인**하는 단계를 반드시 넣고 그 한계를 리포트에 명시한다.

### ③ 🔴 테스트셋과 개인정보 — 실문서 발췌를 git에 커밋하면 안 된다

사용자가 "현재 인덱싱된 실문서 사용"을 택했고 T6.4도 그걸 요구하지만, **이 저장소는 다른 노트북에서 GitHub로 push될 예정**이다(메모리 기록). 실제 업무 문서의 발췌를 테스트 픽스처에 담아 커밋하면 그대로 공개된다.

**분리 설계로 해결한다:**
- `data/slm_testset.json` — 실문서 기반, **`data/`는 이미 .gitignore 대상**이라 커밋되지 않는다. 실제 준수율 측정에 쓴다
- `tests/fixtures/slm_testset_sample.json` — `generate_samples.py`가 만드는 **합성 문서** 기반. 커밋해도 안전하고 자동화 테스트/CI가 쓴다
- 생성 스크립트를 함께 둬서 Phase 7이 언제든 재생성할 수 있게 한다

리포트(비교표)에는 **수치만** 싣고 문서 내용은 옮기지 않는다.

### ④ 근거 강제 프롬프트 (T6.3) — Phase 7이 그대로 재사용할 자산

`slm/prompt.py`에 템플릿을 둔다. 뼈대:
- 시스템: 주어진 발췌에만 근거해 답하고, 발췌에 없으면 **정해진 문구 그대로** "문서에서 찾을 수 없습니다"라고 답하라 (문구를 고정해야 기권 자동 판정이 가능하다)
- 발췌: `hybrid_search()` 결과 청크를 `[파일명, 위치]` 라벨과 함께 삽입 (Phase 7 T7.3의 출처 표기와 형식을 맞춰둔다)
- 측정 시 `temperature=0`(그리디)로 재현성 확보. 단 Phase 3에서 int8 양자화의 비재현성을 겪었듯 **그리디도 스레드 수에 따라 완전히 동일하진 않을 수 있으므로**, 스레드 수를 고정하고 그 사실을 기록한다

### ⑤ Qwen3.5 thinking 모드 주의

Qwen3 계열은 추론(thinking) 모드가 기본 활성인 경우가 있는데, CPU에서 매우 느리고 근거 기반 단답에는 오히려 방해가 된다. 착수 시 `/no_think` 또는 chat template 옵션으로 비활성화되는지 먼저 확인하고, 비활성 상태로 측정한다(활성 상태도 궁금하면 별도 행으로 기록).

### ⑥ 이 PC는 최소 사양이 아니다 (Phase 3 선례 그대로)

15.6GB는 **권장 사양**이다. 8GB 최소 사양 실측은 Phase 9로 이월하고, 이번 수치는 참고치임을 리포트에 명시한다. 또 **현재 여유 RAM이 3GB뿐**이라 4.77GB짜리 EXAONE-3.5-7.8B 측정 시에는 다른 앱을 닫아야 한다 — 측정 직전 여유 RAM을 기록해 신뢰도 판단 근거로 남긴다.

## 모듈 구조

```
config/settings.py              # SlmProfile 데이터클래스 추가 (기존 ModelProfile 옆에 — 모델 설정은 한 파일에)
slm/
├── __init__.py
├── runtime.py                   # llama.cpp 바이너리 탐색 + llama-server 기동/종료 (find_soffice 패턴)
├── client.py                     # llama-server HTTP 클라이언트 (urllib — download.py가 이미 쓰는 방식, requests 미도입)
├── prompt.py                      # 근거 강제 프롬프트 템플릿 (T6.3) — Phase 7 재사용
└── download.py                     # GGUF 다운로드 (indexer/vector/download.py 패턴 재사용)
scripts/
├── setup_llamacpp.py              # llama.cpp 바이너리 1회 다운로드+압축해제
├── build_slm_testset.py            # 인덱스에서 테스트셋 생성 (T6.4)
└── benchmark_slm.py                 # 준수율·속도·메모리 측정 (T6.5/T6.6) — benchmark_search.py 패턴
tests/
├── fixtures/slm_testset_sample.json  # 합성 문서 기반 (커밋 안전)
├── test_slm_prompt.py                 # 프롬프트 조립 순수 로직
├── test_slm_runtime.py                 # 바이너리 탐색·서버 수명주기 (바이너리 없으면 skip)
└── test_slm_scoring.py                  # 기권 판정·채점 로직
vendor/llamacpp/                       # .gitignore 대상 (LibreOffice와 동일 취급)
models/slm/                            # .gitignore 대상 — GGUF 파일
```

## 구현 순서 (작은 모델로 하네스부터 검증)

10.8GB를 다 받아놓고 하네스가 안 돌면 시간을 크게 버린다. **가장 작은 모델(0.81GB) 하나로 전 구간을 먼저 통과시킨다.**

1. **T6.2 런타임** — `scripts/setup_llamacpp.py`로 18MB 바이너리 확보 → `slm/runtime.py`(탐색+서버 기동) → `slm/client.py`(HTTP) → **EXAONE-4.0-1.2B 하나만 받아 "안녕" 한 마디 응답 확인**
2. **T6.3 프롬프트** — `slm/prompt.py`. 근거 있는/없는 발췌 각 1건으로 기권 문구가 실제로 나오는지 눈으로 확인
3. **T6.4 테스트셋** — `scripts/build_slm_testset.py`. 인덱스에서 청크를 뽑아 질문·정답키워드·기권기대여부를 구성. 실문서용은 `data/`에, 합성 샘플용은 `tests/fixtures/`에
4. **T6.5/T6.6 측정** — `scripts/benchmark_slm.py`. 1번 모델로 전 구간 완주 확인 후, **그때 나머지 3개 다운로드**
5. **T6.7 비교표 + 최종 선정** — 표본 육안 검증 포함. 최소/권장 사양별 채택 모델 확정
6. 문서 갱신(TASK 체크박스, PLAN §6-B, CLAUDE.md, 계획서 아카이브) + 로컬 커밋

## 검증 방법

1. `pytest -q` 전체 통과 (기존 376 + 신규). llama.cpp 바이너리·GGUF가 없는 환경에서는 **사유와 함께 skip**(Phase 1의 LibreOffice·hwp, Phase 3의 임베딩 모델과 동일 패턴)
2. **DoD 핵심**: 후보별 기권 정확도·과잉 기권율·응답 정확도·응답 속도·메모리가 표로 정리되고, 최소/권장 사양별 채택 모델이 근거와 함께 확정될 것
3. 자동 채점 결과 중 **표본을 직접 눈으로 확인**해 채점기가 오판하지 않았는지 검증하고, 그 한계를 리포트에 명시
4. 측정 시 여유 RAM·스레드 수를 함께 기록해 재현 조건을 남긴다
5. **실문서 내용이 커밋 대상에 섞이지 않았는지 `git status`로 확인**(§③)

---

# Phase 5: 표 카드 / 이미지 카드 렌더러 구현 계획

## Context

Phase 4(추출형 검색 UI, 358 passed)가 완료돼 텍스트 청크 검색·카드 렌더링까지는 동작한다. 하지만 백엔드는 이미 표(`type=table`)·이미지(`type=image`) 청크까지 인덱싱·검색하고 있는데(Phase 1~3), UI는 `ResultCard`(텍스트 전용)만 있어 표·이미지 결과가 검색되더라도 화면에 제대로 표현되지 못한다. 이번 Phase는 TECH §6.3의 "청크 타입 기반 동적 라우팅"을 완성해, 검색 로직은 그대로 두고 **렌더링 단계에서만 3종 카드로 분기**한다(DESIGN §5.7).

DESIGN §8의 문서 간 불일치 4건은 이미 Phase 4에서 전부 확정됐다 — 이미지 카드·관련성 낮음 카드 모두 "원문 열기"를 병기하기로 했으므로(§8 확정 3·4), 이번 Phase에서 새로 결정할 디자인 이슈는 없다. 목업(DESIGN §5.4·5.5)이 표/이미지 카드 형태를 이미 확정해뒀고, 백엔드 데이터(`SearchResult.table_json`/`image_json`)도 이미 준비돼 있어 순수 UI 구현 작업이다.

## 백엔드 확인 사항 (읽기 전용 검증 완료)

- `indexer/fts5/search.py`의 `SearchResult`는 이미 `type`(ChunkType enum), `table_json`, `image_json`을 담고 있다 — 스키마 변경 불필요.
- `parser/schema.py`의 `TableData`(`rows`, `header_row`, `caption`)·`ImageData`(`image_path`, `caption`, `width`, `height`, `origin`)는 `asdict()`로 직렬화된 그대로라 `TableData(**json.loads(...))`로 바로 역직렬화된다.
- 이미지 원본은 파싱 시점에 이미 `.assets/<문서명>/` 아래 실파일로 추출·저장되어 있다(`parser/base.py`의 `asset_dir_for`) — 카드가 그릴 때 별도 추출 작업이 필요 없고 파일 존재만 확인하면 된다.
- **놓치기 쉬운 함정 2가지** (PLAN 문서가 이미 경고해둔 것, 실제로 코드로 확인함):
  1. xlsx 표의 위치 표기는 `page_or_slide`(시트 **인덱스**)가 아니라 `TableData.caption`(시트 **이름**, `XlsxParser`가 `sheet.title`을 넣어둠)이어야 한다.
  2. `TableData.from_rows()`는 1행짜리 표에서 `header_row`를 비워 둔다(데이터 소실 방지) — 렌더러가 헤더 없는 표를 반드시 처리해야 한다.

## 설계 결정

### ① 카드 공통 헤더를 함수로 공유 (상속 대신)

현재 `ui/widgets/result_card.py`의 `ResultCard`(텍스트 카드)는 헤더 구성(파일명·구분점·위치·관련성 라벨·원문 열기 버튼)을 직접 짜고 있다. 표·이미지 카드도 같은 헤더가 필요하지만 뒤에 붙는 버튼이 다르다(표: "표 복사", 이미지: "확대"). `QFrame` 다중상속보다 단순한 **빌더 함수**로 공유한다.

새 파일 `ui/widgets/card_common.py`:
- `format_location(result: SearchResult) -> str` — 기존 `result_card.py`에서 이동 + xlsx 표는 `TableData.caption`(시트명) 우선 사용하도록 확장
- `parse_table_data(result: SearchResult) -> TableData | None` / `parse_image_data(result: SearchResult) -> ImageData | None` — `table_json`/`image_json` 역직렬화, 파싱 실패 시 `None`(카드가 방어적으로 처리)
- `build_card_header(hybrid_result, extra_buttons: Sequence[QPushButton] = ()) -> tuple[QHBoxLayout, QPushButton]` — 파일명/위치/관련성라벨/부가버튼/원문열기버튼을 조립하고, `open_button`은 호출부가 클릭 시그널을 연결할 수 있게 반환
- `open_source_file(file_path: str) -> str | None` — 성공 시 `None`, 실패 시 사유 문자열(기존 `ResultCard._open_source`의 로직을 공유 함수로 추출)

`ui/widgets/result_card.py`는 `format_location` 재노출 없이 `card_common`에서 import해서 쓰도록 수정(`tests/test_ui_result_card.py`의 `from ui.widgets.result_card import ResultCard, format_location` 임포트를 `card_common`으로 옮기는 테스트 수정 포함).

### ② 표 카드 — `QTableWidget` (T5.2)

새 파일 `ui/widgets/table_card.py`의 `TableCard(QFrame)`:
- `objectName("ResultCard")`로 텍스트 카드와 동일한 프레임 스타일(QSS `#ResultCard` 규칙) 재사용
- 본문은 `QTableWidget` — 헤더 행은 `setHorizontalHeaderLabels()`(있을 때만, 없으면 `horizontalHeader().setVisible(False)`), 데이터는 `QTableWidgetItem`(읽기 전용 플래그)
- **중첩 스크롤 방지**: `ResultList`가 이미 세로 스크롤을 담당하므로 `QTableWidget` 내부 스크롤바는 끄고(`ScrollBarAlwaysOff`), `resizeRowsToContents()` 이후 실제 행 높이 합으로 `setFixedHeight()`를 계산해 표 전체가 항상 펼쳐진 채로 보이게 한다
- "표 복사" 버튼(`build_card_header`의 `extra_buttons`) → TSV(탭 구분, DESIGN §5.4 제안)로 `QGuiApplication.clipboard().setText()`
- 위치 표시는 `format_location`이 xlsx는 시트명을, 그 외 표는 페이지/슬라이드 번호를 반환

### ③ 이미지 카드 — 썸네일 캐시 + `QDialog` 확대 (T5.3~T5.5)

새 파일 `ui/thumbnail_cache.py`:
- `get_thumbnail_path(chunk_id: str, source_path: Path) -> Path | None` — `data/thumbnails/<safe(chunk_id)>.png` 캐시. 있으면 즉시 반환(TECH 4.4 "캐시만 조회 → 속도 확보"), 없으면 `QImage`로 원본을 열어 폭 300px로 축소 후 저장(PySide6 내장 기능만 사용, Pillow 등 신규 의존성 불필요). 원본이 없으면 `None`
- 캐시 무효화는 이번 Phase 범위 밖(Phase 8 증분 인덱싱이 mtime 기반으로 다룰 문제) — `chunk_id`가 키이므로 문서가 재인덱싱되어 chunk_id가 바뀌면 자연히 새 캐시가 생긴다는 점만 문서화

새 파일 `ui/widgets/image_card.py`의 `ImageCard(QFrame)`:
- 좌측 고정 크기 썸네일(`QLabel` + `QPixmap`, 캐시 300px 원본을 카드에 맞는 표시 크기로 축소), 우측 안내 문구 `"이미지 내 텍스트는 인식되지 않았습니다."`(DESIGN §5.5 확정 문구, T5.4)
- 원본을 찾을 수 없으면 썸네일 자리에 대체 텍스트("미리보기를 표시할 수 없습니다" 등) — 예외로 카드 자체가 깨지지 않게 방어
- "확대" 버튼(`build_card_header`의 `extra_buttons`) → `QDialog`에 원본 이미지를 화면의 80% 이내로 스케일해 표시(목업·TECH 어디에도 확대 동작의 세부 스펙이 없어 **[제안]**으로 가장 자연스러운 해석을 택함 — 별도 확대/축소 인터랙션 없이 크게 보여주기만)

### ④ 타입 기반 라우팅 (T5.1, T5.6)

`ui/widgets/result_list.py`의 `show_results()`에 팩토리 분기 추가:
```python
def _make_card(result: HybridResult, query, case_sensitive, exact_word) -> QWidget:
    if result.type is ChunkType.TABLE:
        return TableCard(result)
    if result.type is ChunkType.IMAGE:
        return ImageCard(result)
    return ResultCard(result, query, case_sensitive, exact_word)
```
`card_count()`는 현재 `isinstance(widget, ResultCard)`로 세는데, 세 타입 모두 `objectName("ResultCard")`를 공유하므로 `widget.objectName() == "ResultCard"` 비교로 바꿔 세 타입을 모두 카운트한다.

## 테스트 전략

- `card_common.py`의 `format_location`(xlsx 시트명 케이스 추가) / `parse_table_data` / `parse_image_data`는 Qt 없는 순수 유닛 테스트
- `TableCard`/`ImageCard`는 `pytest-qt`로 헤더 요소(파일명·위치·부가버튼·원문열기) 존재, 헤더 없는 표 처리, 원본 없는 이미지 처리(방어 코드) 검증
- `test_ui_result_card.py`의 기존 텍스트 카드 테스트는 import 경로만 `card_common`으로 수정, 나머지 그대로 유지(회귀 확인용)
- T5.6: `tests/test_ui_main_window.py`에 text/table/image가 섞인 인덱스로 검색해 `ResultList`에 3종 카드가 함께 렌더링되는 통합 테스트 추가
- 기존 358개 테스트가 그대로 통과하는지 먼저 확인(회귀 없음 확인)

## 검증 방법

1. `pytest -q` 전체 통과 (기존 358 + 신규)
2. 실제 데모 폴더(표·이미지가 포함된 실 문서)로 재인덱싱 → 표/이미지가 포함된 질의로 검색 → 텍스트·표·이미지 카드가 한 리스트에 섞여 나오는지 `QWidget.grab()` 스크린샷으로 시각 검증(자동화 테스트만으로는 실제 UI 버그를 못 잡은 전례가 Phase 4에 있었음)
3. 표 카드: 헤더 음영, "표 복사" 클릭 후 클립보드에 TSV가 담기는지 확인
4. 이미지 카드: 썸네일 표시, "확대" 클릭 시 원본 크기로 뜨는지, 원본이 없는 케이스의 방어 동작 확인
5. 완료 후 TASK 체크박스(T5.1~T5.6), PLAN §5-B 실행 결과, CLAUDE.md 갱신, `PHASE_오프라인RAG시스템_단계별_구현계획서.md`에 이 계획 원문 이어붙이기, 로컬 커밋

---

# Phase 4: 추출형 검색 UI 구현 계획

## Context

Phase 1~3(파서·FTS5·하이브리드 검색, 234 passed)이 완료되어 `search.hybrid_search.hybrid_search()`가 백엔드로 준비됐다. 이번 Phase는 **표/이미지 카드를 뺀 텍스트 카드 중심 MVP**를 완성하는 게 목표이며, 여기까지 되면 1차 배포 가능 지점이다.

착수 전 결정은 전부 끝났다 — UI 프레임워크(PySide6), DESIGN §8 불일치 4건, 모델 관리↔PC 성능 연동(Option A, KURE-v1은 "준비 중")까지 DESIGN·PLAN·TASK 문서에 확정되어 있다. 이번 Phase에서 새로 결정할 것은 DESIGN §13에 남은 2건뿐이다: AI 요약 토글 처리 방식(→ **비활성+툴팁**으로 확정, 근거는 아래), 디자인 토큰 실제 값(→ DESIGN §10 제안값을 그대로 채택).

Phase 4는 **처음으로 동시 DB 접근이 생기는 지점**이다 — 백그라운드 인덱싱(쓰기)과 검색(읽기)이 동시에 일어날 수 있는데, 지금 `schema.connect()`는 기본 롤백 저널 모드라 쓰기 중 읽기가 잠길 수 있다. 이 문제를 먼저 해결한다.

## 모듈 구조

```
ui/
├── __init__.py
├── app.py                    # QApplication 진입점, main()
├── main_window.py             # 레이아웃 셸 조립 (T4.1)
├── state.py                    # AppState: 대상 폴더·DB 경로·모델 프로파일 영속화 (JSON, 레지스트리 미사용)
├── search_worker.py             # QThread — hybrid_search를 UI 블로킹 없이 실행
├── highlight.py                   # 검색어 하이라이트 + 발췌 윈도잉 (T4.13)
├── widgets/
│   ├── search_bar.py                # 검색 입력창 + debounce (T4.2~T4.3)
│   ├── sidebar.py                    # 3블록 컨테이너
│   ├── format_filter.py               # 문서 형식 체크박스군 (T4.4~T4.5)
│   ├── toggle_switch.py                # 공용 토글 컴포넌트 (T4.6)
│   ├── search_options.py                # AI요약/대소문자/일치단어 3토글 (T4.7~T4.9)
│   ├── performance_combo.py              # PC 성능 콤보 (T4.10~T4.11)
│   ├── model_manager_dialog.py            # 모델 관리 팝업 — 임베딩 섹션만 (T4.11a~T4.11b)
│   ├── result_card.py                      # 카드 공통 프레임 + 텍스트 카드 (T4.12~T4.14)
│   ├── result_list.py                       # 스크롤 리스트 + 상태별 화면 (T4.15)
│   ├── status_bar.py                         # 상태바 + 진행바 (T4.16)
│   └── folder_dialog.py                       # 폴더 선택 + 재인덱싱 진입점 (T4.17, 최소 구현)
└── qss/app.qss                 # DESIGN §10 토큰 반영 스타일시트
data/                            # .gitignore 대상 — index.sqlite3 + app_state.json
```

## 핵심 설계 결정 (구현 전 확정)

### ① SQLite WAL 모드 — 동시 접근 문제 선제 해결

`indexer/fts5/schema.py`의 `connect()`에 `PRAGMA journal_mode=WAL`을 추가한다. 기본 저널 모드는 쓰기 중 모든 읽기를 막지만, WAL은 읽기가 쓰기와 동시에 진행된다 — 백그라운드 인덱싱 중에도 검색이 막히지 않아야 하므로 필수다. 기존 Phase 1~3 테스트(233건)가 이 변경으로 깨지지 않는지 먼저 확인한다.

### ② 스레딩 — sqlite3/Embedder 스레드 안전성

- **검색**: `SearchWorker(QThread)`가 자기 스레드 안에서 `connect(db_path)`로 커넥션을 새로 연다(`IndexingThread`와 동일 패턴, sqlite3 커넥션은 만든 스레드에서만 안전).
- **임베더 재사용**: `Embedder`는 최초 로딩이 651ms(Phase 3 실측)라 검색마다 새로 만들면 매번 그 비용을 문다. 앱 시작 시 백그라운드에서 한 번 만들어 `MainWindow`가 들고 있다가 매 검색에 재사용한다. onnxruntime의 `InferenceSession.run()`은 다중 스레드 동시 호출을 지원하므로 공유해도 안전하다.
- **경쟁 상태 방지**: 빠르게 타이핑하면 이전 검색이 늦게 끝나 최신 결과를 덮어쓸 수 있다. 요청마다 증가하는 일련번호를 붙여, 최신 번호와 다른 결과는 버린다.

### ③ IME 대응

Qt `QLineEdit`은 `compositionend`를 직접 노출하지 않는다. DESIGN §3.2가 제안한 300ms debounce(각 키 입력마다 `QTimer.singleShot` 재시작)로 조합 중간 상태를 걸러낸다 — 완벽한 조합 감지는 아니지만 실용적 근사치임을 문서에 남긴다.

### ④ 모델 관리 팝업 범위

**임베딩 섹션만 구현한다** (T4.11a). sLM 섹션은 Phase 6/7이 아직 이 저장소에서 시작되지 않아 실제 다운로드 인프라가 없다 — 다른 PC의 화면처럼 Qwen/Phi 행을 가짜로 넣지 않고, "AI 요약 모델은 Phase 7에서 추가됩니다" 안내만 둔다.

`KURE-v1` 행은 "준비 중" 배지 + 비활성 버튼(PLAN §4-C 확정 사항). PC 성능 콤보에서 고성능을 고르면 팝업이 열리고 그 행에 포커스되지만, 실제 전환은 안 된다 — 콤보 값은 `config.settings.get_profile()`이 반환하는 값(현재는 항상 경량)을 그대로 반영해 되돌아간다. 설정과 실제 동작이 어긋난 상태를 만들지 않기 위함(Option A의 취지 그대로).

`T4.11b`(재인덱싱 트리거, `embed_missing()` 재사용)는 코드로는 연결해두되, KURE-v1이 실제로 설치될 수 없어 **이번 Phase에서는 종단 검증이 불가능**하다는 점을 결과 보고 시 명시한다.

### ⑤ AI 요약 토글 — 비활성 + 툴팁으로 확정 (DESIGN §13 남은 항목 #3)

DESIGN §4.2가 제안한 두 안 중 **비활성(disabled) + "Phase 7에서 지원 예정" 툴팁**을 채택한다. 활성 상태로 두고 켰을 때만 안내 문구를 띄우는 안보다 단순하고, "껐다 켜도 아무 일도 안 일어나는" 고장처럼 보이는 상황 자체를 차단한다.

### ⑥ 하이라이트·발췌 (T4.13)

- `highlight.py`가 검색어 목록(공백 분리, 백엔드와 동일 규칙)을 받아 `<span style="background-color:#FDE68A;font-weight:700;">`로 감싼 리치 텍스트를 만든다. `일치되는 단어` ON이면 단어 경계(`\b`, 한글 포함 유니코드 인식)로 제한, OFF면 부분 일치. `대/소문자 구분` ON이면 그대로, OFF면 대소문자 무시 매칭.
- **발췌 윈도잉**: 무조건 앞 140자를 자르지 않고, 첫 매치 위치를 중심으로 창을 잡는다 — 매치가 발췌 밖으로 밀려나 하이라이트가 하나도 안 보이는 상황을 피한다. 잘린 양쪽엔 `…`.

### ⑦ 폴더 관리 — 최소 구현 (T4.17)

전체 "폴더 관리 화면"은 범위 밖(TASK 문서가 별도 작업으로 분리 허용). 상태바의 "폴더 관리" 버튼은 최소 `QDialog`를 연다: 현재 대상 폴더 표시 + "폴더 선택"(`QFileDialog.getExistingDirectory`) + "다시 인덱싱"(`IndexingThread` 기동, 진행 상황은 상태바에 반영). 대상 폴더·DB 경로·모델 프로파일은 `data/app_state.json`에 저장한다 — **레지스트리를 쓰는 `QSettings` 기본 포맷은 TECH 9.1 포터블 원칙 위반이라 쓰지 않는다.**

## 테스트 전략

- **순수 로직**(하이라이트, 발췌 윈도잉, AppState 직렬화)은 Qt 없이 일반 pytest로 검증
- **위젯 동작**은 `pytest-qt`(신규 의존성, `qtbot` 픽스처로 헤드리스 상호작용 가능)로 T4.18/T4.19 커버 — 검색 실행, 토글 조합, 형식 필터, 결과 카드 렌더링을 실제 이벤트 루프에서 확인
- 기존 233개 테스트가 WAL 모드 변경 후에도 통과하는지 먼저 확인

## 검증 방법

1. `pytest -q` 전체 통과 (기존 233 + 신규)
2. 앱을 실제로 띄워 실제 데이터(생성 샘플 + 프로젝트 루트 hwp)로 인덱싱 → 검색 → 필터/토글 조합 → 원문 열기까지 수동 확인
3. 가능하면 PowerShell 화면 캡처로 스크린샷을 남겨 시각적으로 대조한다(디스플레이가 없는 환경이면 콘솔 무오류 실행 + pytest-qt 결과로 대체하고 그 사실을 명시한다)
4. DoD: 사이드바의 모든 토글·콤보박스가 검색 결과에 실제로 반영되는지 교차 확인 (T4.19)
5. 완료 후 TASK 체크박스, PLAN §4-D 실행 결과, CLAUDE.md 갱신

---

# Phase 3: 임베딩 연동 + 벡터 재순위 구현 계획

## Context

Phase 2(FTS5 키워드 인덱싱, 177 passed)가 완료되어 BM25 후보군을 뽑는 `indexer/fts5/search.py`가 준비됐다. 이번 Phase는 그 후보군 위에 **코사인 유사도 재순위**를 얹어 TECH 5.1의 2단계 하이브리드 검색을 완성한다. ANN(근사 최근접)이 아니라 **후보 내 직접 계산**이라는 점이 저장소 선택까지 좌우하는 핵심 전제다.

이 Phase의 결과물은 Phase 4 UI가 그대로 호출하고, 유사도 임계값은 Phase 7 sLM 호출 차단까지 이어진다.

## 사전 실측한 사실 (추측 아님, 이 환경에서 직접 확인)

| 항목 | 실측값 |
|---|---|
| `jhgan/ko-sroberta-multitask` int8 ONNX **기제공** | `onnx/model_qint8_avx512_vnni.onnx` **111MB** — 변환 불필요 |
| 모델 사양 | klue/roberta-base, 768차원, **mean pooling**, **max_seq_length=128** |
| `nlpai-lab/KURE-v1` | **2,271MB**, ONNX 미제공 (변환 시 torch 필요, int8도 ~570MB) |
| ChromaDB | 75MB / **79개 패키지** (kubernetes·grpcio·bcrypt·onnxruntime 중복 포함) |
| onnxruntime + tokenizers + numpy | 35MB / 21개 |
| sentence-transformers | 205MB (**torch 117MB** — 변환 전용, 런타임 불필요) |
| HuggingFace 접속 | 정상 (모델 다운로드 가능) |

## 사용자 확정 결정 2건

1. **벡터 저장소: SQLite BLOB** — ChromaDB(TECH 5.1 원안) 대신 기존 `index.sqlite3`에 테이블 추가. 추가 의존성 0개이며, TECH 5.1이 ChromaDB를 고른 근거였던 "ID 기반 정밀 조회"에 오히려 더 정확히 부합. **TECH 문서 5.1·10장도 함께 갱신한다.**
2. **고성능 모드(KURE-v1)는 구조만 열어둔다** — 토글·추론 코드는 완성하되 실검증은 111MB 경량 모델로. KURE-v1은 TECH 9.3의 sLM과 동일한 "분리 다운로드" 대상으로 문서화.

## 🔴 착수 시 먼저 해결할 것 — 청크 크기 vs 모델 입력 한계

**Phase 2에서 이월된 과제가 실제 문제로 확인됐다.** 청커 기본값은 400자인데 이 모델의 `max_seq_length`는 **128 토큰**이다. 한국어는 대략 1.5~2.5자/토큰이라 400자 청크는 160~270토큰으로 추정되어 **뒷부분이 잘린 채 임베딩된다** — 키워드 검색은 전체 텍스트로 되는데 벡터만 앞부분 기준이 되어 두 단계가 어긋난다.

**구현 첫 단계에서 실제 토크나이저로 토큰 길이 분포를 측정한 뒤 결정한다:**
- 측정 결과 128을 넘으면 `chunker.DEFAULT_MAX_CHARS`를 실측 기반으로 낮춘다(대략 200자 예상)
- 청크 크기를 바꾸면 재인덱싱이 필요하지만, 지금은 샘플 규모라 비용이 거의 없다. **Phase 4 이후에 발견했다면 실사용 인덱스를 재생성해야 했을 문제다.**
- 잘림을 감수하는 선택지도 있으나, 검색 품질 저하가 조용히 발생하므로 권하지 않는다

## 모듈 구조

```
config/
└── settings.py          # 경량/고성능 모델 토글, 유사도 임계값 0.5 단일 상수 (T3.7)
indexer/vector/
├── __init__.py
├── embedder.py           # tokenizers → onnxruntime → mean pooling → L2 정규화 (T3.1, T3.2)
├── store.py               # chunk_vectors 테이블 저장·조회 (T3.3, T3.4)
└── download.py             # HF에서 ONNX+토크나이저 내려받기 (오프라인 배포 전 1회)
search/
├── __init__.py
└── hybrid_search.py        # FTS5 후보 → 벡터 재순위 (T3.5, T3.6)
scripts/
└── benchmark_search.py      # 응답 속도·메모리 측정 (T3.8)
models/                       # .gitignore 대상 — 내려받은 모델 파일
```

## 스키마 추가 (기존 `indexer/fts5/schema.py`에 이어붙임)

```sql
CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    model    TEXT NOT NULL,      -- 어떤 모델로 만든 벡터인지
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL       -- float32 little-endian, L2 정규화 완료 상태로 저장
);
```

- `model` 컬럼이 핵심이다. 경량↔고성능 전환 시 차원(768 vs 1024)이 달라 벡터가 호환되지 않으므로, 조회 시 현재 설정 모델과 다르면 **재임베딩 대상으로 판정**한다.
- 벡터를 저장 시점에 미리 L2 정규화해두면 검색 때 코사인 유사도가 **내적 한 번**으로 끝난다.
- 기존 `documents` 삭제 → `chunks` CASCADE → `chunk_vectors` CASCADE로 정리가 자동 연결된다.

## 구현 순서

1. **토큰 길이 실측 → 청크 크기 확정** (위 🔴 항목) — `download.py`로 모델 확보 후 실제 토크나이저로 측정
2. **T3.7 `config/settings.py`** — 다른 모듈이 전부 참조하므로 먼저. 모델 프로파일(경량/고성능), `models/` 상대 경로(TECH 9.1 포터블 원칙), `SIMILARITY_THRESHOLD = 0.5` 단일 정의
3. **T3.1 `download.py`** — `onnx/model_qint8_avx512_vnni.onnx` + `tokenizer.json`만 선별 다운로드(전체 레포는 440MB 이상이라 받지 않음). 오프라인 PC 대비해 파일 존재 여부·크기 검증 포함
4. **T3.2 `embedder.py`** — onnxruntime 세션 + `tokenizers`로 인코딩 → **mean pooling(attention_mask 가중)** → L2 정규화. int8 ONNX가 이미 제공되므로 torch 없이 동작. 모델 미설치 시 명확한 안내 예외
5. **T3.3/T3.4 `vector/store.py`** — 위 스키마, `store_vectors()`/`fetch_vectors(chunk_ids)`. `indexer/pipeline.py`에 임베딩 단계를 **옵션 인자로** 연결(기본 ON, 테스트는 모델 없으면 skip)
6. **T3.5/T3.6 `search/hybrid_search.py`** — FTS5 상위 N(기본 100) → 쿼리 임베딩 → 후보 벡터 내적 → 재순위 상위 K 반환. 벡터가 없는 청크는 BM25 순위를 유지한 채 뒤로 보내고 **탈락시키지 않는다**(키워드로 잡힌 결과를 임베딩 누락으로 잃으면 안 됨). 임계값 미만은 `is_low_relevance` 플래그로 표시(DESIGN §5.6 흐림 처리, Phase 7 sLM 차단에 재사용)
7. **CLI 확장** — `indexer/cli.py`의 `search`에 `--hybrid` 옵션 추가해 눈으로 비교 가능하게
8. **T3.8 벤치마크** — 임베딩 처리량, 질의 응답 지연, 메모리. **이 PC는 8GB 최소 사양기가 아니므로 참고치임을 명시**하고 기록

## 검증 방법

- `pytest -q` — 기존 177 + 신규. 모델 미설치 환경에서는 임베딩 테스트가 사유와 함께 skip(Phase 1의 LibreOffice·hwp 패턴 재사용)
- **DoD 핵심**: 동일 질의에 대해 키워드 단독 vs 하이브리드 결과를 나란히 출력해 **상위 관련도가 실제로 개선되는지** 샘플 셋으로 확인. 개선이 없으면 원인(청크 크기·풀링·정규화)을 규명한다
- 벡터 없는 청크가 결과에서 사라지지 않는지, 모델 교체 시 차원 불일치가 안전하게 처리되는지 테스트
- 완료 후 TASK 체크박스, PLAN §3-B 실행 결과, CLAUDE.md 갱신 + **TECH 5.1/10장의 ChromaDB 결정 정정**

---

# Phase 2: 폴더 스캔 + FTS5 키워드 인덱싱 구현 계획 (완료됨)

## Context

Phase 1(문서 파서, 126 passed / 0 skipped)이 완료되어 `parser.parse_file()` → `ParsedDocument.chunks`가 안정된 입력으로 준비됐다. 이번 Phase는 sLM·임베딩 없이 **키워드 검색만으로 동작을 확인**하는 것이 목표이며, Phase 3(벡터 재순위)이 이 위에 얹히므로 스키마를 다시 바꾸면 Phase 3까지 영향이 번진다.

`PLAN_오프라인RAG시스템.md`의 Phase 2 절이 착수 전 반드시 결정할 것으로 **"대/소문자 구분" FTS5 구현 방식**을 지목했다 — 이 결정을 미루면 인덱스를 통째로 재생성해야 한다. 아래는 이 결정을 포함해 실제로 SQLite에서 동작을 검증한 뒤 확정한 설계다.

## 사전 검증한 사실 (추측 아님, 이 환경에서 직접 실행 확인)

- `sqlite3` 모듈 버전 3.50.4, **FTS5 사용 가능**, `trigram` 토크나이저·**external content table**(`content=` 옵션) 모두 지원됨
- `unicode61` 토크나이저는 **대소문자를 항상 접는다** — "API"로 검색 시 "api key..." 행도 함께 매치됨을 실측 확인. `remove_diacritics`는 발음 구별 기호 전용 옵션이라 대소문자와 무관함 (PLAN 문서의 선택지 1은 이 부분이 부정확했음 — 정정)
- 한글은 조사가 붙어도 공백이 없으면 하나의 토큰으로 묶인다 — "계약서"로 색인된 문서는 `MATCH '계약'`(완전 토큰)로는 안 잡히고 `MATCH '계약*'`(접두)로만 잡힘. 즉 **"일치되는 단어" 토글은 이 접두 매칭(`*`) 사용 여부를 켜고 끄는 것**으로 자연스럽게 구현된다 — 별도 형태소 분석 없이도 동작
- `kss` 6.0.6은 설치 가능하나 `numpy`, `scipy`, `networkx`, `pecab`, `emoji`, `jamo`, `hangul-jamo`, `tossi`, `cmudict`, `koparadigm`, `kollocate` 등 **약 15개 런타임 의존성**을 끌고 온다. TECH 7장이 지정한 선택이라 이번 Phase에서 채택하되, 무게에 대한 실측 데이터로 기록해두고 Phase 9 패키징 실측 시 재검토한다

## 모듈 구조

```
indexer/
├── __init__.py
├── scanner.py         # 폴더 재귀 스캔 (T2.1) — parser.registry.is_supported() 재사용
├── chunker.py          # kss 기반 문장 분리 + 실패 시 정규식 폴백 (T2.4)
├── pipeline.py          # 스캔→파싱→청킹→저장 오케스트레이션, 백그라운드 스레드 + 진행 콜백 (T2.8)
├── cli.py                # `python -m indexer.cli index/search` 임시 CLI (T2.9)
└── fts5/
    ├── schema.py          # 테이블 DDL + external content 동기화 트리거 (T2.2)
    ├── store.py            # ParsedDocument → chunks/chunks_fts 저장 (T2.3, T2.5, T2.6)
    └── search.py            # 쿼리 빌더 + BM25 검색 (T2.7, 대소문자·일치단어 처리)
tests/
├── test_indexer_scanner.py
├── test_indexer_schema.py
├── test_indexer_store.py
├── test_indexer_search.py
└── test_indexer_chunker.py
```

기존 `tests/conftest.py`의 `samples` 픽스처(모든 샘플이 한 폴더에 생성됨)를 스캐너·엔드투엔드 테스트에 그대로 재사용한다.

## 스키마 설계 (T2.2)

**문서 테이블 + 청크 테이블(원문 보관) + FTS5 인덱스(external content)** 3단 구조로, 원문은 한 곳에만 저장하고 FTS5는 그 원문을 가리키기만 한다(중복 저장 없음).

```sql
CREATE TABLE documents (
    doc_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,           -- ParsedDocument.status
    source_mtime REAL,
    source_hash TEXT,
    indexed_at TEXT NOT NULL
);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,          -- FTS5 external content가 요구하는 정수 rowid
    chunk_id TEXT UNIQUE NOT NULL,   -- Chunk.chunk_id (Phase 3 ChromaDB 조인 키)
    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    type TEXT NOT NULL,              -- text|table|image
    page_or_slide INTEGER,
    content TEXT NOT NULL,           -- 원문 그대로 (대소문자 보존) — table은 TableData.to_text()
    caption TEXT DEFAULT '',         -- 표의 캡션+헤더만 별도 보관 (T2.6 가중치용)
    keywords TEXT DEFAULT '',
    table_json TEXT,                 -- TableData 직렬화 (표 카드 렌더링용, Phase 5)
    image_json TEXT,                 -- ImageData 직렬화
    created_at TEXT NOT NULL,
    source_mtime REAL,
    source_hash TEXT
);

-- content='chunks' → chunks 테이블의 원문을 그대로 참조, FTS5 내부에 텍스트를 중복 저장하지 않음
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    content, file_name, keywords, caption,
    content='chunks', content_rowid='id',
    tokenize='unicode61'
);

-- SQLite 공식 권장 패턴: external content 테이블은 트리거로 동기화
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content, file_name, keywords, caption)
    VALUES (new.id, new.content, new.file_name, new.keywords, new.caption);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content, file_name, keywords, caption)
    VALUES ('delete', old.id, old.content, old.file_name, old.keywords, old.caption);
END;
```

**T2.6 캡션 가중치**: 검색 시 `bm25(chunks_fts, 1.0, 2.0, 1.0, 5.0)`처럼 `caption` 컬럼에 압도적으로 높은 가중치를 줘서, 표의 캡션·헤더가 매치되면 본문 매치보다 우선 노출되게 한다 (TECH 4.3절).

## 검색 옵션 구현 (search.py, T2.7)

| DESIGN 토글 | 구현 방식 |
|---|---|
| **대/소문자 구분** | FTS5는 항상 대소문자를 접으므로 1차 MATCH는 그대로 두고, 결과를 `chunks.content`(원문 보존)와 Python에서 `in` 대소문자 그대로 비교해 후처리 필터링. BM25 순위는 필터링 후에도 유지 |
| **일치되는 단어** | OFF(기본): 각 검색어 뒤에 `*`를 붙여 접두 매칭(`계약*`) → "계약서"도 잡힘. ON: `*` 없이 완전 토큰 매치만 |

## 구현 순서

1. **T2.2** 스키마 먼저 (`fts5/schema.py`) — 위 DDL + 트리거, `sqlite3.connect` 래퍼
2. **T2.1** 스캐너 — `parser.registry.SUPPORTED_EXTENSIONS`/`is_supported()` 재사용, `Path.rglob('*')` 재귀 후 확장자 필터
3. **T2.4** 청커 — `kss.split_sentences()` 우선 사용, `ImportError`/예외 시 정규식(`. ! ? \n` 기준) 폴백으로 인덱싱이 절대 죽지 않게
4. **T2.5** 제목/키워드 — 제목은 Phase 1 `ParsedDocument.title`을 그대로 재사용(재추출 안 함). 청크 단위 키워드 추출(형태소 분석 등)은 이번 Phase 범위에서 제외하고 FTS5 자체 검색에 위임 — 범위를 좁게 유지
5. **T2.3 + T2.6** 저장 파이프라인 (`fts5/store.py`) — `ParsedDocument` → `documents` 1행 + `chunks` N행. table 청크는 `content=TableData.to_text()`(Phase 1에 이미 구현됨, 캡션·헤더 우선 배치), `caption=캡션+header_row`로 별도 채움
6. **T2.7** 검색 함수 (`fts5/search.py`) — 위 표의 옵션 처리 포함 BM25 쿼리
7. **T2.8** `pipeline.py` — `threading.Thread` + `on_progress(done, total)` 콜백. UI가 없는 지금은 콜백을 CLI 진행률 출력으로 연결
8. **T2.9** `cli.py` — `python -m indexer.cli index <폴더>` / `search <질의> [--case-sensitive] [--exact-word]`로 눈으로 확인 가능하게

## 알려진 제약 (이번 Phase에서 해결하지 않고 이월)

- **구버전 포맷 변환 비용**(건당 2.47초, Phase 1에서 실측)은 이번 Phase에서 최적화하지 않는다. `pipeline.py`가 어차피 백그라운드 스레드라 UI 프리즈는 없고, 배치 변환·데몬 상주는 Phase 9 실측 후 필요하면 붙인다
- kss의 무거운 의존성 체인은 그대로 채택하되 Phase 9 인스톨러 용량 검토 시 재평가 대상으로 TASK/PLAN에 남긴다

## 검증 방법

- `pytest -q` — 새 테스트 포함 전체 통과 확인 (기존 126 + indexer 신규)
- `samples` 픽스처 폴더를 스캔 → 인덱싱 → "API"(대소문자 구분 ON/OFF), "계약"(일치단어 ON/OFF)로 검색해 기대한 행만 나오는지 assert
- 표 청크가 캡션 가중치로 상위 노출되는지 bm25 스코어 비교로 확인
- CLI로 실제 눈으로 검색 결과 확인 (TASK DoD의 "CLI 또는 임시 UI" 충족)
- 완료 후 TASK 문서 체크박스 갱신, PLAN 문서 Phase 2 절에 "실행 결과" 기록, CLAUDE.md 한 줄 요약

---

# Phase 1: 문서 파서 모듈 구현 계획 (완료됨— 아래는 착수 시 계획 기록)

## Context
오프라인 RAG 시스템의 착수 Phase. 아직 코드가 전혀 없는 빈 프로젝트(마크다운 기획 문서 3종만 존재)이며, Python 3.10.6 환경에 관련 라이브러리는 아직 미설치, LibreOffice(`soffice`)도 PATH에 없는 상태를 확인했다. 이후 Phase 2(FTS5 인덱싱)가 이 Phase의 파서 출력 스키마에 의존하므로, 모든 형식별 파서가 동일한 청크 스키마(TECH 4.2절)를 따르는 것이 이번 작업의 핵심 제약이다.

## 모듈 구조

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

## 공통 스키마 설계 (TECH 4.2절 반영)

`schema.py`에 정의:
- `Chunk`: `chunk_id, doc_id, file_path, file_name, type(text|table|image), page_or_slide, content, keywords, embedding_vector(=None, Phase 3에서 채움), created_at, source_mtime, source_hash`
- `type=table`인 청크는 `content`에 행·열 구조(`list[list[str]]`)를 JSON 직렬화 가능한 형태로 담고, 문단 텍스트와 절대 병합하지 않음 (TECH 3.1절)
- `type=image`인 청크는 `content`에 이미지 파일 경로(캡처/추출본 저장 위치)를 담고, `keywords`에 "이미지(텍스트 미인식 가능)" 플래그성 메타를 남겨 Phase 5 렌더러가 안내 문구를 붙일 수 있게 함
- `ParsedDocument`: `doc_id, file_path, title, chunks: list[Chunk], parse_status(ok|partial|failed), errors: list[str]` — 구버전 포맷 변환 실패 등을 부분 실패로 표현하기 위함

## 구현 순서 (Task 번호는 TASK 문서 기준, 의존성 우선으로 재배열)

1. **T1.1** 스캐폴딩 — `venv` 생성 안내, `requirements.txt`(pymupdf, python-docx, openpyxl, python-pptx, chardet, pyhwp), `pyproject.toml`(pytest 설정), 디렉토리 골격, `.gitignore`
2. **T1.12** 공통 스키마 + `BaseParser` 먼저 정의 (다른 모든 파서가 이걸 참조하므로 순서를 앞당김 — TASK 문서 원래 순번과 다르지만 의존관계상 필요)
3. **T1.4** TXT 파서 — chardet로 인코딩 감지 후 kss 없이 일단 원문 청크 하나로 반환(문장 분리는 Phase 2 T2.4 소관, 여기서는 파서 출력까지만)
4. **T1.2** PDF 파서 — PyMuPDF `get_text()`(본문), `find_tables()`(표), `get_images()`(삽입 이미지 추출), 텍스트 없는 페이지는 벡터도형으로 간주해 페이지 렌더링 캡처
5. **T1.3** DOCX 파서 — 문단(헤딩 태그로 제목 후보 추출)/표(`table.rows`)/이미지(`inline_shapes`) 분리 추출. 벡터 도형(SmartArt 등)은 별도 감지 로직 없이 "LibreOffice 변환 캡처로 보완 가능" 지점을 훅으로 남기고 T1.9에서 연결
6. **T1.5** XLSX 파서 — 시트별 표 청크(헤더 행 포함), 임베디드 이미지 추출
7. **T1.6** PPTX 파서 — 슬라이드별 텍스트/표/이미지, 벡터 도형은 DOCX와 동일하게 T1.9 캡처 훅
8. **T1.7** HWP 파서 — `hwp5txt` CLI 또는 `pyhwp` API로 텍스트 추출. 표/이미지 구조 추출은 라이브러리 한계로 텍스트만 지원함을 코드 주석 없이 README/제한사항 문서에 명시
9. **T1.8** HWPX 파서 — zip 열어 `Contents/section*.xml` 파싱, 문단/표 태그 매핑 (OOXML 유사 구조)
10. **T1.9** LibreOffice 헤드리스 변환 파이프라인 — `soffice --headless --convert-to` wrapper, `LibreOfficeNotFoundError`/`ConversionTimeoutError`/`ConversionFailedError` 명확히 구분. doc→docx, xls→xlsx, ppt→pptx 변환 후 기존 파서에 위임하는 `legacy_parser.py`. 변환 실패 시 `ParsedDocument.parse_status="failed"` + `errors`에 사유 기록 (예외를 삼키지 않고 상위로 상태 전파)
11. **T1.10 / T1.11** 표/이미지 분리 저장 규칙 — 각 파서 구현 시 이미 반영하지만, 전체 파서 대상으로 마지막에 일괄 검토 패스 진행 (표가 텍스트에 섞이지 않는지, 이미지 청크에 페이지/슬라이드 번호가 채워지는지)
12. **T1.13** 샘플 문서 + 단위 테스트 — `tests/fixtures/generate_samples.py`로 txt/docx/xlsx/pptx/pdf 샘플을 코드로 생성(표 1개 + 이미지 1개씩 포함)해 각 파서 테스트. **제약**: 이 환경엔 LibreOffice가 설치되어 있지 않아 doc/xls/ppt 변환 테스트와, 유효한 바이너리 생성이 어려운 hwp 파일 테스트는 실제 실행이 불가능함 → 해당 테스트는 `pytest.mark.skipif`로 환경 의존성을 명시하고 스킵 처리, 계획대로 구현은 완료하되 실제 검증은 LibreOffice/한글 설치 환경에서 추가로 필요함을 결과 보고 시 명확히 알림

## 알려진 제약 (사용자에게 미리 공유)
- **LibreOffice 미설치**: T1.9 파이프라인은 코드로 구현하지만 이 환경에서 실제 변환 동작은 검증 불가. `soffice` 실행 파일 경로를 설정 가능하게 만들고, 미설치 시 명확한 에러 메시지로 안내.
- **hwp 실제 샘플 부재**: 유효한 바이너리 .hwp 파일을 코드로 생성할 수 없어 pyhwp 파서는 실제 hwp 파일로 테스트 불가. pyhwp 설치 자체가 실패할 가능성도 있어(Windows/Python 3.10 호환성 이슈 보고 사례 있음), 설치 실패 시 대안(예: 텍스트 추출 실패를 명확히 알리는 에러 처리)을 우선 마련.
- 위 두 항목은 실제 검증이 필요하며, TASK 문서 DoD("9종 형식 오류 없이 파싱")를 완전히 충족하려면 LibreOffice/한글이 설치된 환경에서 별도 확인 필요.

## 검증 방법
- `pytest tests/` 전체 실행, txt/pdf/docx/xlsx/pptx/hwpx 파서는 실제 통과 확인
- legacy(doc/xls/ppt) 및 hwp는 스킵 사유가 명확히 출력되는지 확인
- 각 파서 출력이 `schema.Chunk` 검증(pydantic 또는 dataclass validate)을 통과하는지, 표/이미지 청크가 텍스트 청크와 분리되어 있는지 assert
- 작업 완료 후 TASK 문서 체크박스 갱신 + CLAUDE.md에 한 줄 요약 추가

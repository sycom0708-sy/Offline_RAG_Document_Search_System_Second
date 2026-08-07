# Phase 단계별 구현 계획서

> 이 문서는 Claude Code **plan 모드**로 각 Phase 착수 전에 작성한 계획 원문을 그대로 보관하는 아카이브다. **승인 시점 스냅샷**이므로 이후 실제 구현이 달라진 부분(버그 발견, 설계 변경 등)은 반영되지 않는다 — 계획 대비 실제로 어떻게 됐는지는 `PLAN_오프라인RAG시스템.md`의 각 Phase "실행 결과" 절을 참고한다. Phase가 완료될 때마다 그 시점의 plan 파일 내용을 이 문서 위쪽에 이어붙인다(최신이 위).

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

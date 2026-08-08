# sLM 준수율 측정 하네스 (Phase 6)

"문서에 없으면 모른다고 답하는가"를 후보 모델별로 **실측**하기 위한 도구 모음이다.
설계로 보장할 수 없는 항목이라 실측이 유일한 방법이고(TECH 10장), Phase 7의
안전장치 2번이 이 결과에 직접 의존한다.

## 구성

| 파일 | 역할 |
|---|---|
| `runtime.py` | llama-server 실행 파일 탐색(환경변수 > PATH > `vendor/`)과 수명주기. 로딩 시간·프로세스 메모리 측정 포함 |
| `client.py` | llama-server HTTP 클라이언트. `/v1/chat/completions`와 `/apply-template` |
| `prompt.py` | 근거 강제 프롬프트, 기권·정답 판정 |
| `testset.py` | 테스트셋 스키마·로더. 실문서는 본문 대신 `chunk_ids`만 담는다 |
| `download.py` | GGUF 다운로더 (이어받기 지원) |
| `../scripts/setup_llamacpp.py` | llama.cpp 사전 빌드 바이너리 설치 |
| `../scripts/build_slm_testset.py` | 테스트셋 초안 생성·점검 |
| `../scripts/benchmark_slm.py` | 준수율·속도·메모리 측정, 결과 비교표 |

## 처음 설정 (인터넷 되는 PC에서 1회)

```bash
python -m scripts.setup_llamacpp          # llama.cpp 바이너리 (약 18MB)
python -m slm.download --list             # 후보·크기·설치 여부
python -m slm.download exaone-4.0-1.2b    # 필요한 것만
```

오프라인 PC에는 `vendor/`와 `models/` 폴더를 통째로 옮기면 된다. 둘 다
`.gitignore` 대상이라 저장소에는 들어가지 않는다.

## 측정

```bash
python -m scripts.benchmark_slm --testset data/slm_testset.json --threads 4 --show 10
```

- `--threads`를 **고정해야 재현 가능하다**. 그리디(temperature=0)라도 스레드 수가
  바뀌면 결과가 달라질 수 있다
- `--label`로 측정 환경 이름을 남긴다 (비교표에 그대로 쓰인다)
- 원시 결과 JSON은 `data/`에만 저장된다 — 실문서 인용이 섞이므로 커밋 금지

## 다른 PC에서 이어서 측정하기

최소 사양 기준기(i5-8265U / 8GB)와 권장 사양(Ultra 5 125U / 16GB)은 물리적으로 다른
기계라 한 번에 못 잰다.
각 PC에서 따로 돌리고 결과 JSON을 모아 합친다.

**옮길 것** (모두 `.gitignore` 대상이라 git으로는 안 간다)

- `data/index.sqlite3` — 테스트셋이 `chunk_ids`로 참조하므로 **같은 인덱스**여야 한다.
  다시 인덱싱하면 chunk_id가 바뀌어 `validate`가 실패한다
- `data/slm_testset.json` — 질문·정답 키워드

**그 PC에서**

```bash
python -m scripts.setup_llamacpp
python -m slm.download exaone-3.5-7.8b
python -m scripts.build_slm_testset validate data/slm_testset.json
python -m scripts.benchmark_slm --testset data/slm_testset.json --threads 8 --label "권장사양 Core Ultra 5 125U" --out data/slm_bench_recommended.json
```

**결과 합치기** (측정 없이 표만 출력)

```bash
python -m scripts.benchmark_slm --compare data/slm_bench_minspec.json data/slm_bench_recommended.json
```

## 함정 (실측으로 확인한 것)

- **system 메시지가 버려지는 모델이 있다.** EXAONE-4.0의 chat template은 system을
  렌더링에서 통째로 뺀다. 그래서 `build_messages()`는 규칙을 user 메시지에 담는
  것이 기본이고, 벤치마크는 모델을 올릴 때마다 `/apply-template`으로 기권 문구가
  실제 프롬프트에 실렸는지 확인한 뒤에야 측정을 시작한다
- **Qwen3.5는 thinking 모드가 기본 활성이다.** 끄지 않으면 토큰을 전부 사고에 쓰고
  빈 응답을 돌려준다. `--reasoning off`로 꺼진다(`--reasoning-budget 0`은 효과 없음).
  프로파일의 `extra_server_args`에 고정해 두었다
- **메모리는 llama-server 프로세스를 재야 한다.** 파이썬 쪽은 HTTP 요청만 보내므로
  자기 프로세스를 재면 모델 크기가 전혀 안 잡힌다
- 준수율은 **기권 정확도와 과잉 기권율을 함께** 본다. 무조건 기권하는 모델은 한
  지표로는 만점이 나온다. 자동 채점은 근사치이므로 `--show`로 표본을 눈으로 볼 것

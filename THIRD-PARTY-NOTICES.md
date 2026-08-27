# 서드파티 라이선스 고지 (THIRD-PARTY NOTICES)

이 소프트웨어는 아래 서드파티 구성 요소를 포함하거나(배포 패키지에 실제로 담아 배포) 실행 시 함께 사용합니다. 각 구성 요소는 해당 라이선스 조건에 따릅니다.

이 문서는 설치된 패키지 메타데이터(`pip show`)와 각 프로젝트의 공식 라이선스 파일을 2026년 8월 27일 기준으로 직접 대조해 작성했습니다.

---

## ⚠️ 검토가 필요한 항목

아래 4개는 흔한 MIT/Apache/BSD가 아니라서, 조직의 라이선스 정책에 맞는지 별도 확인을 권합니다.

| 구성 요소 | 라이선스 | 왜 주의가 필요한가 |
|---|---|---|
| **EXAONE-4.0-1.2B** | EXAONE AI Model License 1.2-NC | **비상업적(Non-Commercial) 용도로 제한**됩니다. 배포 패키지에는 포함되지 않고 사용자가 앱의 "모델 관리"에서 직접 내려받는 방식이지만, 실제 사용은 이 라이선스를 따릅니다. 회사 내부 업무에 쓰는 것이 이 라이선스의 "비상업적"에 해당하는지는 법무 검토가 필요합니다. |
| **PyMuPDF** | GNU AGPL-3.0-or-later (또는 Artifex 상용 라이선스) | PDF 파싱에 쓰이며 배포 패키지에 실제로 포함됩니다. AGPL은 배포 시 소스 공개 의무가 따르는 강한 카피레프트입니다. 독점 라이선스로 배포하려면 Artifex의 상용 라이선스가 필요합니다. |
| **pyhwp** | GNU AGPL-3.0-or-later | 한글(.hwp) 파싱에 쓰이며 배포 패키지에 실제로 포함됩니다. 위와 동일하게 AGPL 배포 의무가 적용됩니다. |
| **ko-sroberta-multitask** | 명시 안 됨 | Hugging Face 모델 카드·README 어디에도 라이선스 필드가 없습니다. 경량 모드 기본 임베딩 모델로 **항상 배포 패키지에 포함**되는데, 저작권자의 명시적 허가 조건이 없는 상태입니다. |

---

## 1. 번들 실행 파일 (배포 패키지에 포함)

| 구성 요소 | 버전 | 라이선스 | 포함 조건 |
|---|---|---|---|
| LibreOffice Portable | 26.2.4 | MPL-2.0 / LGPL-3.0+ (LibreOffice 자체 정책) | 기본 포함, `--skip-libreoffice` 빌드 옵션으로 제외 가능 |
| llama.cpp | 릴리스 b10306 | MIT License | 항상 포함 |

> LibreOffice 고지는 앱의 **설정 → 오픈소스 라이선스** 카드에도 별도로 표시됩니다(`ui/widgets/settings_page.py`).

---

## 2. 번들 AI 모델 (배포 패키지에 포함)

| 모델 | 제공처 | 라이선스 | 포함 조건 |
|---|---|---|---|
| ko-sroberta-multitask | jhgan | ⚠️ 명시 안 됨 | 경량 모드 기본, 항상 포함 |
| KURE-v1 | nlpai-lab | MIT License | 빌드 시점에 있으면 포함(용량 문제로 없을 수 있음), 권장 모드용 |

---

## 3. 사용자가 별도로 내려받는 AI 모델 (배포 패키지 미포함)

앱의 **설정 → 모델 관리**에서 사용자가 직접 내려받거나, 인터넷이 되는 PC에서 받아 폴더에 복사해 넣습니다. 설치 프로그램 자체에는 포함되지 않지만, 사용 시 각 라이선스가 그대로 적용됩니다.

| 모델 | 제공처 | 라이선스 |
|---|---|---|
| EXAONE-4.0-1.2B | LGAI-EXAONE | EXAONE AI Model License 1.2-NC (비상업적) ⚠️ |
| Qwen3.5-4B | Alibaba/Qwen | Apache License 2.0 |

---

## 4. Python 라이브러리 (런타임, 배포 패키지에 포함)

PyInstaller로 정적 실행 파일에 함께 묶여 배포됩니다.

| 패키지 | 버전 | 라이선스 |
|---|---|---|
| PyMuPDF | 1.28.2 | AGPL-3.0-or-later / Artifex 상용 ⚠️ |
| python-docx | 1.2.0 | MIT License |
| openpyxl | 3.1.5 | MIT License |
| python-pptx | 1.0.2 | MIT License |
| chardet | 7.5.1 | 0BSD |
| pyhwp | 0.1b15 | AGPL-3.0-or-later ⚠️ |
| six | 1.17.0 | MIT License |
| kss | 6.0.6 | BSD-3-Clause |
| onnxruntime | 1.28.0 | MIT License |
| tokenizers | 0.23.1 | Apache License 2.0 |
| numpy | 2.5.1 | BSD-3-Clause (일부 하위 구성 요소는 0BSD/MIT/Zlib/CC0-1.0) |
| PySide6-Essentials | 6.11.1 | LGPL-3.0-only (GPL-2.0/3.0 선택 가능) |
| watchdog | 6.0.0 | Apache License 2.0 |
| reportlab | 5.0.0 | BSD License |
| Pillow | 12.3.0 | MIT-CMU (HPND 계열) |

> **PySide6-Essentials**는 동적 라이브러리(DLL) 형태로 포함됩니다. LGPL은 별도 조건(재링크 가능성 등)을 요구하므로, 배포 방식을 바꿀 계획이 있다면 Qt/PySide6 LGPL 조건을 다시 확인하는 것을 권합니다.

---

## 5. 빌드·개발 전용 (배포 패키지에 포함되지 않음)

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| pytest | 9.1.1 | MIT License | 테스트 전용 |
| pytest-qt | 4.5.0 | MIT License | 테스트 전용 |
| pyinstaller | 6.22.2 | GPLv2-or-later + 부트로더 예외 | 빌드 도구. 부트로더 예외 조항에 따라 이 도구로 만든 결과물(우리 앱)에는 GPL 의무가 적용되지 않음 |
| truststore | (버전 미고정) | MIT License | `scripts/convert_kure.py`가 KURE-v1 변환 시에만 별도 가상환경(`.venv-convert`)에서 사용, 배포와 무관 |

---

## 확인 방법

Python 패키지 라이선스는 아래 명령으로 재확인할 수 있습니다.

```bash
./.venv/Scripts/python.exe -c "import importlib.metadata as m; print(m.metadata('패키지명').get('License-Expression'))"
```

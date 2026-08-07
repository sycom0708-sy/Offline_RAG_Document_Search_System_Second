"""sLM(소형 언어 모델) 추론 — Phase 6 후보 검증, Phase 7 AI 요약.

`llama-cpp-python`은 Python 3.14용 사전 빌드 휠이 없어(실측 확인) 소스
컴파일에 CMake+MSVC가 필요하다. PRD 4장의 "관리자 권한 불필요"와 충돌하므로
**llama.cpp 공식 사전 빌드 바이너리(18MB)를 서브프로세스로 호출**한다 —
LibreOffice(`soffice`)를 부르는 방식과 같은 패턴이다.
"""

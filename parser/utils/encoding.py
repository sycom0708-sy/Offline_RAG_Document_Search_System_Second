"""한글 텍스트 파일 인코딩 자동 감지 (TECH 3장: CP949/EUC-KR 대응)."""

from __future__ import annotations

from pathlib import Path

import chardet

# chardet은 짧은 한글 텍스트에서 CP949를 다른 멀티바이트 인코딩으로 오판하는 경우가 있어
# 감지 결과를 우선 시도하되, 실패하면 아래 순서로 폴백한다.
_FALLBACK_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16")
_CONFIDENCE_THRESHOLD = 0.7


def detect_encoding(raw: bytes) -> str:
    result = chardet.detect(raw)
    encoding = result.get("encoding")
    confidence = result.get("confidence") or 0.0
    if encoding and confidence >= _CONFIDENCE_THRESHOLD:
        return encoding
    return ""


def read_text_file(file_path: str | Path) -> tuple[str, str]:
    """(본문, 사용된 인코딩)을 반환한다. 모든 후보 실패 시 UnicodeDecodeError."""
    raw = Path(file_path).read_bytes()
    if not raw:
        return "", "utf-8"

    candidates: list[str] = []
    detected = detect_encoding(raw)
    if detected:
        candidates.append(detected)
    candidates.extend(enc for enc in _FALLBACK_ENCODINGS if enc.lower() != detected.lower())

    last_error: UnicodeDecodeError | None = None
    for encoding in candidates:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError) as exc:
            if isinstance(exc, UnicodeDecodeError):
                last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise UnicodeDecodeError("unknown", raw, 0, 1, "지원하는 인코딩으로 디코딩할 수 없습니다")

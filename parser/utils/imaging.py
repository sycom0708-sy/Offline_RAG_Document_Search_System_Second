"""이미지 바이트에서 실제 형식을 판별한다.

HWP의 BinData는 확장자가 `.tmp`로 저장되고, HWPX/기타 포맷도 확장자가 없거나
실제 내용과 다른 경우가 있어 파일명 대신 시그니처(매직 넘버)로 판별한다.
"""

from __future__ import annotations

# (오프셋, 시그니처, 확장자)
_SIGNATURES: tuple[tuple[int, bytes, str], ...] = (
    (0, b"\x89PNG\r\n\x1a\n", "png"),
    (0, b"GIF87a", "gif"),
    (0, b"GIF89a", "gif"),
    (0, b"\xff\xd8\xff", "jpg"),
    (0, b"BM", "bmp"),
    (0, b"II*\x00", "tif"),
    (0, b"MM\x00*", "tif"),
    (0, b"\xd7\xcd\xc6\x9a", "wmf"),  # placeable WMF
    (40, b" EMF", "emf"),
    (0, b"RIFF", "webp"),  # 8바이트 뒤 'WEBP' 확인
)


def sniff_image_extension(data: bytes) -> str | None:
    """이미지로 인식되면 확장자를, 아니면 None을 반환한다."""
    for offset, signature, extension in _SIGNATURES:
        if data[offset : offset + len(signature)] != signature:
            continue
        if extension == "webp" and data[8:12] != b"WEBP":
            continue
        return extension
    return None


def is_image(data: bytes) -> bool:
    return sniff_image_extension(data) is not None

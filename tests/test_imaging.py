"""이미지 시그니처 판별 테스트.

HWP BinData는 확장자가 .tmp라 파일명으로는 형식을 알 수 없다.
"""

from __future__ import annotations

import pytest

from parser.utils.imaging import is_image, sniff_image_extension

SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "png"),
    (b"GIF89a" + b"\x00" * 8, "gif"),
    (b"GIF87a" + b"\x00" * 8, "gif"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 8, "jpg"),
    (b"BM" + b"\x00" * 12, "bmp"),
    (b"II*\x00" + b"\x00" * 8, "tif"),
    (b"MM\x00*" + b"\x00" * 8, "tif"),
    (b"\xd7\xcd\xc6\x9a" + b"\x00" * 8, "wmf"),
    (b"\x00" * 40 + b" EMF" + b"\x00" * 4, "emf"),
    (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 4, "webp"),
]


@pytest.mark.parametrize("data,expected", SIGNATURES)
def test_known_signatures_are_detected(data, expected):
    assert sniff_image_extension(data) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not an image at all",
        b"PK\x03\x04" + b"\x00" * 12,  # zip
        b"%PDF-1.7" + b"\x00" * 8,
        b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 4,  # RIFF지만 WEBP 아님
    ],
)
def test_non_images_are_rejected(data):
    assert sniff_image_extension(data) is None
    assert not is_image(data)


def test_extension_is_independent_of_filename():
    """.tmp로 저장된 GIF도 정확히 판별되어야 한다."""
    assert sniff_image_extension(b"GIF89a" + b"\x00" * 8) == "gif"

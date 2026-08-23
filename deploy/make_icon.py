"""앱 아이콘(.ico) 생성 — 문서+돋보기 시안 1번(20% 확대), 순수 Pillow로 그린다.

디자인 자체는 시안 검토 때 SVG로 먼저 확정했다[사용자 확정, 2026-08-22].
외부 이미지 생성 도구 없이, 그 SVG의 좌표를 그대로 정규화해 옮겨 그린다 —
새 디자인 도구를 들이는 대신 이미 확정된 좌표를 재사용하는 편이 결과물이
시안과 어긋날 위험이 없다.

결과물은 `deploy/`가 아니라 `ui/icons/`에 둔다 — exe·인스톨러 아이콘
자원일 뿐 아니라 `ui/app.py`가 런타임에 창·팝업 아이콘으로도 그대로
쓰기 때문이다(메인 창·모든 다이얼로그에 공통 적용, 2026-08-22 요청).
`ui/qss/app.qss`와 같은 방식으로 `deploy/app.spec`의 datas에 등록해
얼린 exe에도 번들된다.

    python -m deploy.make_icon
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT_PATH = Path(__file__).resolve().parent.parent / "ui" / "icons" / "app.ico"

BLUE = (37, 99, 235, 255)  # #2563EB — 앱 강조색과 동일
WHITE = (255, 255, 255, 255)

# 시안 SVG(140x140 타일, 20% 확대판)의 좌표를 0~1 비율로 정규화한 값.
DOC_RECT = (0.2943, 0.2086, 0.4457, 0.5657)  # x, y, w, h
DOC_LINES = [
    (0.3800, 0.3286, 0.2743),  # x, y, w — 첫 줄
    (0.3800, 0.4314, 0.2743),  # 둘째 줄
    (0.3800, 0.5343, 0.1714),  # 셋째 줄(짧음)
]
LINE_HEIGHT = 0.0343
CIRCLE = (0.6543, 0.6543, 0.1371, 0.0514)  # cx, cy, r, stroke
HANDLE = (0.7486, 0.7486, 0.8600, 0.8600, 0.0600)  # x0,y0,x1,y1, stroke


def _draw_icon(size: int) -> Image.Image:
    """`size`px 정사각 캔버스에 아이콘을 그린다. 작은 크기는 두께를 보정한다."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def s(v: float) -> float:
        return v * size

    # 작은 아이콘(16~32px)에서 1px 미만 선은 안 보인다 — 최소 두께를 보장한다.
    def stroke(v: float) -> float:
        return max(s(v), 1.4 if size <= 32 else 1.0)

    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=s(0.2), fill=BLUE)

    x, y, w, h = DOC_RECT
    draw.rounded_rectangle(
        [s(x), s(y), s(x + w), s(y + h)], radius=s(0.03), fill=WHITE
    )

    for lx, ly, lw in DOC_LINES:
        half = s(LINE_HEIGHT) / 2
        cy = s(ly) + half
        draw.line(
            [(s(lx), cy), (s(lx + lw), cy)],
            fill=BLUE,
            width=max(int(s(LINE_HEIGHT)), 1),
        )

    cx, cy, r, sw = CIRCLE
    bw = stroke(sw)
    draw.ellipse(
        [s(cx) - s(r), s(cy) - s(r), s(cx) + s(r), s(cy) + s(r)],
        outline=WHITE,
        width=int(bw),
    )

    x0, y0, x1, y1, hsw = HANDLE
    draw.line([(s(x0), s(y0)), (s(x1), s(y1))], fill=WHITE, width=int(stroke(hsw)))

    return img


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = _draw_icon(256)
    frames = [base] + [_draw_icon(sz) for sz in sizes if sz != 256]
    base.save(
        OUT_PATH,
        format="ICO",
        sizes=[(sz, sz) for sz in sizes],
        append_images=[im for im in frames if im.size[0] != 256],
    )
    print(f"완료: {OUT_PATH}")


if __name__ == "__main__":
    main()

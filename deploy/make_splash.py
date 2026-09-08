"""스플래시 화면 이미지(.png) 생성 — 앱 아이콘+이름+버전+ATEC 워터마크.

PyInstaller의 Splash 기능(exe 실행 직후~메인 창이 뜨기 전까지 보여주는
정적 이미지)에 쓸 자산이다. 정적 이미지 한 장만 지원해 로딩 바는 애니메이션
없이 고정된 모양으로 그린다.

결과물은 `deploy/`가 아니라 `ui/icons/`에 둔다 — `make_icon.py`와 같은 이유
(exe·인스톨러 자산, `deploy/app.spec`의 `datas`에 이미 `ui/icons` 통째로
등록돼 있어 빌드 설정을 새로 건드릴 필요가 없다).

    python -m deploy.make_splash
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_PATH = PROJECT_ROOT / "ui" / "icons" / "app.ico"
CI_LOGO_PATH = PROJECT_ROOT / "ui" / "icons" / "atecmobility_ci.png"
FONT_REGULAR = PROJECT_ROOT / "font" / "NanumGothic.ttf"
FONT_BOLD = PROJECT_ROOT / "font" / "NanumGothicBold.ttf"
OUT_PATH = PROJECT_ROOT / "ui" / "icons" / "splash.png"

WIDTH, HEIGHT = 480, 300
ACCENT = (37, 99, 235)  # #2563EB — DESIGN 전반이 공유하는 강조색
TITLE_COLOR = (26, 26, 26)
MUTED_COLOR = (138, 138, 138)
TRACK_COLOR = (238, 241, 245)


def _build() -> Image.Image:
    # 🔴 캔버스를 RGBA가 아니라 RGB로 잡는다 — 알파가 조금이라도 남으면 실제
    # 스플래시에서 색이 변한다. PyInstaller의 Windows 스플래시는 tk 창을
    # 마젠타(#FF00FF) 색상 키로 투명 처리하는 레이어드 창이라(`splash.py`의
    # "The color 'magenta' / '#ff00ff' must not be used ..." 주석 참고), 알파가
    # 255 미만인 픽셀이 **마젠타와 블렌딩**돼 보라색으로 그려진다.
    #
    # RGBA 캔버스에서 `paste(im, pos, im)`를 하면 그 일이 벌어진다 — 마스크
    # 합성이 RGB뿐 아니라 **바탕의 알파까지** 낮추기 때문에(255*(1-a)+a_src*a),
    # 안티에일리어싱된 가장자리에 반투명 픽셀이 남는다. 실측(2026-09-08):
    # 알파<255 픽셀 1,484개 = 실제 렌더링에서 원본과 달라진 픽셀 수와 정확히
    # 일치했고, 14px로 축소한 CI 워터마크의 한글("에이텍모빌리티")은 획이
    # 대부분 반투명이라 회청색(90,103,113)이 보라색(192,133,201)으로 보였다.
    # RGB 캔버스는 알파 자체가 없어 이 경로가 원천적으로 막힌다.
    img = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # 앱 아이콘(문서+돋보기, 둥근 사각형이 이미 이미지에 그려져 있다)
    icon = Image.open(ICON_PATH).convert("RGBA")
    icon_size = 76
    icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
    icon_x = (WIDTH - icon_size) // 2
    icon_y = 46
    img.paste(icon, (icon_x, icon_y), icon)

    title_font = ImageFont.truetype(str(FONT_BOLD), 20)
    version_font = ImageFont.truetype(str(FONT_REGULAR), 13)
    loading_font = ImageFont.truetype(str(FONT_REGULAR), 13)

    title = "오프라인 문서 검색"
    title_y = icon_y + icon_size + 16
    tw = draw.textlength(title, font=title_font)
    draw.text(((WIDTH - tw) / 2, title_y), title, font=title_font, fill=TITLE_COLOR)

    version = "v1.0.2"
    version_y = title_y + 30
    vw = draw.textlength(version, font=version_font)
    draw.text(((WIDTH - vw) / 2, version_y), version, font=version_font, fill=MUTED_COLOR)

    # 로딩 바 — 정적 이미지라 애니메이션 대신 고정된 진행 표시로 그린다.
    bar_w, bar_h = 160, 4
    bar_x = (WIDTH - bar_w) // 2
    bar_y = version_y + 40
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=2, fill=TRACK_COLOR
    )
    fill_w = int(bar_w * 0.4)
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], radius=2, fill=ACCENT
    )

    loading_text = "불러오는 중…"
    loading_y = bar_y + 14
    lw = draw.textlength(loading_text, font=loading_font)
    draw.text(((WIDTH - lw) / 2, loading_y), loading_text, font=loading_font, fill=MUTED_COLOR)

    # ATEC 워터마크 — 우측 하단(T10.44 상태바 워터마크와 같은 자리 관례)
    logo = Image.open(CI_LOGO_PATH).convert("RGBA")
    logo_h = 14
    logo_w = int(logo.width * logo_h / logo.height)
    logo = logo.resize((logo_w, logo_h), Image.LANCZOS)
    logo_x = WIDTH - logo_w - 16
    logo_y = HEIGHT - logo_h - 14
    img.paste(logo, (logo_x, logo_y), logo)

    return img


def main() -> None:
    img = _build()
    img.save(OUT_PATH)
    print(f"저장됨: {OUT_PATH} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()

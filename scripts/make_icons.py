"""Build ValhallISC icons from assets/valhallisc.jpg (run after changing the logo; outputs are committed).

The JPEG has a *fake* (baked-in) checkerboard background, so transparency is derived from darkness:
black lines -> opaque, the light checkerboard -> transparent.

Outputs:
  src/irisfs/gui/icons/tray.png, tray@2x.png   black glyph + alpha (macOS template image; recolored
                                               at runtime on Linux/Windows). Inner valknut + ring only:
                                               the rune ring is unreadable at 22 px.
  assets/app_icon_1024.png, valhallisc.icns/.ico   full logo on a white disc (app/bundle/exe icons)
  src/irisfs/gui/icons/app_<size>.png          the same logo for windows (title bar, taskbar)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "valhallisc.jpg"
ICONS = ROOT / "src" / "irisfs" / "gui" / "icons"
WINDOW_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def alpha_from_darkness(gray: Image.Image) -> Image.Image:
    return gray.point(lambda v: 0 if v >= 170 else (255 if v <= 90 else int((170 - v) * 255 / 80)))


def glyph(alpha: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", alpha.size, (*color, 0))
    img.putalpha(alpha)
    return img


def main() -> None:
    gray = Image.open(SRC).convert("L")
    w = gray.size[0]
    alpha = alpha_from_darkness(gray)

    # Tray: inner circle (valknut + inner ring), lines thickened a lot so they stay bold at 22 px
    # (MaxFilter 31 at 1024 px; 9 was too thin in the menu bar, 41 closes the gaps between triangles).
    c, r = w // 2, int(w * 0.335)
    circle = Image.new("L", gray.size, 0)
    ImageDraw.Draw(circle).ellipse((c - r, c - r, c + r, c + r), fill=255)
    inner = Image.composite(alpha, Image.new("L", gray.size, 0), circle).filter(ImageFilter.MaxFilter(31))
    inner = inner.crop((c - r, c - r, c + r, c + r))
    ICONS.mkdir(parents=True, exist_ok=True)
    for px, name in ((22, "tray.png"), (44, "tray@2x.png")):
        small = inner.resize((px, px), Image.LANCZOS).point(lambda v: min(255, int(v * 1.4)))
        glyph(small, (0, 0, 0)).save(ICONS / name)

    # App icon: full logo, black on a white disc (reads well on light and dark backgrounds).
    app = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    ImageDraw.Draw(app).ellipse((8, 8, w - 8, w - 8), fill=(255, 255, 255, 255))
    app.alpha_composite(glyph(alpha, (0, 0, 0)))
    app.save(ROOT / "assets" / "app_icon_1024.png")
    app.save(ROOT / "assets" / "valhallisc.icns")  # macOS bundle icon
    app.save(
        ROOT / "assets" / "valhallisc.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    window_icons(app)
    print("icons written")


def window_icons(app: Image.Image) -> None:
    """The logo for window title bars and the Windows taskbar / Linux window list (wx.IconBundle)."""
    for px in WINDOW_ICON_SIZES:
        app.resize((px, px), Image.LANCZOS).save(ICONS / f"app_{px}.png")


if __name__ == "__main__":
    main()

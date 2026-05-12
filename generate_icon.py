"""Generate YT-Audio-Extractor.ico from scratch.

Dark circle background (app --bg color) with an amber ring and amber play
triangle (app --accent color). Matches the favicon.svg design exactly. Drawn
fresh at each Windows icon size (16, 32, 48, 64, 128, 256) so small sizes
stay crisp.

Run once after cloning:   python generate_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


BG_COLOR = (11, 11, 13, 255)      # var(--bg)         #0b0b0d
ACCENT   = (255, 176, 32, 255)    # var(--accent)     #ffb020
SIZES = [16, 32, 48, 64, 128, 256]
OUT_PATH        = Path(__file__).parent / "YT-Audio-Extractor.ico"
OUT_PATH_STATIC = Path(__file__).parent / "static" / "favicon.ico"


def render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Full background circle.
    draw.ellipse([0, 0, size - 1, size - 1], fill=BG_COLOR)

    # Amber ring (two concentric filled circles; proportions match favicon.svg).
    outer = max(1, round(size * 0.045))
    inner = max(outer + 1, round(size * 0.080))
    draw.ellipse([outer, outer, size - 1 - outer, size - 1 - outer], fill=ACCENT)
    draw.ellipse([inner, inner, size - 1 - inner, size - 1 - inner], fill=BG_COLOR)

    # Play triangle — coords derived from favicon.svg viewBox (32×32):
    #   left x=12, right x=24, top y=8, bottom y=24, center y=16.
    cx, cy = size / 2, size / 2
    lx = cx - size * 0.125   # -4/32 from center
    rx = cx + size * 0.25    # +8/32 from center
    ty = cy - size * 0.25    # -8/32 from center
    by = cy + size * 0.25    # +8/32 from center
    draw.polygon([(lx, ty), (lx, by), (rx, cy)], fill=ACCENT)

    return img


def save_ico(path: Path, images: list) -> None:
    images[-1].save(
        path,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[:-1],
    )
    print(f"Wrote {path} ({path.stat().st_size} bytes)")


def main() -> None:
    images = [render(s) for s in SIZES]
    save_ico(OUT_PATH, images)
    save_ico(OUT_PATH_STATIC, images)


if __name__ == "__main__":
    main()

"""Generate YT-Audio-Extractor.ico from scratch.

A dark rounded-square background (matching the app's --bg color) with a play
triangle in the app's amber accent color. Drawn fresh at each Windows icon size
(16, 32, 48, 64, 128, 256) so the small sizes stay crisp instead of being
downsampled from a single source.

Run once after cloning:   python generate_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


BG_COLOR = (11, 11, 13, 255)      # var(--bg)         #0b0b0d
ACCENT   = (255, 176, 32, 255)    # var(--accent)     #ffb020
SIZES = [16, 32, 48, 64, 128, 256]
OUT_PATH = Path(__file__).parent / "YT-Audio-Extractor.ico"


def render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded-square background. Scale corner radius with size.
    pad = max(0, size // 32)
    radius = max(1, size // 6)
    draw.rounded_rectangle(
        (pad, pad, size - 1 - pad, size - 1 - pad),
        radius=radius,
        fill=BG_COLOR,
    )

    # Play triangle, optically centered (nudged right since the visual mass of
    # a right-pointing triangle is left-of-its-bounding-box-center).
    cx, cy = size / 2, size / 2
    tri_h = size * 0.55
    tri_w = tri_h * 0.866   # equilateral-ish ratio
    nudge = size * 0.04
    points = [
        (cx - tri_w * 0.45 + nudge, cy - tri_h * 0.5),
        (cx - tri_w * 0.45 + nudge, cy + tri_h * 0.5),
        (cx + tri_w * 0.55 + nudge, cy),
    ]
    draw.polygon(points, fill=ACCENT)
    return img


def main() -> None:
    images = [render(s) for s in SIZES]
    # PIL stacks the supplied images into a multi-resolution .ico when sizes
    # are supplied and the first image is large enough.
    images[-1].save(
        OUT_PATH,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[:-1],
    )
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

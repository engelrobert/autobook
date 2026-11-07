"""The autobook icon (the shapes of resources/autobook.svg) drawn with Pillow, for the tray and the .ico."""

from __future__ import annotations

from PIL import Image, ImageDraw

# viewBox 0 0 64 64, as in resources/autobook.svg
SHAPES = [
    ("rect", (6, 10, 58, 54), 4, "#c4502a"),
    ("rect", (10, 14, 31, 50), 2, "#f6f4ef"),
    ("rect", (33, 14, 54, 50), 2, "#f6f4ef"),
    ("poly", [(13, 42), (19, 33), (23, 38), (26, 35), (30, 42)], 0, "#2d6a8a"),
    ("circle", (17, 22, 3), 0, "#d69e2e"),
    ("rect", (36, 18, 51, 29), 0, "#7aa36b"),
    ("rect", (36, 32, 43, 46), 0, "#2d6a8a"),
    ("rect", (45, 32, 51, 46), 0, "#d69e2e"),
]


def draw_icon(size: int = 256) -> Image.Image:
    """The icon at size x size pixels, drawn at 4x and scaled down for smooth edges."""
    big = size * 4
    k = big / 64
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for kind, geo, radius, colour in SHAPES:
        if kind == "rect":
            d.rounded_rectangle([v * k for v in geo], radius=radius * k, fill=colour)
        elif kind == "poly":
            d.polygon([(x * k, y * k) for x, y in geo], fill=colour)
        else:
            cx, cy, r = geo
            d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=colour)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def write_ico(path) -> None:
    draw_icon(256).save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                                   (256, 256)])

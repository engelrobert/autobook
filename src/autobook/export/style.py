"""Page design: the frame around the photos and the background colours (book and single pages)."""

from __future__ import annotations

# hand-picked colours that print well; the keys are stored in the plan, the UI names them per language
SWATCHES = {
    "white": "#FFFFFF",
    "cream": "#F3EDE0",
    "lightgrey": "#E4E4E1",
    "sand": "#D8C9AE",
    "sage": "#B7C2A9",
    "dustyblue": "#A7B7C6",
    "anthracite": "#3B3B3D",
    "black": "#1A1A1A",
}
FRAME_MM = {"none": 0.0, "narrow": 8.0, "wide": 16.0}
INK = "#2D3A45"  # text on light pages
LIGHT_INK = "#F3EFE7"  # text on dark pages
OPENER_PREFIX = "chapter:"  # page colour key of a plain chapter opener: chapter:<chapter id>


def rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _luminance(hex_colour: str) -> float:
    def lin(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink_for(hex_colour: str) -> str:
    """Dark or light text, whichever reads better on this colour."""
    return INK if _contrast(hex_colour, INK) >= _contrast(hex_colour, LIGHT_INK) else LIGHT_INK


def paint(key: str) -> dict[str, str]:
    """Background and text colour of a swatch (unknown keys: white)."""
    bg = SWATCHES.get(key, SWATCHES["white"])
    return {"bg": bg, "ink": ink_for(bg)}

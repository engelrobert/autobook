"""Full-page images for Manual pages: cropped hero photos and plain backgrounds."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from autobook.thumbs import open_upright

PAPER = (244, 241, 234)  # warm off-white for text pages


def page_pixels(width_mm: float, height_mm: float, bleed_mm: float, dpi: float = 300) -> tuple[int, int]:
    """Pixel size of a full page including bleed."""
    return (round((width_mm + 2 * bleed_mm) / 25.4 * dpi), round((height_mm + 2 * bleed_mm) / 25.4 * dpi))


HERO_FOCUS = (0.5, 0.4)  # sides centred; a little more of the upper part (skies, heads)
# Darker parts under white titles, in the photo itself: printers want no transparency in the PDF
# (epubli: "may not be printed at all"). (where it starts or ends as a share of the height, darkest)
OPENER_SHADE = (0.42, 170 / 255)  # chapter pages: from 42 % down, darkest at the bottom edge
COVER_SCRIM = (0.45, 150 / 255)  # front cover: darkest at the top edge, gone at 45 %


def shade(im: Image.Image, edge: float, darkest: float, *, top: bool) -> Image.Image:
    """Darken towards the top (top=True: from the edge down to `edge` of the height) or towards the
    bottom (from `edge` down), linearly up to `darkest` (0..1): black laid over the photo, baked in."""
    w, h = im.size
    start = round(h * edge)
    column = []
    for y in range(h):
        t = (1 - y / start if y < start else 0.0) if top else ((y - start) / max(1, h - start) if y >= start else 0.0)
        column.append(round(255 * darkest * t))
    mask = Image.new("L", (1, h))
    mask.putdata(column)
    return Image.composite(Image.new(im.mode, im.size), im, mask.resize((w, h)))


def _crop(src: str | Path, target: float, focus: tuple[float, float] | None,
          point: tuple[float, float] | None = None) -> Image.Image:
    """The photo cut to the aspect ratio target (width / height). focus: where the cut is (0..1 of
    what is cut away); without one, point (the faces, 0..1 of the photo) is kept as central as possible."""
    im = open_upright(src)
    w, h = im.size
    if w / h > target:  # too wide: cut the sides
        nw = round(h * target)
        fx = focus[0] if focus else (_centre_on(point[0], w, nw) if point else HERO_FOCUS[0])
        left = round((w - nw) * fx)
        return im.crop((left, 0, left + nw, h))
    nh = round(w / target)  # too tall: cut top/bottom
    fy = focus[1] if focus else (_centre_on(point[1], h, nh) if point else HERO_FOCUS[1])
    top = round((h - nh) * fy)
    return im.crop((0, top, w, top + nh))


def _centre_on(at: float, full: int, part: int) -> float:
    """Where to cut (0..1) so that the point `at` (0..1 of full) sits in the middle of part."""
    return max(0.0, min(1.0, (at * full - part / 2) / (full - part))) if full > part else 0.5


def hero_crop(src: str | Path, out: Path, size: tuple[int, int],
              focus: tuple[float, float] | None = None, point: tuple[float, float] | None = None,
              shading: tuple[float, float] | None = None, *, top: bool = False) -> Path:
    """Crop a photo to the page aspect ratio (full bleed) and downscale to at most `size`.

    Full-page divider heroes and the front cover are cropped into a file this way; the template cuts
    the others. focus (x, y): where the cut is, 0 keeps the left/top edge, 1 the right/bottom one.
    shading: darken under the title (OPENER_SHADE towards the bottom, COVER_SCRIM with top=True).
    """
    tw, th = size
    im = _crop(src, tw / th, focus, point)
    icc = im.info.get("icc_profile")
    if im.width > tw:
        im = im.resize((tw, th), Image.Resampling.LANCZOS)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if shading:
        im = shade(im, *shading, top=top)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".jpg", ".jpeg"):
        im.save(out, "JPEG", quality=92, subsampling=0, icc_profile=icc)
    else:  # an older book's cover file keeps its name, so it keeps its type too
        im.save(out, icc_profile=icc)
    return out


def spread_halves(src: str | Path, out_left: Path, out_right: Path, page: tuple[int, int], spread_w: int,
                  focus: tuple[float, float] | None = None,
                  point: tuple[float, float] | None = None) -> tuple[Path, Path]:
    """A panorama across two facing pages: cut to the spread (both pages, outer bleed) and split.
    page: one page's pixels with bleed; spread_w: the spread's width in pixels (2 trims + 2 bleeds).
    The halves overlap by the two gutter bleeds, so the picture runs on across the fold."""
    pw, ph = page
    im = _crop(src, spread_w / ph, focus, point)
    k = im.width / spread_w
    half = round(pw * k)
    for box, out in (((0, 0, half, im.height), out_left), ((im.width - half, 0, im.width, im.height), out_right)):
        part = im.crop(box)
        if part.width > pw:
            part = part.resize((pw, ph), Image.Resampling.LANCZOS)
        out.parent.mkdir(parents=True, exist_ok=True)
        part.save(out, "JPEG", quality=92, subsampling=0)
    return out_left, out_right


def plain_background(out: Path, size: tuple[int, int], color: tuple[int, int, int] = PAPER) -> Path:
    """Solid page. A tiny mark in the bleed corner (cut off when trimming) makes every
    file unique, because Fotobuch skips files whose content hash it already knows."""
    out.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    # four 8 px patches, each 4 grey steps apart per hash nibble: survives JPEG (a 1-step mark did not)
    digest = hashlib.sha1(out.name.encode()).digest()
    for i in range(4):
        shade = 4 * (digest[i] % 16) + 4
        im.paste(tuple(max(0, c - shade) for c in color), (8 * i, 0, 8 * i + 8, 8))
    im.save(out, "JPEG", quality=95)
    return out

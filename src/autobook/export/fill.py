"""Even pages: every photo page fills the same frame box exactly.

Fotobuch never crops, so a page whose photos don't match the page shape keeps
white strips of random width. Here each page is arranged anew as nested rows
and columns of its own photos that fill the box exactly; the photos are trimmed
a little to make that work (the template draws them with fit: cover). Photos may
swap places within their page when that fits better. A page that would need
more than MAX_TRIM keeps its photos whole and gets even space on two opposite
sides instead.

Fotobuch re-solves automatic pages whose slots changed, so refitted pages are
stored as Manual pages; `to_auto` turns them back before Fotobuch edits the book.
"""

from __future__ import annotations

import itertools
import math
from functools import cache, lru_cache

MAX_TRIM = 0.15  # share of a photo that may be cut off to fill the page (user's choice, 2026-10-31)
TRIM_WEIGHT = 1.0
SIZE_WEIGHT = 0.2  # like Fotobuch's page solver: coverage 1.0, size 0.2
MAX_SIZE_RATIO = 6.0  # biggest / smallest photo on a page (after area weights) before it costs extra
RATIO_PENALTY = 1.0  # per ln unit above MAX_SIZE_RATIO
EMPTY_WEIGHT = 20.0  # even borders come first: an unfilled share of the box costs much more than trimming
ORDER_WEIGHT = 0.02  # keep the photos' own order unless another one is clearly better
FULL_SEARCH_MAX = 8  # photos per page searched in every arrangement; more: rows or columns only
PERMUTE_MAX = 6  # photos per page that may swap places
FULL_PAGE_TRIM = 0.35  # a photo alone on its page may lose this much to reach the paper edge

Box = tuple[float, float, float, float]  # x, y, w, h in mm


@cache
def _trees(n: int, horiz: bool, depth: int) -> tuple:
    """All ordered arrangements of n photos: a leaf is a photo index, a node is
    (horiz, children). horiz = children side by side; nested nodes alternate."""
    return tuple(_build(0, n, horiz, depth))


def _build(i: int, j: int, horiz: bool, depth: int):
    if j - i == 1:
        yield i
        return
    if depth == 0:
        return
    n = j - i
    for mask in range(1, 1 << (n - 1)):  # every way to cut i..j into consecutive runs
        cuts = [i] + [i + k + 1 for k in range(n - 1) if mask >> k & 1] + [j]
        parts = [list(_build(a, b, not horiz, depth - 1)) for a, b in zip(cuts, cuts[1:])]
        if all(parts):
            yield from ((horiz, kids) for kids in itertools.product(*parts))


def _affine(tree, aspects, gap):
    """Width of the arranged photos as a function of their height: w = a*h + b."""
    if isinstance(tree, int):
        return aspects[tree], 0.0
    horiz, kids = tree
    ab = [_affine(k, aspects, gap) for k in kids]
    if horiz:
        return sum(a for a, _ in ab), sum(b for _, b in ab) + gap * (len(kids) - 1)
    inv = sum(1 / a for a, _ in ab)
    return 1 / inv, (sum(b / a for a, b in ab) - gap * (len(kids) - 1)) / inv


def _place(tree, aspects, gap, x, y, w, h, out):
    if isinstance(tree, int):
        out[tree] = (x, y, w, h)
        return
    horiz, kids = tree
    ab = [_affine(k, aspects, gap) for k in kids]
    if horiz:
        sizes = [a * h + b for a, b in ab]
        room = w - gap * (len(kids) - 1)
    else:
        sizes = [(w - b) / a for a, b in ab]
        room = h - gap * (len(kids) - 1)
    k = room / sum(sizes)  # 1.0 when the box fits the arrangement; spreads rounding otherwise
    for kid, size in zip(kids, sizes):
        size *= k
        if horiz:
            _place(kid, aspects, gap, x, y, size, h, out)
            x += size + gap
        else:
            _place(kid, aspects, gap, x, y, w, size, out)
            y += size + gap


def _width(tree, aspects, s, gap, h) -> float:
    a, b = _affine(tree, [r * s for r in aspects], gap)
    return a * h + b


def trim(s: float) -> float:
    return 1 - min(s, 1 / s)


@lru_cache(maxsize=4096)
def _candidates(aspects: tuple[float, ...], w: float, h: float, gap: float, max_trim: float) -> list:
    """(tree, shape factor for every photo, fills the box) for every arrangement.
    One factor s for all photos: the width at height h only grows with s, so a tree
    can fill the box within the trim limit exactly when w lies between its widths
    at the two limits; bisection then finds s."""
    n = len(aspects)
    depth = 99 if n <= FULL_SEARCH_MAX else 2
    lo_s, hi_s = 1 - max_trim, 1 / (1 - max_trim)
    out = []
    for tree in _trees(n, True, depth) + (_trees(n, False, depth) if n > 1 else ()):
        if _width(tree, aspects, hi_s, gap, h) < w:
            out.append((tree, hi_s, False))
        elif _width(tree, aspects, lo_s, gap, h) > w:
            out.append((tree, lo_s, False))
        else:
            lo, hi = lo_s, hi_s
            for _ in range(40):
                mid = math.sqrt(lo * hi)
                lo, hi = (mid, hi) if _width(tree, aspects, mid, gap, h) < w else (lo, mid)
            out.append((tree, math.sqrt(lo * hi), True))
    return out


def _orders(photos: list[tuple[float, float]]) -> list[tuple[int, ...]]:
    """The photos' own order first, then every other order that looks different."""
    n = len(photos)
    if not 1 < n <= PERMUTE_MAX:
        return [tuple(range(n))]
    seen, out = set(), []
    for perm in itertools.permutations(range(n)):
        key = tuple((round(photos[i][0], 2), photos[i][1]) for i in perm)
        if key not in seen:
            seen.add(key)
            out.append(perm)
    return out


def fill_page(photos: list[tuple[float, float]], box: Box, gap: float,
              max_trim: float = MAX_TRIM, permute: bool = True) -> tuple[list[Box], float]:
    """photos: (aspect w/h, area weight) in page order. Returns one rect per photo
    (in that order) and the largest trim used. permute: photos may swap places."""
    if not photos:
        return [], 0.0
    _, rects, cut = _solve(_key(photos), tuple(box), gap, max_trim, permute)
    return list(rects), cut


def page_cost(photos: list[tuple[float, float]], box: Box, gap: float,
              max_trim: float = MAX_TRIM, permute: bool = False) -> float:
    """How well these photos fill one page (lower is better; same measure as fill_page).
    The default keeps their order, which is much faster: for trying many page splits."""
    return _solve(_key(photos), tuple(box), gap, max_trim, permute)[0] if photos else 0.0


def _key(photos) -> tuple:
    return tuple((round(a, 3), round(float(wt), 3)) for a, wt in photos)


@lru_cache(maxsize=65536)
def _solve(photos: tuple, box: Box, gap: float, max_trim: float, permute: bool) -> tuple:
    n = len(photos)
    x, y, w, h = box
    best = None
    for order in (_orders(photos) if permute else [tuple(range(n))]):
        aspects = tuple(round(photos[i][0], 3) for i in order)
        for tree, s, filled in _candidates(aspects, round(w, 2), round(h, 2), gap, max_trim):
            rects = _fit(tree, [a * s for a in aspects], gap, box)
            if rects is None:
                continue
            logs = [math.log(rw * rh / photos[i][1]) for (_, _, rw, rh), i in zip(rects, order)]
            mean = sum(logs) / n
            spread = math.sqrt(sum((v - mean) ** 2 for v in logs) / n)
            used = 1.0 if filled else (max(r[0] + r[2] for r in rects) - min(r[0] for r in rects)) * \
                (max(r[1] + r[3] for r in rects) - min(r[1] for r in rects)) / (w * h)
            cost = (TRIM_WEIGHT * trim(s) + SIZE_WEIGHT * spread + EMPTY_WEIGHT * (1 - used)
                    + RATIO_PENALTY * max(0.0, max(logs) - min(logs) - math.log(MAX_SIZE_RATIO))
                    + (ORDER_WEIGHT if order != tuple(range(n)) else 0.0))
            if best is None or cost < best[0]:
                out = [None] * n
                for r, i in zip(rects, order):
                    out[i] = r
                best = (cost, tuple(out), trim(s))
    if best is None:  # boxes too small for the gaps; cannot happen on real pages
        raise ValueError(f"cannot fit {n} photos into {box}")
    return best


def _fit(tree, aspects, gap, box: Box) -> list[Box] | None:
    """Arrange at the given shapes, as large as fits into box, centred."""
    x, y, w, h = box
    a, b = _affine(tree, aspects, gap)
    tw, th = a * h + b, h
    if tw > w + 1e-6:  # too wide: full width, less height
        tw, th = w, (w - b) / a
    if tw <= 0 or th <= 0:
        return None
    out: list = [None] * len(aspects)
    _place(tree, aspects, gap, x + (w - tw) / 2, y + (h - th) / 2, tw, th, out)
    if any(r[2] <= 0 or r[3] <= 0 for r in out):
        return None
    return out


def page_box(cfg, frame_mm: float) -> Box:
    """The area photos fill, in slot coordinates (relative to the trim corner plus
    Fotobuch's margin). No frame: the whole page including the bleed."""
    w, h, b = float(cfg["page_width_mm"]), float(cfg["page_height_mm"]), float(cfg["bleed_mm"])
    m = float(cfg.get("margin_mm", 0.0))
    if frame_mm <= 0:
        return (-b - m, -b - m, w + 2 * b, h + 2 * b)
    return (frame_mm - m, frame_mm - m, w - 2 * frame_mm, h - 2 * frame_mm)


def _photo_pages(data):
    """Index and page of every page with real photos (not the cover, not generated pages)."""
    from autobook.export.fotobuch import GEN_GROUP

    cover = data["config"]["book"].get("cover", {})
    start = 1 if cover and cover.get("active") else 0
    for i, page in enumerate(data["layout"]):
        ids = [str(x) for x in page.get("photos", [])]
        if i >= start and ids and not all(x.startswith(GEN_GROUP + "/") for x in ids):
            yield i, page


def refit_layout(data, frame_mm: float, max_trim: float = MAX_TRIM,
                 full_page: frozenset[str] = frozenset()) -> int:
    """Lay out every photo page anew into the frame box (as Manual pages, so
    Fotobuch keeps them). Returns how many pages could not be filled evenly.
    full_page: photos with a page to themselves; they run to the paper edge when
    that trims at most FULL_PAGE_TRIM, otherwise they are shown whole in the frame."""
    cfg = data["config"]["book"]
    box, gap = page_box(cfg, frame_mm), float(cfg["gap_mm"])
    files = {str(f["id"]): f for g in data["photos"] for f in g["files"]}
    uneven = 0
    for _, page in _photo_pages(data):
        ids = [str(x) for x in page["photos"]]
        photos = [(files[x]["width_px"] / files[x]["height_px"], float(files[x].get("area_weight", 1.0)))
                  for x in ids]
        if len(ids) == 1 and ids[0] in full_page:
            edge = page_box(cfg, 0)
            (r,), _ = fill_page(photos, edge, gap, FULL_PAGE_TRIM)
            if abs(r[2] - edge[2]) < 0.05 and abs(r[3] - edge[3]) < 0.05:  # reaches the paper edge
                page["slots"] = [{"x_mm": round(r[0], 2), "y_mm": round(r[1], 2), "width_mm": round(r[2], 2),
                                  "height_mm": round(r[3], 2)}]
                page["mode"] = "manual"
                continue
        rects, cut = fill_page(photos, box, gap, max_trim)
        x0, y0 = min(r[0] for r in rects), min(r[1] for r in rects)
        x1, y1 = max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)
        if x1 - x0 < box[2] - 0.05 or y1 - y0 < box[3] - 0.05:
            uneven += 1
        page["slots"] = [{"x_mm": round(rx, 2), "y_mm": round(ry, 2), "width_mm": round(rw, 2),
                          "height_mm": round(rh, 2)} for rx, ry, rw, rh in rects]
        page["mode"] = "manual"
    return uneven


def to_auto(data) -> None:
    """Hand the photo pages back to Fotobuch (before remove/place), keeping their slots."""
    for _, page in _photo_pages(data):
        page.pop("mode", None)

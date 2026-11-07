"""Even pages: photos fill the same frame box on every page (export/fill.py)."""

from __future__ import annotations

import pytest

from autobook.export.fill import MAX_TRIM, fill_page, page_box, refit_layout, to_auto
from autobook.export.style import INK, LIGHT_INK, ink_for, paint

EPS = 0.05
GAP = 4.0


def bbox(rects):
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects))


def check_tiling(rects, box, gap=GAP):
    """No overlaps, at least `gap` between photos, and every right/bottom edge either
    ends at the arrangement's edge or is followed by another photo exactly `gap` later."""
    x0, y0, x1, y1 = bbox(rects)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            apart = (a[0] + a[2] + gap <= b[0] + EPS or b[0] + b[2] + gap <= a[0] + EPS
                     or a[1] + a[3] + gap <= b[1] + EPS or b[1] + b[3] + gap <= a[1] + EPS)
            assert apart, (a, b)
        right, bottom = a[0] + a[2], a[1] + a[3]
        assert abs(right - x1) < EPS or any(abs(b[0] - right - gap) < EPS for b in rects), a
        assert abs(bottom - y1) < EPS or any(abs(b[1] - bottom - gap) < EPS for b in rects), a


def trims(photos, rects):
    return [1 - min(a / (w / h), (w / h) / a) for (a, _), (_, _, w, h) in zip(photos, rects)]


def test_a_page_fills_its_box_exactly_with_even_gaps():
    box = (8.0, 8.0, 281.0, 194.0)  # A4 landscape with a narrow frame
    photos = [(1.5, 1.0), (0.75, 1.0), (1.333, 1.0), (1.333, 1.0), (0.75, 1.0)]
    rects, cut = fill_page(photos, box, GAP)
    x0, y0, x1, y1 = bbox(rects)
    assert (abs(x0 - 8) < EPS and abs(y0 - 8) < EPS and abs(x1 - 289) < EPS and abs(y1 - 202) < EPS)
    check_tiling(rects, box)
    assert max(trims(photos, rects)) <= MAX_TRIM + 1e-6 and cut <= MAX_TRIM + 1e-6


def test_photos_keep_their_order_when_they_look_alike():
    rects, _ = fill_page([(1.5, 1.0)] * 4, (0.0, 0.0, 200.0, 200.0), GAP)
    keys = [(round(r[1]), round(r[0])) for r in rects]  # reading order: top to bottom, left to right
    assert keys == sorted(keys)


def test_a_bigger_photo_gets_more_room():
    rects, _ = fill_page([(1.333, 1.0), (1.333, 1.8), (1.333, 1.0)], (8.0, 8.0, 194.0, 194.0), GAP)
    areas = [w * h for _, _, w, h in rects]
    assert areas[1] > 1.3 * max(areas[0], areas[2])


def test_a_photo_is_never_trimmed_beyond_the_limit_but_centred():
    # a 4:3 photo alone on a square page would lose 25 %: it keeps 85 % and gets even space above and below
    box = (8.0, 8.0, 194.0, 194.0)
    (r,), cut = fill_page([(4 / 3, 1.0)], box, GAP)
    assert abs(cut - MAX_TRIM) < 1e-6
    assert abs(r[2] - 194) < EPS and r[3] < 194
    top, bottom = r[1] - 8, 202 - (r[1] + r[3])
    assert abs(top - bottom) < EPS and top > 5


def test_page_box_per_frame():
    cfg = {"page_width_mm": 210.0, "page_height_mm": 210.0, "bleed_mm": 3.0, "margin_mm": 0.0}
    assert page_box(cfg, 0) == (-3.0, -3.0, 216.0, 216.0)  # no frame: into the bleed
    assert page_box(cfg, 8) == (8.0, 8.0, 194.0, 194.0)


def test_refit_only_touches_photo_pages_and_hands_them_back():
    data = {
        "config": {"book": {"page_width_mm": 210.0, "page_height_mm": 210.0, "bleed_mm": 3.0, "margin_mm": 0.0,
                            "gap_mm": GAP, "cover": {"active": True}}},
        "photos": [{"group": "01_a", "files": [
            {"id": f"01_a/{i}.jpg", "width_px": w, "height_px": h, "area_weight": 1.0}
            for i, (w, h) in enumerate([(4000, 3000), (4000, 3000), (3000, 2000)])]},
            {"group": "zz_autobook", "files": [{"id": "zz_autobook/ab_div_c1.jpg", "width_px": 2551,
                                                "height_px": 2551, "area_weight": 1.0}]}],
        "layout": [
            {"page": 0, "photos": ["01_a/0.jpg"], "slots": [{"x_mm": 1, "y_mm": 2, "width_mm": 3, "height_mm": 4}]},
            {"page": 1, "photos": ["zz_autobook/ab_div_c1.jpg"], "mode": "manual",
             "slots": [{"x_mm": -3, "y_mm": -3, "width_mm": 216, "height_mm": 216}]},
            {"page": 2, "photos": ["01_a/0.jpg", "01_a/1.jpg", "01_a/2.jpg"], "slots": []},
        ],
    }
    cover, opener = dict(data["layout"][0]["slots"][0]), dict(data["layout"][1]["slots"][0])
    refit_layout(data, 8.0)
    assert data["layout"][0]["slots"][0] == cover and "mode" not in data["layout"][0]
    assert data["layout"][1]["slots"][0] == opener
    page = data["layout"][2]
    assert page["mode"] == "manual" and len(page["slots"]) == 3
    rects = [(s["x_mm"], s["y_mm"], s["width_mm"], s["height_mm"]) for s in page["slots"]]
    x0, y0, x1, y1 = bbox(rects)
    assert abs(x0 - 8) < EPS and abs(y0 - 8) < EPS and abs(x1 - 202) < EPS and abs(y1 - 202) < EPS
    to_auto(data)
    assert "mode" not in data["layout"][2] and data["layout"][1]["mode"] == "manual"


@pytest.mark.parametrize("key,ink", [("white", INK), ("sage", INK), ("dustyblue", INK),
                                     ("anthracite", LIGHT_INK), ("black", LIGHT_INK)])
def test_text_is_light_on_dark_colours(key, ink):
    assert paint(key)["ink"] == ink == ink_for(paint(key)["bg"])

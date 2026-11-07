"""End-to-end against the real fotobuch binary (skipped when it is not installed)."""

import json

import pytest
from pypdf import PdfReader

from autobook.brief.models import Brief
from autobook.catalog import Catalog
from autobook.export.fotobuch import (
    GEN_GROUP,
    export,
    fix_dividers,
    load_state,
    save_state,
    source_regex,
    staged_name,
)
from autobook.export.profile import available_profiles, load_profile
from autobook.export.release import release
from autobook.pipeline.select import build_plan
from autobook.plan.models import PlanPhoto
from tests.conftest import requires_fotobuch


def test_profiles_load():
    names = available_profiles()
    assert "square-21" in names
    for n in names:
        p = load_profile(n)
        assert p.page_width_mm > 0 and p.cover_enabled


def test_staged_names_are_unique_and_sortable():
    used: set[str] = set()
    p = PlanPhoto(id="p1", path="/x/IMG 0001.HEIC", ts="2026-07-01T10:12:03")
    a, b = staged_name(p, used), staged_name(p, used)
    assert a == "20260701-101203_img-0001.jpg" and b == "20260701-101203_img-0001_2.jpg"
    import re
    assert re.search(source_regex("01_x", a), "C:\\w\\staging\\01_x\\" + a)


def _layout(groups_pages):
    cfg = {"page_width_mm": 210.0, "page_height_mm": 210.0, "bleed_mm": 3.0, "margin_mm": 0.0,
           "cover": {"active": True}}
    lay = [{"page": 0, "photos": ["00_cover/cover.jpg"], "slots": []}]
    for g, n in groups_pages:
        for i in range(n):
            lay.append({"page": 0, "photos": [f"{g}/{i}a.jpg", f"{g}/{i}b.jpg"], "slots": []})
    return {"config": {"book": cfg}, "photos": [], "layout": lay}


@requires_fotobuch
@pytest.mark.fotobuch
def test_export_release_update_and_fix(work_copy, monkeypatch):
    plan = build_plan(work_copy, Brief(target_photos=30, language="de"), profile="square-21")
    plan.save(work_copy.plan)
    r = export(plan, work_copy, fresh=True)
    assert r.mode == "fresh" and r.pdf.exists() and r.pages % 2 == 0 and r.pages >= 24

    preview = load_state(r.project_dir)["config"]["preview"]
    assert preview["show_preview_watermark"] is False and preview["show_borders"] is False  # clean preview

    side = (r.project_dir / "autobook.yaml").read_text(encoding="utf-8")
    assert plan.chapters[0].title in side and "Kapitel" not in side and "stats" not in side

    # final PDF + preflight
    rep = release(work_copy)
    assert rep.ok, rep.render()
    reader = PdfReader(str(rep.files[0]))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert plan.book.title in text and plan.chapters[-1].title in text
    assert "\n13\n" in "\n" + text.replace(" ", "\n") + "\n"  # a page number
    inner = reader.pages[1]
    w = float(inner.mediabox.width) * 25.4 / 72
    assert abs(w - 216) < 0.3  # 210 mm + 2 × 3 mm bleed

    # incremental update: drop one photo, add one alternative, retitle
    ch = plan.chapters[1]
    dropped = ch.photos.pop(0)
    row = Catalog(work_copy.catalog).photo(ch.alternatives[0])
    ch.photos.append(PlanPhoto(id=row["id"], path=row["path"], ts=row["taken"], width=row["width"],
                               height=row["height"]))
    ch.title = "Neuer Titel"
    r2 = export(plan, work_copy, guides=True)
    assert r2.mode == "update"
    manifest = json.loads(work_copy.manifest.read_text())
    data = load_state(r2.project_dir)
    placed = {str(x) for p in data["layout"] for x in p["photos"]}
    assert manifest["photos"][row["id"]]["fid"] in placed
    assert dropped.id not in manifest["photos"]
    assert "Neuer Titel" in (r2.project_dir / "autobook.yaml").read_text(encoding="utf-8")
    assert data["config"]["preview"]["show_borders"] is True  # --guides switches them back on

    # change the cover without starting from scratch; the original photo must stay untouched
    import hashlib
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731
    cat = Catalog(work_copy.catalog)
    old_cover_src = cat.photo(plan.book.cover_photo)["path"]
    old_sha = sha(old_cover_src)
    new_cover = next(p.id for c in plan.chapters for p in c.photos if p.id != plan.book.cover_photo)
    plan.book.cover_photo = new_cover
    r3 = export(plan, work_copy)
    assert r3.mode == "update" and "cover photo replaced" in r3.warnings
    m = json.loads(work_copy.manifest.read_text())
    assert m["cover"]["plan_id"] == new_cover
    staged = work_copy.staging / m["cover"]["fid"]
    assert sha(staged) == sha(cat.photo(new_cover)["path"]) and sha(old_cover_src) == old_sha

    # simulate a full rebuild that leaves dividers in wrong places, then fix
    lay = data["layout"]
    gen = [p for p in lay if str(p["photos"][0]).startswith(GEN_GROUP + "/ab_div")]
    others = [p for p in lay if p not in gen]
    data["layout"] = others[:1] + gen + others[1:]
    save_state(r2.project_dir, data)
    fix_dividers(work_copy)
    data = load_state(r2.project_dir)
    for c in manifest["chapters"]:
        idx = next(i for i, p in enumerate(data["layout"]) if str(p["photos"][0]) == f"{GEN_GROUP}/ab_div_{c['id']}.jpg")
        assert str(data["layout"][idx + 1]["photos"][0]).startswith(c["group"] + "/")

    # bigger changes: patched page by page if the layout was edited by hand (fix_dividers saved it),
    # laid out from scratch if nobody touched it since the last export
    import autobook.export.fotobuch as fbx
    monkeypatch.setattr(fbx, "BIG_CHANGE_MIN", 2)
    monkeypatch.setattr(fbx, "BIG_CHANGE_SHARE", 0.0)

    def swap(n):
        for c in plan.chapters[:n]:
            alt = next(a for a in c.alternatives if a not in {p.id for p in c.photos})
            row = cat.photo(alt)
            c.photos[-1] = PlanPhoto(id=row["id"], path=row["path"], ts=row["taken"], width=row["width"],
                                     height=row["height"])
            c.alternatives.remove(alt)

    swap(2)
    r4 = export(plan, work_copy)
    assert r4.mode == "update" and r4.warnings[0] == "update: 2 added, 2 removed"
    placed = {str(x) for p in load_state(r4.project_dir)["layout"] for x in p["photos"]}
    m = json.loads(work_copy.manifest.read_text())
    assert all(m["photos"][p.id]["fid"] in placed for c in plan.chapters for p in c.photos)
    swap(2)
    r5 = export(plan, work_copy)
    assert r5.mode == "fresh" and "laid out from scratch" in r5.warnings[0]
    assert load_state(r5.project_dir)["config"]["preview"]["show_borders"] is True  # guides kept



def test_too_few_photos_are_padded_to_the_printers_minimum(work_copy):
    plan = build_plan(work_copy, Brief(target_photos=8), profile="square-21")
    r = export(plan, work_copy, fresh=True)
    assert r.pages == 24  # square-21: at least 24 pages
    assert any("blank pages added" in w for w in r.warnings)



@requires_fotobuch
def test_pages_fill_the_frame_and_take_colours(work_copy):
    import pypdfium2 as pdfium

    from autobook.export.fill import _photo_pages, page_box
    from autobook.export.fotobuch import _yaml
    from autobook.export.style import SWATCHES, paint

    def edges(data, frame):
        """Per photo page: the empty space (left, top, right, bottom) between the frame box and its photos."""
        x, y, w, h = page_box(data["config"]["book"], frame)
        out = []
        for _, page in _photo_pages(data):
            s = page["slots"]
            out.append((min(q["x_mm"] for q in s) - x, min(q["y_mm"] for q in s) - y,
                        x + w - max(q["x_mm"] + q["width_mm"] for q in s),
                        y + h - max(q["y_mm"] + q["height_mm"] for q in s)))
        return out

    plan = build_plan(work_copy, Brief(target_photos=30), profile="square-21")
    r = export(plan, work_copy, fresh=True)
    data = load_state(r.project_dir)
    assert all(p.get("mode") == "manual" for _, p in _photo_pages(data))  # Fotobuch keeps our slots
    for left, top, right, bottom in edges(data, 8):  # narrow frame: filled, or centred with even space
        assert abs(left - right) < 0.05 and abs(top - bottom) < 0.05 and min(left, top) < 0.05
    side = _yaml().load((r.project_dir / "autobook.yaml").read_text(encoding="utf-8"))
    assert side["frame_mm"] == 8 and side["paper"]["bg"] == SWATCHES["white"] and not side["fills"]

    # colours only: a quick update; the page with the chosen first photo turns anthracite
    m = json.loads(work_copy.manifest.read_text())
    plan_of = {v["fid"]: k for k, v in m["photos"].items()}
    idx, page = next(_photo_pages(data))
    plan.book.background = "sage"
    plan.book.page_colors = {plan_of[str(page["photos"][0])]: "anthracite"}
    opener = plan.chapters[0]  # a plain chapter page in its own colour
    opener.divider.style, opener.divider.photo = "plain", None
    plan.book.page_colors[f"chapter:{opener.id}"] = "black"
    (r.project_dir / "book.typ").write_text("// a template from an older autobook\n", encoding="utf-8")
    r2 = export(plan, work_copy)
    assert r2.mode == "update" and r2.warnings[0] == "update: 0 added, 0 removed"
    side = _yaml().load((r2.project_dir / "autobook.yaml").read_text(encoding="utf-8"))
    assert side["paper"] == paint("sage") and side["fills"] == {
        str(page["photos"][0]): paint("anthracite"), f"{GEN_GROUP}/ab_div_{opener.id}.jpg": paint("black")}
    from PIL import Image
    with Image.open(work_copy.render / f"ab_div_{opener.id}.jpg") as im:
        assert max(abs(a - b) for a, b in zip(im.getpixel((im.width // 2, im.height // 2)), (0x1A, 0x1A, 0x1A))) < 6
    doc = pdfium.PdfDocument(str(r2.pdf))
    try:
        def corner(i):  # a pixel in the frame, 4 mm in from the trim corner
            im = doc[i].render(scale=2).to_pil().convert("RGB")
            px = round(7 / 25.4 * 72 * 2)
            return im.getpixel((px, px))
        dark, other = corner(idx), corner(next(i for i, _ in _photo_pages(data) if i != idx))
    finally:
        doc.close()
    assert max(abs(a - b) for a, b in zip(dark, (0x3B, 0x3B, 0x3D))) < 12
    assert max(abs(a - b) for a, b in zip(other, (0xB7, 0xC2, 0xA9))) < 12

    # no frame: the photos run into the bleed
    plan.book.frame = "none"
    r3 = export(plan, work_copy)
    data = load_state(r3.project_dir)
    for left, top, right, bottom in edges(data, 0):
        assert abs(left - right) < 0.05 and abs(top - bottom) < 0.05 and min(left, top) < 0.05

    # choosing the cut: the template moves a trimmed photo inside its slot (here the cover, which loses a lot)
    def cover_slot_pixels(pdf):
        cfg = load_state(r3.project_dir)["config"]["book"]["cover"]
        slot = load_state(r3.project_dir)["layout"][0]["slots"][0]
        doc = pdfium.PdfDocument(str(pdf))
        try:
            im = doc[0].render(scale=0.5).to_pil().convert("L")
        finally:
            doc.close()
        k = 72 / 25.4 * 0.5
        x0, y0 = (slot["x_mm"] + cfg["bleed_mm"]) * k, (slot["y_mm"] + cfg["bleed_mm"]) * k
        x1, y1 = x0 + slot["width_mm"] * k, y0 + slot["height_mm"] * k
        box = (round(x0) + 2, round(y0) + 2, round(x1) - 2, round(y1) - 2)
        return list(im.crop(box).tobytes())

    page_photo = plan_of[str(page["photos"][0])]
    plan.book.crops = {plan.book.cover_photo: (0.0, 0.0), page_photo: (0.0, 1.0)}
    r4 = export(plan, work_copy)
    assert r4.mode == "update" and r4.warnings[0] == "update: 0 added, 0 removed"
    side = _yaml().load((r4.project_dir / "autobook.yaml").read_text(encoding="utf-8"))
    m = json.loads(work_copy.manifest.read_text())
    assert side["crops"] == {m["cover"]["fid"]: [0.0, 0.0], m["photos"][page_photo]["fid"]: [0.0, 1.0]}
    one_side = cover_slot_pixels(r4.pdf)
    plan.book.crops = {plan.book.cover_photo: (1.0, 1.0)}
    other_side = cover_slot_pixels(export(plan, work_copy).pdf)
    assert len(one_side) == len(other_side)
    assert sum(abs(a - b) for a, b in zip(one_side, other_side)) / len(one_side) > 4  # another part of the photo



@requires_fotobuch
def test_pages_follow_the_book_rules_and_your_choices(work_copy):
    from autobook.export.fotobuch import TITLE_PAGE, _yaml

    plan = build_plan(work_copy, Brief(target_photos=30), profile="square-21")
    r = export(plan, work_copy, fresh=True)
    m = json.loads(work_copy.manifest.read_text())
    fid = lambda pid: m["photos"][pid]["fid"]  # noqa: E731

    def inner():
        return [[str(x) for x in p["photos"]] for p in load_state(r.project_dir)["layout"][1:]]

    pages = inner()
    assert pages[0] == [f"{GEN_GROUP}/{TITLE_PAGE}"]  # page 1: the title page
    openers = [n for n, ids in enumerate(pages, 1) if ids[0].startswith(f"{GEN_GROUP}/ab_div_")]
    assert len(openers) == len(plan.chapters) and all(n % 2 == 0 for n in openers)  # left, facing the photos
    side = _yaml().load((r.project_dir / "autobook.yaml").read_text(encoding="utf-8"))
    assert side["dividers"][f"{GEN_GROUP}/{TITLE_PAGE}"]["title"] == plan.book.title and "focus" in side

    # your own split of a chapter, a page to itself, a panorama
    a, b = plan.chapters[0], plan.chapters[1]
    first, rest = a.photos[0].id, [p.id for p in a.photos[1:]]
    a.pages = [[first], rest]
    alone = b.photos[len(b.photos) // 2]
    alone.layout = "page"
    pano = b.photos[0]
    pano.layout = "spread"
    r = export(plan, work_copy)
    assert r.mode == "update"
    pages = inner()
    assert [fid(first)] in pages and [fid(alone.id)] in pages
    assert pano.id not in json.loads(work_copy.manifest.read_text())["photos"]  # drawn by autobook, not Fotobuch
    k = pages.index([f"{GEN_GROUP}/ab_pano_{pano.id}_l.jpg"])
    assert pages[k + 1] == [f"{GEN_GROUP}/ab_pano_{pano.id}_r.jpg"] and (k + 1) % 2 == 0  # starts on a left page
    assert all(n % 2 == 0 for n, ids in enumerate(pages, 1) if ids[0].startswith(f"{GEN_GROUP}/ab_div_"))

    # an update with new photos keeps your split; many new ones in your split make more pages, not crowded ones
    cat = Catalog(work_copy.catalog)
    row = cat.photo(plan.chapters[2].alternatives[0])
    plan.chapters[2].photos.append(PlanPhoto(id=row["id"], path=row["path"], ts=row["taken"], width=row["width"],
                                             height=row["height"]))
    for alt in a.alternatives[:8]:
        row = cat.photo(alt)
        a.photos.append(PlanPhoto(id=row["id"], path=row["path"], ts=row["taken"], width=row["width"],
                                  height=row["height"]))
    a.photos.sort(key=lambda p: p.ts)
    r = export(plan, work_copy)
    most = load_state(r.project_dir)["config"]["book_layout_solver"]["photos_per_page_max"]
    assert [fid(first)] in inner() and all(len(ids) <= most for ids in inner())


def test_chapter_photo_cut_follows_the_focus(tmp_path):
    from PIL import Image

    from autobook.render.pages import hero_crop

    src = tmp_path / "wide.jpg"
    im = Image.new("RGB", (900, 300), (240, 240, 240))
    im.paste((220, 20, 20), (0, 0, 60, 300))  # a red strip at the left edge
    im.save(src, quality=95)
    for x, red in ((0.0, True), (1.0, False), (None, False)):
        out = hero_crop(src, tmp_path / f"out_{x}.jpg", (300, 300), None if x is None else (x, 0.5))
        with Image.open(out) as o:
            assert (o.getpixel((5, 150))[0] > 180 and o.getpixel((5, 150))[1] < 80) is red, x


def test_split_cover(tmp_path):
    from pypdf import PdfWriter

    from autobook.export.release import split_cover

    w = PdfWriter()
    for size in ((1230, 630), (612, 612), (612, 612)):
        w.add_blank_page(*size)
    pdf = tmp_path / "book_final.pdf"
    with open(pdf, "wb") as f:
        w.write(f)
    cover, inner = split_cover(pdf)
    assert len(PdfReader(str(cover)).pages) == 1 and len(PdfReader(str(inner)).pages) == 2
    assert float(PdfReader(str(cover)).pages[0].mediabox.width) == 1230

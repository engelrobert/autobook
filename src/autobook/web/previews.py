"""Page previews for the review page, and the other shots of a moment."""

from __future__ import annotations

import json
from pathlib import Path

from autobook.catalog import Catalog
from autobook.discover.favourites import level as fav_level
from autobook.select.scoring import final_score
from autobook.settings import Work

PREVIEWS = "previews"
PAGE_WIDTH_PX = 900  # large enough for one spread across a laptop screen


def page_index(work: Work, sizes: list[list[float]] | None = None) -> list[dict]:
    """What is on each page of the exported book: [{n, kind, photos, chapter, rects}] in PDF order.

    n = page number as printed (0 = cover); kind = cover | title | photos | opener | spread | blank
    (a spread page is one half of a panorama: photos = [its photo id], side = l | r).
    rects: where each photo sits on the rendered page, as fractions of its width and height
    ({id, x, y, w, h}, plus ar = the slot's own width/height, which the cut follows); needs the page
    sizes in mm of the preview PDF (from render_pages).
    """
    from autobook.export.fotobuch import GEN_GROUP, PROJECT, TITLE_PAGE, load_state

    if not work.manifest.exists():
        return []
    manifest = json.loads(work.manifest.read_text(encoding="utf-8"))
    project = Path(manifest["project_dir"])
    if not (project / f"{PROJECT}.yaml").exists():
        return []
    data = load_state(project)
    plan_of = {v["fid"]: pid for pid, v in manifest["photos"].items()}
    cover = manifest.get("cover") or {}
    if cover.get("fid"):
        plan_of[cover["fid"]] = cover["plan_id"]
    cfg = data["config"]["book"]
    has_cover = bool((cfg.get("cover") or {}).get("active"))
    pages = []
    for i, page in enumerate(data["layout"]):
        ids = [str(x) for x in page["photos"]]
        n = i if has_cover else i + 1
        if has_cover and i == 0:
            kind = "cover"
        elif len(ids) == 1 and ids[0].startswith(f"{GEN_GROUP}/ab_div_"):
            kind = "opener"
        elif ids == [f"{GEN_GROUP}/{TITLE_PAGE}"]:
            kind = "title"
        elif len(ids) == 1 and ids[0].startswith(f"{GEN_GROUP}/ab_pano_"):
            kind = "spread"  # one half of a panorama: ab_pano_<photo id>_l / _r
        elif len(ids) == 1 and ids[0].startswith(f"{GEN_GROUP}/"):
            kind = "blank"
        else:
            kind = "photos"
        chapter = ids[0].split("ab_div_")[1].rsplit(".", 1)[0] if kind == "opener" else None
        pano = ids[0].split("ab_pano_")[1].rsplit(".", 1)[0] if kind == "spread" else None
        rects = []
        if sizes and i < len(sizes) and kind in ("photos", "cover"):
            w_mm, h_mm = sizes[i]
            bleed = float((cfg.get("cover") or {}).get("bleed_mm", cfg["bleed_mm"]) if kind == "cover"
                          else cfg["bleed_mm"])
            for f, slot in zip(ids, page.get("slots") or []):
                if f not in plan_of:
                    continue
                x, y = (float(slot["x_mm"]) + bleed) / w_mm, (float(slot["y_mm"]) + bleed) / h_mm
                w, h = float(slot["width_mm"]) / w_mm, float(slot["height_mm"]) / h_mm
                x0, y0, x1, y1 = max(0.0, x), max(0.0, y), min(1.0, x + w), min(1.0, y + h)
                rects.append({"id": plan_of[f], "x": round(x0, 4), "y": round(y0, 4),
                              "w": round(x1 - x0, 4), "h": round(y1 - y0, 4),
                              "ar": round(float(slot["width_mm"]) / float(slot["height_mm"]), 4)})
        pages.append({"n": n, "kind": kind, "photos": [plan_of[f] for f in ids if f in plan_of] or
                      ([pano[:-2]] if pano else []), "chapter": chapter, "rects": rects,
                      **({"side": pano[-1]} if pano else {})})
    return pages


def page_sizes(folder: Path) -> list[list[float]]:
    """Page sizes in mm of a rendered preview (written by render_pages)."""
    f = folder / "sizes.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def render_pages(work: Work) -> Path | None:
    """Render the preview PDF's pages once per export; returns the folder (page-000.jpg …, sizes.json)."""
    import pypdfium2 as pdfium

    if not work.manifest.exists():
        return None
    pdf = Path(json.loads(work.manifest.read_text(encoding="utf-8"))["project_dir"]) / "book.pdf"
    if not pdf.exists():
        return None
    out = work.root / PREVIEWS / str(int(pdf.stat().st_mtime))
    done = out / "done"
    if done.exists():
        return out
    out.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    sizes = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            sizes.append([round(page.get_width() * 25.4 / 72, 2), round(page.get_height() * 25.4 / 72, 2)])
            scale = (PAGE_WIDTH_PX * (2 if i == 0 else 1)) / page.get_width()  # the cover is a spread
            page.render(scale=scale).to_pil().convert("RGB").save(out / f"page-{i:03d}.jpg", quality=86)
    finally:
        doc.close()
    (out / "sizes.json").write_text(json.dumps(sizes), encoding="utf-8")
    done.write_text("ok")
    for old in (work.root / PREVIEWS).iterdir():  # keep only the current export's pages
        if old != out and old.is_dir():
            for f in old.iterdir():
                f.unlink()
            old.rmdir()
    return out


def siblings(work: Work, pid: str) -> list[dict]:
    """Other shots of the same moment (duplicate group or marked as nearly the same by the AI)."""
    from autobook.brief.models import Brief
    from autobook.pipeline.select import merge_similar

    cat = Catalog(work.catalog)
    try:
        rows = cat.joined()
        ai = cat.ai_latest("score")
        roots = cat.kv_get("roots", [])
    finally:
        cat.close()
    brief = Brief.model_validate_json(work.brief.read_text(encoding="utf-8")) if work.brief.exists() else Brief()
    markers = brief.favourite_markers if brief.favourites_mode != "ignore" else []
    for p in rows:
        p["ai"] = ai.get(p["id"])
    merge_similar(rows)
    by_id = {p["id"]: p for p in rows}
    if pid not in by_id:
        return []
    group = by_id[pid]["dup_group"]
    out = []
    for p in rows:
        if p["dup_group"] == group and p["id"] != pid:
            out.append({"id": p["id"], "taken": p["taken"], "w": p["width"], "h": p["height"],
                        "score": round(final_score(p["local_score"] or 0, p["ai"], brief.priorities), 3),
                        "fav": fav_level(p["path"], roots, markers),
                        "reason": (p["ai"] or {}).get("reason", "")})
    return sorted(out, key=lambda s: (-s["fav"], -s["score"]))

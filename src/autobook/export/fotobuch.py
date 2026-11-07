"""Book plan -> Fotobuch project, without forking Fotobuch ("Stage A").

How it maps:
  * one Fotobuch group per chapter (staging/NN_date_slug, hard links to the
    originals), plus 00_cover (the cover photo) and zz_autobook (generated
    full-page images: chapter openers, padding pages);
  * group order via sort_key, photo size via area_weight, no page mixing two
    chapters via book_layout_solver.group_max_per_page = 1;
  * the title page, chapter openers and panorama halves are Manual pages with one
    full-bleed slot. Our template (resources/autobook.typ) draws the titles on top,
    reading autobook.yaml next to the project;
  * after Fotobuch's first split into pages, autobook decides the final page order
    (export/paginate.py) and fills every photo page evenly (export/fill.py).
After a full `fotobuch rebuild --all`, `autobook fix-dividers` lays the pages out again.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from ruamel.yaml import YAML

from autobook.catalog import Catalog
from autobook.export.fill import MAX_TRIM, page_box, refit_layout, to_auto
from autobook.export.paginate import Book, ChapterPages, paginate
from autobook.export.profile import Profile, load_profile
from autobook.export.style import FRAME_MM, OPENER_PREFIX, paint, rgb
from autobook.i18n import format_range
from autobook.plan.models import BookPlan, Chapter, PlanPhoto
from autobook.render.pages import COVER_SCRIM, OPENER_SHADE, hero_crop, page_pixels, plain_background, spread_halves
from autobook.settings import Work, fotobuch_bin
from autobook.thumbs import open_upright

PROJECT = "book"
COVER_GROUP = "00_cover"
GEN_GROUP = "zz_autobook"
COVER_SORT_KEY = "1900-01-01T00:00:00+00:00"
SPINE_CLEARANCE_MM = 5.0  # between the spine and the front cover photo
GEN_SORT_KEY = "2999-12-31T00:00:00+00:00"
FOTOBUCH_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
# an update that changes more photos than this re-lays the book out, if nobody edited it in Fotobuch
BIG_CHANGE_MIN = 20
BIG_CHANGE_SHARE = 0.15
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class FotobuchError(RuntimeError):
    pass


class ExportConflict(RuntimeError):
    pass


class Fotobuch:
    """Runs the fotobuch CLI (list arguments only, never through a shell)."""

    def __init__(self, binary: str | None = None):
        self.bin = binary or fotobuch_bin()
        if not self.bin:
            raise FotobuchError("fotobuch binary not found: install it from "
                                "https://github.com/EddyXorb/fotobuch/releases or set FOTOBUCH_BIN")

    def run(self, args: list[str], cwd: Path) -> str:
        env = {**os.environ, "NO_COLOR": "1", "RUST_LOG": os.environ.get("RUST_LOG", "info")}
        p = subprocess.run([self.bin, *map(str, args)], cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = ANSI.sub("", (p.stdout or "") + (p.stderr or ""))
        if p.returncode != 0:
            tail = "\n".join(out.strip().splitlines()[-25:])
            raise FotobuchError(f"fotobuch {' '.join(map(str, args))} failed (exit {p.returncode}):\n{tail}")
        return out

    def version(self) -> str:
        from autobook.settings import cache_dir

        # fotobuch writes a log/ folder into its working directory; keep that out of the user's cwd
        return self.run(["--version"], cache_dir()).strip().split()[-1]


# ── naming and staging ───────────────────────────────────────────────────────
def slug(text: str, n: int = 30) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:n].strip("-") or "x"


def chapter_group(i: int, ch: Chapter) -> str:
    return f"{i:02d}_{ch.date_from}_{slug(ch.title)}"


def staged_name(photo: PlanPhoto, used: set[str]) -> str:
    stamp = photo.ts.replace("-", "").replace(":", "").replace("T", "-")[:15]
    src = Path(photo.path)
    ext = src.suffix.lower()
    if ext not in FOTOBUCH_EXTS:
        ext = ".jpg"  # HEIC etc. are converted
    base = f"{stamp}_{slug(src.stem, 40)}"
    name, k = base + ext, 2
    while name.lower() in used:
        name, k = f"{base}_{k}{ext}", k + 1
    used.add(name.lower())
    return name


def remove_project(project_dir: Path) -> None:
    """Delete Fotobuch's project folder. Git makes its objects read-only, and Windows refuses to delete
    read-only files, so those are made writable first. Only for the project folder: the staging folders
    hold hard links to the originals, whose read-only mark must stay."""
    def writable_and_retry(func, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    shutil.rmtree(project_dir, onexc=writable_and_retry)


def link_or_copy(src: Path, dst: Path) -> None:
    """Hard link (no extra space), copy when linking is impossible (other drive, FAT, …)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if dst.stat().st_size == src.stat().st_size:
            return
        dst.unlink()
    if src.suffix.lower() not in FOTOBUCH_EXTS:
        im = open_upright(src)  # HEIC → JPEG, orientation applied
        im.save(dst, "JPEG", quality=95, subsampling=0, icc_profile=im.info.get("icc_profile"))
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def cover_config(profile: Profile, plan: BookPlan) -> dict:
    """Fotobuch's cover settings. From the sizes typed in from the printer's cover calculator: the
    margin all round becomes the cover's bleed and the spine is fixed. Else the profile's estimate."""
    c = profile.cover
    size = plan.book.cover_size if profile.cover_typed else None
    if size:
        return {"front_back_width_mm": size.width_mm - 2 * size.margin_mm,
                "height_mm": size.height_mm - 2 * size.margin_mm, "bleed_mm": size.margin_mm,
                "spine_mode": "fixed", "spine_width_mm": size.spine_mm, "spine_clearance_mm": SPINE_CLEARANCE_MM}
    return {"front_back_width_mm": float(c["front_back_width_mm"]), "height_mm": float(c["height_mm"]),
            "bleed_mm": float(c.get("bleed_mm", profile.bleed_mm)), "spine_mode": "auto",
            "spine_mm_per_10_pages": float(c.get("spine_mm_per_10_pages", 1.0)),
            "spine_clearance_mm": SPINE_CLEARANCE_MM}


def cover_photo_px(cfg: dict, dpi: float) -> tuple[int, int]:
    """Pixels of the front cover photo as the template draws it: Fotobuch's slot (from the spine's
    clearance to the outer edge) and the bleed on its three outer sides."""
    spine = cfg["spine_width_mm"] / 2 if cfg["spine_mode"] == "fixed" else 0.0  # auto: the spine starts at the middle
    w = cfg["front_back_width_mm"] / 2 - spine - cfg["spine_clearance_mm"] + cfg["bleed_mm"]
    h = cfg["height_mm"] + 2 * cfg["bleed_mm"]
    return round(w / 25.4 * dpi), round(h / 25.4 * dpi)


def stage_cover(plan: BookPlan, work: Work, cat: Catalog, size: tuple[int, int], name: str = "cover.jpg") -> dict:
    """The front cover photo, cut to its place on the cover (your cut or the faces kept in view) and
    darker at the top under the white title. Returns the cover info for the manifest."""
    from autobook.ai.focus import known_focus

    row = cat.photo(plan.book.cover_photo) if plan.book.cover_photo else None
    if not row:
        return {}
    dst = work.staging / COVER_GROUP / name
    tmp = dst.with_name("new-" + name)
    hero_crop(row["path"], tmp, size, plan.book.crops.get(plan.book.cover_photo),
              known_focus(cat).get(plan.book.cover_photo), COVER_SCRIM, top=True)
    os.replace(tmp, dst)  # also breaks a hard link to an original instead of writing through it
    return {"plan_id": plan.book.cover_photo, "fid": f"{COVER_GROUP}/{name}"}


def source_regex(group: str, filename: str) -> str:
    """Regex matching a staged file's source path, for fotobuch remove/place."""
    return re.escape(group) + r"[\\/]" + re.escape(filename) + "$"


def any_regex(patterns: list[str], chunk: int = 40) -> list[str]:
    """Join source regexes into alternations, in chunks that keep command lines short (Windows)."""
    return ["(?:" + "|".join(patterns[i:i + chunk]) + ")" for i in range(0, len(patterns), chunk)]


def project_sha(project_dir: Path) -> str:
    """Fingerprint of the project file, to tell whether someone edited the layout in Fotobuch."""
    return hashlib.sha1((project_dir / f"{PROJECT}.yaml").read_bytes()).hexdigest()


# ── YAML helpers ─────────────────────────────────────────────────────────────
def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    return y


def load_state(project_dir: Path):
    path = project_dir / f"{PROJECT}.yaml"
    with open(path, encoding="utf-8") as f:
        return _yaml().load(f)


def save_state(project_dir: Path, data) -> None:
    path = project_dir / f"{PROJECT}.yaml"
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _yaml().dump(data, f)
    os.replace(tmp, path)


def apply_order_and_weights(data, sort_keys: dict[str, str], weights: dict[str, float]) -> None:
    for g in data["photos"]:
        if g["group"] in sort_keys:
            g["sort_key"] = sort_keys[g["group"]]
        for f in g["files"]:
            if f["id"] in weights:
                f["area_weight"] = float(weights[f["id"]])
    data["photos"].sort(key=lambda g: str(g["sort_key"]))


def _full_bleed_page(pid: str, cfg) -> dict:
    b, m = float(cfg["bleed_mm"]), float(cfg.get("margin_mm", 0.0))
    return {"page": 0, "photos": [pid],
            "slots": [{"x_mm": -(b + m), "y_mm": -(b + m),
                       "width_mm": float(cfg["page_width_mm"]) + 2 * b,
                       "height_mm": float(cfg["page_height_mm"]) + 2 * b}],
            "mode": "manual"}


def inner_page_count(data) -> int:
    cfg = data["config"]["book"]
    has_cover = bool(cfg.get("cover", {}) and cfg["cover"].get("active"))
    return len(data["layout"]) - (1 if has_cover else 0)


# ── generated images and sidecar ─────────────────────────────────────────────
@dataclass
class Generated:
    files: dict[str, Path] = field(default_factory=dict)  # staged filename -> rendered file
    dividers: list[tuple[str, str]] = field(default_factory=list)  # (chapter group, filename)
    info: dict[str, dict] = field(default_factory=dict)  # filename -> sidecar info
    title: str | None = None  # filename of the title page (page 1)
    spreads: dict[str, list[tuple[float, str, str]]] = field(default_factory=dict)  # group -> (time, left, right)


TITLE_PAGE = "ab_title.jpg"


def render_generated(plan: BookPlan, work: Work, profile: Profile, cat: Catalog,
                     groups: dict[str, str]) -> Generated:
    """Full pages made by autobook: the title page, chapter pages (the hero photo cropped to the
    full page, or plain paper in the page colour) and panoramas across two pages."""
    from autobook.ai.focus import known_focus

    px = page_pixels(profile.page_width_mm, profile.page_height_mm, profile.bleed_mm, profile.dpi)
    focus = known_focus(cat)
    gen = Generated()
    dst = work.render / TITLE_PAGE
    gen.files[TITLE_PAGE] = plain_background(dst, px, rgb(paint(plan.book.background)["bg"]))
    gen.title = TITLE_PAGE
    gen.info[TITLE_PAGE] = {"kind": "divider", "style": "plain", "title": plan.book.title,
                            "subtitle": plan.book.subtitle,
                            "date": format_range(_date(plan.book.date_from), _date(plan.book.date_to),
                                                 plan.book.language)}
    spread_w = round((2 * profile.page_width_mm + 2 * profile.bleed_mm) / 25.4 * profile.dpi)
    for ch in plan.chapters:
        for p in ch.photos:
            if p.layout != "spread":
                continue
            left, right = f"ab_pano_{p.id}_l.jpg", f"ab_pano_{p.id}_r.jpg"
            try:
                spread_halves(p.path, work.render / left, work.render / right, px, spread_w,
                              plan.book.crops.get(p.id), focus.get(p.id))
            except Exception:  # a broken file must not stop the export: the photo is left out
                continue
            for name in (left, right):
                gen.files[name] = work.render / name
                gen.info[name] = {"kind": "spread"}
            gen.spreads.setdefault(groups[ch.id], []).append((_secs(p.ts), f"{GEN_GROUP}/{left}",
                                                              f"{GEN_GROUP}/{right}"))
    for ch in plan.chapters:
        name = f"ab_div_{ch.id}.jpg"
        dst = work.render / name
        style = ch.divider.style
        paper = rgb(paint(plan.book.page_colors.get(OPENER_PREFIX + ch.id, plan.book.background))["bg"])
        try:
            if style == "hero" and ch.divider.photo and cat.photo(ch.divider.photo):
                hero_crop(cat.photo(ch.divider.photo)["path"], dst, px, plan.book.crops.get(ch.divider.photo),
                          focus.get(ch.divider.photo), OPENER_SHADE)
            else:
                style = "plain"
                plain_background(dst, px, paper)
        except Exception:  # a broken hero file must not stop the export
            style = "plain"
            plain_background(dst, px, paper)
        gen.files[name] = dst
        gen.dividers.append((groups[ch.id], name))
        gen.info[name] = {"kind": "divider", "style": style, "title": ch.title, "subtitle": ch.subtitle,
                          "date": format_range(_date(ch.date_from), _date(ch.date_to), plan.book.language)}
    return gen


def _date(iso: str):
    from datetime import date

    return date.fromisoformat(iso[:10])


def page_fills(plan: BookPlan, photos: dict[str, dict]) -> dict[str, dict[str, str]]:
    """Fotobuch photo id -> page colours, for pages with their own colour (keyed by their first photo)."""
    fills = {}
    for key, colour in plan.book.page_colors.items():
        if key.startswith(OPENER_PREFIX):
            fills[f"{GEN_GROUP}/ab_div_{key.removeprefix(OPENER_PREFIX)}.jpg"] = paint(colour)
        elif key in photos:
            fills[photos[key]["fid"]] = paint(colour)
    return fills


def photo_crops(plan: BookPlan, photos: dict[str, dict]) -> dict[str, list[float]]:
    """Fotobuch photo id -> where the template cuts it (the cover photo is staged already cut)."""
    return {photos[pid]["fid"]: [round(x, 4), round(y, 4)] for pid, (x, y) in plan.book.crops.items() if pid in photos}


def photo_focus(photos: dict[str, dict], focus: dict[str, tuple[float, float]]) -> dict[str, list[float]]:
    """Fotobuch photo id -> where the faces are (0..1 of the photo), for photos without your own cut."""
    return {photos[pid]["fid"]: list(xy) for pid, xy in focus.items() if pid in photos}


def write_sidecar(project_dir: Path, plan: BookPlan, gen: Generated, extra_end: list[str] | None = None,
                  photos: dict[str, dict] | None = None,
                  focus: dict[str, tuple[float, float]] | None = None) -> None:
    fid = lambda name: f"{GEN_GROUP}/{name}"  # noqa: E731
    dividers = {fid(n): {k: v for k, v in info.items() if k != "kind"}
                for n, info in gen.info.items() if info["kind"] == "divider"}
    full = {fid(n): {"kind": "blank"} for n in extra_end or []}  # no page number on padding pages
    full.update({fid(n): {"kind": "spread"} for n, info in gen.info.items() if info["kind"] == "spread"})
    dates = format_range(_date(plan.book.date_from), _date(plan.book.date_to), plan.book.language)
    side = {
        "style": {"font": "Libertinus Serif", "ink": "#2d3a45", "accent": "#c4502a", "safe_margin_mm": 12},
        "page_numbers": {"enabled": True},
        "frame_mm": FRAME_MM[plan.book.frame],
        "paper": paint(plan.book.background),
        "fills": page_fills(plan, photos or {}),
        "crops": photo_crops(plan, photos or {}),
        "focus": photo_focus(photos or {}, focus or {}),
        "cover": {"title": plan.book.title, "subtitle": plan.book.subtitle, "dates": dates,
                  "back_text": f"{plan.book.title} · {dates}", "scrim": True},
        "dividers": dividers,
        "fullpage": full,
    }
    with open(project_dir / "autobook.yaml", "w", encoding="utf-8") as f:
        _yaml().dump(side, f)


def _template() -> str:
    tpl = resources.files("autobook.resources").joinpath("autobook.typ").read_text(encoding="utf-8")
    return tpl.replace("{project_name}", PROJECT)


def install_template(project_dir: Path) -> None:
    (project_dir / f"{PROJECT}.typ").write_text(_template(), encoding="utf-8")


def template_current(project_dir: Path) -> bool:
    """False for a book laid out by an older autobook (it is laid out again to get even pages and colours)."""
    try:
        return (project_dir / f"{PROJECT}.typ").read_text(encoding="utf-8") == _template()
    except OSError:
        return False


# ── export ───────────────────────────────────────────────────────────────────
@dataclass
class ExportResult:
    project_dir: Path
    pdf: Path
    pages: int
    warnings: list[str]
    mode: str  # "fresh" | "update"
    blank_pages: int = 0  # white pages added at the end to reach the printer's minimum page count


def solver_settings(plan: BookPlan, profile: Profile) -> dict[str, object]:
    """Page budget for Fotobuch's page solver (it tends towards page_min, so set it with care)."""
    n = plan.photo_count()
    chapters = len(plan.chapters)
    extra = chapters  # chapter opener pages
    per_page = max(1.0, plan.book.photos_per_page)
    target = max(chapters, round(n / per_page))
    floor = max(chapters, int(profile.print.get("page_count_min", 0) or 0) - extra)
    target = min(n, max(target, floor))  # never more pages than photos
    page_min = min(target, max(floor, round(target * 0.92)))
    return {
        "book_layout_solver.group_max_per_page": 1,
        "book_layout_solver.group_min_photos": 1,
        "book_layout_solver.photos_per_page_min": 1,
        # room above the average, so dense books can still mix big and small photos
        "book_layout_solver.photos_per_page_max": max(profile.photos_per_page_max, math.ceil(per_page) + 1),
        "book_layout_solver.page_target": target,
        "book_layout_solver.page_min": page_min,
        "book_layout_solver.page_max": max(chapters, round(target * 1.15)) + 1,
    }


def export(plan: BookPlan, work: Work, profile: Profile | str | None = None, *, fresh: bool = False,
           fb: Fotobuch | None = None, progress=lambda *a: None, guides: bool | None = None) -> ExportResult:
    """guides: show Fotobuch's PREVIEW watermark and bleed/margin lines in the preview PDF.
    None = off for a new project, unchanged for an update. The release PDF never has them."""
    profile = load_profile(profile or plan.book.profile) if not isinstance(profile, Profile) else profile
    fb = fb or Fotobuch()
    project_dir = work.project_parent / PROJECT
    note = None
    if project_dir.exists() and not fresh:
        before = (json.loads(work.manifest.read_text(encoding="utf-8")) if work.manifest.exists() else {}).get("profile")
        if not before or before == profile.name or _same_pages(before, profile):
            return _update(plan, work, profile, fb, project_dir, progress, guides)
        note = f"new format ({before} -> {profile.name}): laid out from scratch"  # nothing of the old pages fits
    if project_dir.exists():
        if not (project_dir / f"{PROJECT}.yaml").exists():
            raise ExportConflict(f"{project_dir} exists but is not an autobook/Fotobuch project; not deleting it")
        remove_project(project_dir)
    r = _fresh(plan, work, profile, fb, project_dir, progress, bool(guides))
    if note:
        r.warnings.insert(0, note)
    return r


def _same_pages(name: str, profile: Profile) -> bool:
    try:
        return load_profile(name).same_pages(profile)
    except ValueError:  # a profile file that is gone
        return False


def _guide_settings(on: bool) -> dict[str, bool]:
    return {"preview.show_preview_watermark": on, "preview.show_borders": on}


def _stage(plan: BookPlan, work: Work) -> tuple[dict, dict[str, str]]:
    """Stage chapter files. Returns (photos manifest, chapter id -> group)."""
    groups = {ch.id: chapter_group(i, ch) for i, ch in enumerate(plan.chapters, 1)}
    photos: dict[str, dict] = {}
    for ch in plan.chapters:
        used: set[str] = set()
        g = groups[ch.id]
        existing = work.staging / g
        if existing.exists():
            used.update(p.name.lower() for p in existing.iterdir())
        for p in ch.photos:
            if p.layout == "spread":  # drawn by autobook across two pages, not a Fotobuch photo
                continue
            name = staged_name(p, used)
            link_or_copy(Path(p.path), work.staging / g / name)
            photos[p.id] = {"fid": f"{g}/{name}", "group": g, "file": name, "chapter": ch.id,
                            "weight": p.weight}
    return photos, groups


def _stage_generated(gen: Generated, work: Work) -> None:
    gdir = work.staging / GEN_GROUP
    gdir.mkdir(parents=True, exist_ok=True)
    for name, src in gen.files.items():
        dst = gdir / name
        if dst.exists():
            dst.unlink()
        shutil.copy2(src, dst)


def _sort_keys(plan: BookPlan, groups: dict[str, str]) -> dict[str, str]:
    """Strictly increasing sort keys in book order (theme chapters are not chronological)."""
    keys = [f"{ch.date_from}T00:{i // 60:02d}:{i % 60:02d}+00:00" for i, ch in enumerate(plan.chapters)]
    if any(a >= b for a, b in zip(keys, keys[1:])):
        keys = [f"2000-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00" for i in range(len(plan.chapters))]
    return {groups[ch.id]: k for ch, k in zip(plan.chapters, keys)}


def book_pages(data, plan: BookPlan, photos: dict[str, dict], gen: Generated, profile: Profile) -> Book:
    """What paginate needs: Fotobuch's first split into pages per chapter (or your own split),
    photo shapes and times, the generated pages and the printer's page rules."""
    cfg = data["config"]["book"]
    files = {str(f["id"]): f for g in data["photos"] for f in g["files"]}
    most = int(data["config"].get("book_layout_solver", {}).get("photos_per_page_max", 6))
    fid_of = {pid: v["fid"] for pid, v in photos.items() if v["fid"] in files}
    time = {fid: _secs(str(f["timestamp"])) for fid, f in files.items() if f.get("timestamp")}
    known = set(fid_of.values())
    by_group: dict[str, list[list[str]]] = {}
    for page in data["layout"][(1 if (cfg.get("cover") or {}).get("active") else 0):]:
        ids = [str(x) for x in page["photos"] if str(x) in known]
        if ids:
            by_group.setdefault(ids[0].split("/")[0], []).append(ids)
    chapters = []
    for ch, (group, opener) in zip(plan.chapters, gen.dividers):
        mine = [fid_of[p.id] for p in ch.photos if p.id in fid_of]
        if ch.pages:  # your own split: photos that left the chapter drop out
            pages = [[fid_of[i] for i in pg if i in fid_of and fid_of[i] in mine] for pg in ch.pages]
        else:
            pages = [[i for i in pg if i in mine] for pg in by_group.get(group, [])]
        pages = [pg for pg in pages if pg]
        placed = {i for pg in pages for i in pg}
        for fid in mine:  # new in the chapter: onto the page whose photos are closest in time
            if fid in placed:
                continue
            if pages:
                best = min(pages, key=lambda pg: min(abs(time.get(i, 0) - time.get(fid, 0)) for i in pg))
                best.append(fid)
                best.sort(key=lambda i: time.get(i, 0))
            else:
                pages.append([fid])
        pages = [part for pg in pages for part in _split_full(pg, most)]  # many new photos: more pages
        chapters.append(ChapterPages(group=group, opener=f"{GEN_GROUP}/{opener}", pages=pages,
                                     custom=bool(ch.pages), spreads=gen.spreads.get(group, [])))
    return Book(
        chapters=chapters,
        shape={fid: (f["width_px"] / f["height_px"], float(f.get("area_weight", 1.0))) for fid, f in files.items()},
        time=time,
        box=page_box(cfg, FRAME_MM[plan.book.frame]), gap=float(cfg["gap_mm"]),
        max_per_page=most,
        title=f"{GEN_GROUP}/{gen.title}" if gen.title else None, per_page=plan.book.photos_per_page,
        own_page=frozenset(fid_of[p.id] for p in plan.all_photos() if p.layout == "page" and p.id in fid_of),
        page_step=int(profile.print.get("page_count_step", 1) or 1),
        min_pages=int(profile.print.get("page_count_min", 0) or 0),
        max_pages=int(profile.print.get("page_count_max", 0) or 0))


def _split_full(ids: list[str], most: int) -> list[list[str]]:
    """A page with more photos than a page holds, as several even pages (in order)."""
    n = math.ceil(len(ids) / most)
    size = math.ceil(len(ids) / n)
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def _finish(plan, work, profile, fb, project_dir, gen, cat, warnings, photos) -> tuple[int, Path, int]:
    """Decide the pages (paginate), add blank pages only where nothing else works, fill every
    photo page evenly, write the sidecar, build. Returns (inner pages, preview PDF, blank pages)."""
    data = load_state(project_dir)
    _check_generated(data, gen)
    book = book_pages(data, plan, photos, gen, profile)
    res = paginate(book)
    blanks = [f"ab_blank_{i + 1}.jpg" for i in range(sum(p.kind == "blank" for p in res.pages) + res.blanks)]
    if res.blanks:
        inner = len(res.pages)
        warnings.append(f"only {inner} pages: {res.blanks} blank pages added at the end to reach the printer's "
                        f"minimum of {inner + res.blanks} (add photos to fill them)")
    if blanks:  # distinct files: Fotobuch keeps one photo per content hash
        px = page_pixels(profile.page_width_mm, profile.page_height_mm, profile.bleed_mm, profile.dpi)
        paper = rgb(paint(plan.book.background)["bg"])
        for b in blanks:
            gen.files[b] = plain_background(work.render / b, px, paper)  # like the photo pages around them
        _stage_generated(gen, work)
        fb.run(["add", "--update", work.staging / GEN_GROUP], project_dir)
        data = load_state(project_dir)
        _check_generated(data, gen)
    cfg = data["config"]["book"]
    free = iter(f"{GEN_GROUP}/{b}" for b in blanks)
    pages = data["layout"][:1] if (cfg.get("cover") or {}).get("active") else []
    for p in res.pages:
        if p.kind == "photos":
            pages.append({"page": 0, "photos": p.ids, "slots": [], "mode": "manual"})
        else:
            pages.append(_full_bleed_page(next(free) if p.kind == "blank" else p.ids[0], cfg))
    pages += [_full_bleed_page(b, cfg) for b in free]
    for i, page in enumerate(pages):
        page["page"] = i
    data["layout"] = pages
    _gen_group_sort_key(data)
    uneven = refit_layout(data, FRAME_MM[plan.book.frame], full_page=book.own_page)
    if uneven:
        warnings.append(f"{uneven} page(s) would need more than {MAX_TRIM:.0%} of a photo trimmed to fill them: "
                        "they have a little extra space on two sides")
    save_state(project_dir, data)
    install_template(project_dir)  # books laid out with an older autobook get the current one
    from autobook.ai.focus import known_focus

    write_sidecar(project_dir, plan, gen, blanks, photos, known_focus(cat))
    out = fb.run(["build"], project_dir)
    # "N photo(s) but M slot(s)" is Fotobuch's normal state between `place` and `build`
    warnings += [ln.strip() for ln in out.splitlines() if "WARN" in ln and "slot(s)" not in ln]
    return inner_page_count(load_state(project_dir)), project_dir / f"{PROJECT}.pdf", res.blanks


def _check_generated(data, gen: Generated) -> None:
    known = {str(f["id"]) for g in data["photos"] for f in g["files"]}
    missing = [n for n in gen.files if f"{GEN_GROUP}/{n}" not in known]
    if missing:
        raise FotobuchError(f"fotobuch did not import generated pages: {', '.join(missing)}")


def _gen_group_sort_key(data) -> None:
    for g in data["photos"]:
        if g["group"] == GEN_GROUP:
            g["sort_key"] = GEN_SORT_KEY
        if g["group"] == COVER_GROUP:
            g["sort_key"] = COVER_SORT_KEY
    data["photos"].sort(key=lambda g: str(g["sort_key"]))


def _fresh(plan, work, profile, fb, project_dir, progress, guides: bool) -> ExportResult:
    work.project_parent.mkdir(parents=True, exist_ok=True)
    if work.staging.exists():
        shutil.rmtree(work.staging)
    cat = Catalog(work.catalog)
    warnings: list[str] = []
    try:
        progress("stage", 0, 1)
        photos, groups = _stage(plan, work)
        cover_cfg = cover_config(profile, plan)
        cover = stage_cover(plan, work, cat, cover_photo_px(cover_cfg, profile.dpi)) if profile.cover_enabled else {}
        progress("render", 0, 1)
        gen = render_generated(plan, work, profile, cat, groups)
        c = profile.cover
        args = ["project", "new", PROJECT, "--width", profile.page_width_mm, "--height", profile.page_height_mm,
                "--bleed", profile.bleed_mm, "--margin-mm", profile.margin_mm,
                "--parent-dir", work.project_parent, "--quiet"]
        use_cover = bool(cover)
        if use_cover:
            args += ["--with-cover", "--cover-width", cover_cfg["front_back_width_mm"],
                     "--cover-height", cover_cfg["height_mm"]]
            args += (["--spine-mm", cover_cfg["spine_width_mm"]] if cover_cfg["spine_mode"] == "fixed"
                     else ["--spine-grow-per-10-pages-mm", cover_cfg["spine_mm_per_10_pages"]])
        progress("fotobuch", 0, 4)
        fb.run(args, work.project_parent)
        install_template(project_dir)
        write_sidecar(project_dir, plan, gen)  # minimal sidecar so previews compile
        settings: dict[str, object] = {"book.gap_mm": profile.gap_mm, "book.dpi": profile.dpi,
                                       "preview.show_slot_info": False, **_guide_settings(guides)}
        if use_cover:
            settings["book.cover.mode"] = c.get("mode", "front-full")
        settings.update(solver_settings(plan, profile))
        for k, v in settings.items():
            fb.run(["config", "set", k, str(v).lower() if isinstance(v, bool) else str(v)], project_dir)
        add_dirs = ([work.staging / COVER_GROUP] if use_cover else []) + \
                   [work.staging / groups[ch.id] for ch in plan.chapters if ch.photos]
        fb.run(["add", "--allow-duplicates", *add_dirs], project_dir)
        data = load_state(project_dir)
        keys = _sort_keys(plan, groups)
        if use_cover:
            keys[COVER_GROUP] = COVER_SORT_KEY
            data["config"]["book"]["cover"].update(cover_cfg)
        apply_order_and_weights(data, keys, {v["fid"]: v["weight"] for v in photos.values()})
        data["config"]["book"]["title"] = str(plan.book.title)  # spine text; YAML keeps it a string
        save_state(project_dir, data)
        progress("fotobuch", 1, 4)
        fb.run(["build"], project_dir)  # first build: solver lays out all chapters
        _stage_generated(gen, work)
        fb.run(["add", work.staging / GEN_GROUP], project_dir)
        data = load_state(project_dir)
        _gen_group_sort_key(data)
        save_state(project_dir, data)
        progress("fotobuch", 2, 4)
        pages, pdf, blank = _finish(plan, work, profile, fb, project_dir, gen, cat, warnings, photos)
        progress("fotobuch", 4, 4)
        manifest = {"project_dir": str(project_dir), "profile": profile.name, "fotobuch": fb.version(),
                    "chapters": [{"id": ch.id, "group": groups[ch.id]} for ch in plan.chapters],
                    "photos": photos, "cover": cover, "project_sha": project_sha(project_dir)}
        work.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return ExportResult(project_dir, pdf, pages, warnings, "fresh", blank)
    finally:
        cat.close()


def _update(plan, work, profile, fb, project_dir, progress, guides: bool | None = None) -> ExportResult:
    """Apply plan changes to an existing project, keeping manual edits made in Fotobuch."""
    if not work.manifest.exists():
        raise ExportConflict("project exists but export_manifest.json is missing; use --fresh")
    old = json.loads(work.manifest.read_text(encoding="utf-8"))
    if [c["id"] for c in old["chapters"]] != [c.id for c in plan.chapters]:
        raise ExportConflict("the chapter structure changed; re-export with --fresh "
                             "(manual layout edits in Fotobuch will be lost)")
    if old.get("profile") != profile.name and not _same_pages(old.get("profile", ""), profile):
        raise ExportConflict(f"profile changed ({old.get('profile')} -> {profile.name}); use --fresh")
    cover_changed = old.get("cover", {}).get("plan_id") != plan.book.cover_photo
    if cover_changed and not (old.get("cover") and plan.book.cover_photo):
        raise ExportConflict("the book gained or lost its cover photo; use --fresh")
    new_ids = {p.id for p in plan.all_photos() if p.layout != "spread"}
    changes = len(new_ids ^ set(old["photos"]))
    untouched = old.get("project_sha") == project_sha(project_dir)
    if untouched and changes > max(BIG_CHANGE_MIN, BIG_CHANGE_SHARE * len(new_ids)):
        # nothing to keep and a lot to change: a new layout is faster and better balanced
        if guides is None:
            guides = bool(load_state(project_dir)["config"].get("preview", {}).get("show_borders", False))
        remove_project(project_dir)
        r = _fresh(plan, work, profile, fb, project_dir, progress, guides)
        r.warnings.insert(0, f"{changes} photos changed and the layout had no manual edits: laid out from scratch")
        return r
    groups = {c["id"]: c["group"] for c in old["chapters"]}
    data = load_state(project_dir)
    to_auto(data)  # our evenly filled pages are Manual pages; Fotobuch may only place photos on Auto ones
    cover_cfg = cover_config(profile, plan)
    book_cfg = data["config"]["book"]
    book_cfg.update(gap_mm=profile.gap_mm, dpi=profile.dpi)  # the same pages may come from another printer's profile
    if (book_cfg.get("cover") or {}).get("active"):
        book_cfg["cover"].update(cover_cfg)  # `build` lays the cover out again when its size changed
    save_state(project_dir, data)
    old["profile"] = profile.name
    cat = Catalog(work.catalog)
    warnings: list[str] = []
    if old.get("cover"):  # same file name, new content: Fotobuch re-reads it and keeps the cover layout
        staged = work.staging / old["cover"]["fid"]
        before = staged.read_bytes() if staged.exists() else None
        stage_cover(plan, work, cat, cover_photo_px(cover_cfg, profile.dpi), staged.name)
        if staged.read_bytes() != before:
            fb.run(["add", "--update", work.staging / COVER_GROUP], project_dir)
        if cover_changed:
            old["cover"]["plan_id"] = plan.book.cover_photo
            warnings.append("cover photo replaced")
    try:
        old_photos: dict[str, dict] = old["photos"]
        # a photo moved to another chapter leaves its old chapter's group and comes back in the new one
        chapter_of = {p.id: ch.id for ch in plan.chapters for p in ch.photos}
        stays = {k for k, v in old_photos.items() if k in new_ids and v.get("chapter") == chapter_of.get(k)}
        removed = [v for k, v in old_photos.items() if k not in stays]
        for rx in any_regex([source_regex(v["group"], v["file"]) for v in removed]):
            fb.run(["remove", rx], project_dir)
        photos = {k: v for k, v in old_photos.items() if k in stays}
        added: list[tuple[PlanPhoto, str]] = []
        for ch in plan.chapters:
            g = groups[ch.id]
            used = {p.name.lower() for p in (work.staging / g).iterdir()} if (work.staging / g).exists() else set()
            for p in ch.photos:
                if p.layout == "spread":
                    continue
                if p.id in photos:
                    photos[p.id]["weight"] = p.weight
                    continue
                name = staged_name(p, used)
                link_or_copy(Path(p.path), work.staging / g / name)
                photos[p.id] = {"fid": f"{g}/{name}", "group": g, "file": name, "chapter": ch.id,
                                "weight": p.weight}
                added.append((p, g))
        for g in sorted({g for _, g in added}):
            fb.run(["add", work.staging / g], project_dir)
        data = load_state(project_dir)
        apply_order_and_weights(data, {}, {v["fid"]: v["weight"] for v in photos.values()})
        _gen_group_sort_key(data)
        save_state(project_dir, data)
        # place each new photo onto the nearest page of its own chapter (placing into a page
        # keeps the page numbers, so one call per target page is enough)
        data = load_state(project_dir)
        by_target: dict[int | None, list[str]] = {}
        for p, g in added:
            by_target.setdefault(_nearest_page(data, g, p.ts), []).append(source_regex(g, photos[p.id]["file"]))
        for target, rxs in by_target.items():
            into = [] if target is None else ["--into", target]
            for rx in any_regex(rxs):
                fb.run(["place", "--filter", rx, *into], project_dir)
        if guides is not None:
            for k, v in _guide_settings(guides).items():
                fb.run(["config", "set", k, str(v).lower()], project_dir)
        gen = render_generated(plan, work, profile, cat, groups)
        _stage_generated(gen, work)
        fb.run(["add", "--update", work.staging / GEN_GROUP], project_dir)
        pages, pdf, blank = _finish(plan, work, profile, fb, project_dir, gen, cat, warnings, photos)
        old["photos"] = photos
        old["project_sha"] = project_sha(project_dir)
        work.manifest.write_text(json.dumps(old, indent=2, ensure_ascii=False), encoding="utf-8")
        warnings.insert(0, f"update: {len(added)} added, {len(removed)} removed")
        return ExportResult(project_dir, pdf, pages, warnings, "update", blank)
    finally:
        cat.close()


def _nearest_page(data, group: str, ts: str) -> int | None:
    stamps = {f["id"]: str(f["timestamp"]) for g in data["photos"] for f in g["files"]}
    best, best_d = None, None
    for i, page in enumerate(data["layout"]):
        ids = [str(x) for x in page["photos"]]
        if page.get("mode") == "manual" or not any(x.startswith(group + "/") for x in ids):
            continue
        for x in ids:
            d = abs(_secs(stamps.get(x, ts)) - _secs(ts))
            if best_d is None or d < best_d:
                best, best_d = i, d
    return best


def _secs(ts: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(ts.replace("Z", "+00:00")[:19]).timestamp()


def fix_dividers(work: Work, fb: Fotobuch | None = None) -> list[str]:
    """Put the title page, chapter pages and panoramas back in place after a manual
    `fotobuch rebuild --all`: an update lays out the pages again and keeps the rest."""
    warnings = export(BookPlan.load(work.plan), work, fb=fb).warnings
    manifest = json.loads(work.manifest.read_text(encoding="utf-8"))
    manifest["project_sha"] = None  # edited in Fotobuch: the next big change patches instead of starting over
    work.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return warnings

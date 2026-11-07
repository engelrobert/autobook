"""More or fewer photos in a finished selection, without a new AI run.

The reserve of a chapter is every photo of its days that could still go in: not blurry, no
screenshot, not a moment the chapter already shows, not one you took out yourself; favourites
first, then by the score from the selection run (AI scores from the catalog's cache).
Growing takes the reserve from the top; shrinking takes out the weakest photos, but never a
favourite, never a photo you put in yourself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from autobook.brief.models import Brief
from autobook.catalog import Catalog
from autobook.discover.favourites import level as fav_level
from autobook.plan.models import BookPlan, PlanPhoto, Scores
from autobook.select.scoring import final_score
from autobook.settings import Work

SKIP_REASONS = ("screenshot", "excluded_event")


@dataclass
class Candidate:
    id: str
    score: float
    fav: int
    row: dict  # catalog row


def reserve(work: Work, plan: BookPlan, brief: Brief | None = None) -> dict[str, list[Candidate]]:
    """Chapter id -> photos that could still go into it, best first (one per moment)."""
    cat = Catalog(work.catalog)
    try:
        rows = {p["id"]: p for p in cat.joined()}
        blur = set(cat.kv_get("blur", []))
        roots = cat.kv_get("roots", [])
        ai = cat.ai_latest("score")
    finally:
        cat.close()
    markers = brief.favourite_markers if brief and brief.favourites_mode != "ignore" else []
    priorities = brief.priorities if brief else []
    skip = {r.id for r in plan.rejected if r.reason in SKIP_REASONS}
    shown = {p.id for p in plan.all_photos()} | {c.divider.photo for c in plan.chapters} | {plan.book.cover_photo}
    out: dict[str, list[Candidate]] = {}
    for ch in plan.chapters:
        events = set(ch.event_ids)
        moments = {rows[i]["dup_group"] for i in [*(p.id for p in ch.photos), ch.divider.photo] if i in rows}
        excluded = set(ch.excluded)
        best: dict[str, Candidate] = {}
        for pid, row in rows.items():
            if pid in shown or pid in skip or pid in excluded or not row.get("phash"):
                continue
            if events and row["event_id"] not in events:
                continue
            if not events and not ch.date_from <= str(row["taken"])[:10] <= ch.date_to:
                continue
            a = ai.get(pid)
            flags = set((a or {}).get("flags", []))
            blurry = "blurry" in flags if a is not None else pid in blur
            if blurry or "screenshot" in flags or "document" in flags or row["dup_group"] in moments:
                continue
            c = Candidate(pid, final_score(row.get("local_score") or 0.0, a, priorities),
                          fav_level(row["path"], roots, markers), row)
            group = row["dup_group"] or pid
            if group not in best or (c.fav, c.score) > (best[group].fav, best[group].score):
                best[group] = c
        out[ch.id] = sorted(best.values(), key=lambda c: (-c.fav, -c.score, c.row["taken"]))
    return out


def _plan_photo(c: Candidate) -> PlanPhoto:
    r = c.row
    return PlanPhoto(id=c.id, path=r["path"], ts=r["taken"], ts_source=r.get("ts_source") or "exif",
                     gps=(r["lat"], r["lon"]) if r.get("lat") is not None else None,
                     width=r["width"], height=r["height"], dup_group=r.get("dup_group"),
                     scores=Scores(local=r.get("local_score"), final=round(c.score, 3)))


def _shares(counts: dict[str, int], target: int, room: dict[str, int]) -> dict[str, int]:
    """Spread a new total over the chapters in proportion to their current size (largest
    remainder), at least 1 each, never more than a chapter can hold (room)."""
    total = sum(counts.values()) or 1
    raw = {k: max(1.0, target * v / total) for k, v in counts.items()}
    want = {k: min(room[k], math.floor(v)) for k, v in raw.items()}
    left = target - sum(want.values())
    for k in sorted(raw, key=lambda k: raw[k] - math.floor(raw[k]), reverse=True):
        if left <= 0:
            break
        if want[k] < room[k]:
            want[k] += 1
            left -= 1
    for k in sorted(counts, key=lambda k: room[k] - want[k], reverse=True):  # what is left: where there is room
        while left > 0 and want[k] < room[k]:
            want[k] += 1
            left -= 1
    return want


def resize(plan: BookPlan, pool: dict[str, list[Candidate]], favourites: set[str], *,
           target: int | None = None, chapter: str | None = None, delta: int = 0
           ) -> tuple[BookPlan, list[str], list[str]]:
    """A copy of the plan with more or fewer photos: the whole book to `target`, or one chapter
    by `delta`. Returns (plan, added ids, removed ids); the plan given is left unchanged."""
    new = plan.model_copy(deep=True)
    counts = {c.id: len(c.photos) for c in new.chapters}
    fixed = {c.id: sum(1 for p in c.photos if p.by_user or p.id in favourites) for c in new.chapters}
    room = {c.id: len(c.photos) + len(pool.get(c.id, [])) for c in new.chapters}
    if chapter is not None:
        want = {**counts, chapter: max(max(1, fixed[chapter]), min(room[chapter], counts[chapter] + delta))}
    else:
        target = max(sum(max(1, f) for f in fixed.values()), min(sum(room.values()), int(target or 0)))
        want = _shares(counts, target, room)
    added, removed = [], []
    for c in new.chapters:
        w = max(want[c.id], fixed[c.id], 1)
        if w > len(c.photos):
            take = pool.get(c.id, [])[:w - len(c.photos)]
            c.photos += [_plan_photo(x) for x in take]
            c.photos.sort(key=lambda p: p.ts)
            c.alternatives = [a for a in c.alternatives if a not in {x.id for x in take}]
            added += [x.id for x in take]
        elif w < len(c.photos):
            weakest = sorted((p for p in c.photos if not p.by_user and p.id not in favourites),
                             key=lambda p: p.scores.final)[:len(c.photos) - w]
            out = {p.id for p in weakest}
            c.photos = [p for p in c.photos if p.id not in out]
            c.alternatives = [p.id for p in weakest] + [a for a in c.alternatives if a not in out]
            removed += [p.id for p in weakest]
    return new, added, removed


def favourite_ids(work: Work, plan: BookPlan, brief: Brief | None) -> set[str]:
    """Photos in the book that carry your favourite marks and are must-haves (the slider never
    takes them out). In "best" mode the favourites are only the candidates: none is fixed."""
    if not brief or brief.favourites_mode not in ("fill", "only") or not brief.favourite_markers:
        return set()
    cat = Catalog(work.catalog)
    try:
        roots = cat.kv_get("roots", [])
        rows = {p["id"]: p for p in cat.joined()}
    finally:
        cat.close()
    return {p.id for p in plan.all_photos() if p.id in rows
            and fav_level(rows[p.id]["path"], roots, brief.favourite_markers) > 0}


def estimate_pages(counts: list[int], per_page: float, min_pages: int = 0, step: int = 1) -> int:
    """About how many pages: the title page, one chapter page each and the photo pages; at least
    the printer's minimum (a thin book spreads its photos further), on the printer's page step."""
    n = max(min_pages, 1 + len(counts) + sum(math.ceil(c / max(1.0, per_page)) for c in counts if c))
    return n + (-n) % max(1, step)


def fit_points(plan: BookPlan, capacity: int, min_pages: int, step: int, max_pages: int) -> list[dict]:
    """Photo counts at which the estimate fills whole pages and lands on a page count the
    printer takes: the printer's minimum, then every page step after it."""
    per = max(1.0, plan.book.photos_per_page)
    fixed = 1 + len(plan.chapters)  # title page and chapter pages
    out = []
    pages = max(min_pages, fixed + len(plan.chapters))
    pages += (-pages) % max(1, step)
    while not max_pages or pages <= max_pages:
        n = round((pages - fixed) * per)
        if n > capacity:
            break
        if n >= len(plan.chapters):
            out.append({"photos": n, "pages": pages})
        pages += max(1, step)
    return out

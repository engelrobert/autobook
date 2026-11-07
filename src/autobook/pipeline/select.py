"""`autobook select`: catalog + brief (+ optional AI) -> book_plan.json."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime

from autobook.brief.models import Brief
from autobook.catalog import Catalog
from autobook.cluster.chapters import make_chapters
from autobook.cluster.folders import (
    chapter_folder,
    clean_label,
    dominant_label,
    folder_hints,
    folder_order,
)
from autobook.cluster.places import country_name
from autobook.discover.favourites import level as fav_level
from autobook.export.profile import load_profile
from autobook.i18n import MONTHS, format_range, lang_of
from autobook.plan.models import (
    BookMeta,
    BookPlan,
    Chapter,
    Divider,
    PlanPhoto,
    Rejected,
    Scores,
)
from autobook.select.allocate import allocate
from autobook.select.scoring import final_score, is_people, split_quota
from autobook.select.selector import pick, weights_for
from autobook.settings import Work

THEMES = {
    "people": "people", "group": "people", "portrait": "people", "kids": "people",
    "landscape": "landscape", "nature": "landscape", "water": "landscape",
    "city": "city", "architecture": "city", "transport": "city", "sign": "city",
    "food": "food", "animals": "animals", "night": "night", "activity": "activity", "detail": "details",
}


HERO_MIN_DPI = 240  # a chapter opener is cropped to the full page; below this it looks soft
HERO_MAX_CROP = 0.35  # never cut away more than this share (a portrait selfie on a landscape page)
STRONG_FAV_SHARE = 0.2  # at most this share of a chapter is shown big because it is marked as the best
BEST_LEVEL_BONUS = 0.05  # "best of the favourites": score bonus per favourite level above 1


def _noop(*_):
    pass


def hero_fits(p: dict, page_w: float, page_h: float) -> bool:
    """Can this photo fill a full page (incl. bleed, in mm) sharply and without a drastic crop?"""
    w, h = p.get("width") or 0, p.get("height") or 0
    if not w or not h:
        return False
    page_aspect, aspect = page_w / page_h, w / h
    crop = 1 - min(aspect / page_aspect, page_aspect / aspect)
    used_w = min(w, h * page_aspect)  # pixel width left after cropping to the page shape
    return crop <= HERO_MAX_CROP and used_w / (page_w / 25.4) >= HERO_MIN_DPI


def pick_mixed(cands: list[dict], k: int, k_place: int | None) -> tuple[list[dict], list[dict]]:
    """Offline/fallback pick with k_place photos of places (AI tag says: no people as the subject).
    Photos without AI tags are not counted as places. k_place None = no mix target."""
    if k_place is None or not any(is_people(p) is False for p in cands):
        return pick(cands, k)
    places = [p for p in cands if is_people(p) is False]
    people = [p for p in cands if is_people(p) is not False]
    kl = min(k_place, len({p["dup_group"] for p in places}))
    sel_p, _ = pick(people, k - kl)
    sel_l, _ = pick(places, k - len(sel_p))  # places fill up when there are too few people photos
    sel = sorted([*sel_p, *sel_l], key=lambda p: (p["taken"], p["id"]))
    ids = {p["id"] for p in sel}
    return sel, sorted((p for p in cands if p["id"] not in ids), key=lambda p: -p["final"])


def merge_similar(photos: list[dict]) -> int:
    """Join duplicate groups the AI marked as nearly the same picture (`similar_to`).

    Perceptual hashes miss these (a moving toddler, a slightly different angle);
    the scoring call sees the photos side by side. Returns the number of merges.
    """
    parent: dict[str, str] = {}

    def find(g: str) -> str:
        while parent.get(g, g) != g:
            g = parent[g]
        return g

    by_id = {p["id"]: p for p in photos}
    merges = 0
    for p in photos:
        other = by_id.get(((p.get("ai") or {}).get("similar_to")) or "")
        if other is None:
            continue
        a, b = find(p["dup_group"] or p["id"]), find(other["dup_group"] or other["id"])
        if a != b:
            parent[max(a, b)] = min(a, b)
            merges += 1
    for p in photos:
        p["dup_group"] = find(p["dup_group"] or p["id"])
    return merges


def _refill(ch: Chapter, removed: dict, by_id: dict[str, dict], strong: set[str] | None) -> None:
    """Take the best fitting alternative into the chapter after `removed` left its pages."""
    groups = {by_id[p.id]["dup_group"] for p in ch.photos if p.id in by_id}
    alt = next((by_id[a] for a in ch.alternatives if a in by_id and not by_id[a]["blur"]
                and by_id[a]["dup_group"] != removed["dup_group"] and by_id[a]["dup_group"] not in groups), None)
    if alt is None:
        return
    ch.alternatives = [a for a in ch.alternatives if a != alt["id"]]
    ch.photos = sorted([*ch.photos, _plan_photo(alt, 1.0, strong)], key=lambda p: p.ts)


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def theme_chapters(photos: list[dict], min_photos: int = 8) -> list[dict]:
    """Chapters by the photos' primary AI tag; small themes are pooled into 'moments'."""
    groups: dict[str, list[dict]] = {}
    for p in photos:
        tags = (p.get("ai") or {}).get("tags") or []
        theme = THEMES.get(tags[0], "moments") if tags else "moments"
        groups.setdefault(theme, []).append(p)
    for t in [t for t, ps in groups.items() if len(ps) < min_photos and t != "moments"]:
        groups.setdefault("moments", []).extend(groups.pop(t))
    out = []
    for i, (theme, ps) in enumerate(sorted(groups.items(), key=lambda kv: min(p["taken"] for p in kv[1]))):
        ps.sort(key=lambda p: p["taken"])
        out.append({"id": f"ch_{i + 1:02d}", "theme": theme, "photo_ids": [p["id"] for p in ps],
                    "event_ids": list(dict.fromkeys(p["event_id"] for p in ps)),
                    "start": ps[0]["taken"], "end": ps[-1]["taken"], "n": len(ps),
                    "place": None, "country": None, "mode": "theme"})
    return out


def folder_chapters(photos: list[dict], roots: list[str]) -> list[dict]:
    """Chapters by the folders the photos were sorted into (the first level below the scan root),
    in folder_order and never merged: the folders are the person's own structure. Photos lying in
    the root itself join the folder chapter closest in time."""
    groups: dict[str, list[dict]] = {}
    loose = []
    for p in photos:
        f = chapter_folder(p["path"], roots)
        (groups.setdefault(f, []) if f else loose).append(p)
    if not groups:
        return []
    secs = lambda ts: datetime.fromisoformat(ts.replace("Z", "+00:00")[:19]).timestamp()
    spans = {f: (min(secs(p["taken"]) for p in ps), max(secs(p["taken"]) for p in ps)) for f, ps in groups.items()}
    for p in loose:
        t = secs(p["taken"])
        groups[min(spans, key=lambda f: max(spans[f][0] - t, t - spans[f][1], 0))].append(p)
    out = []
    for i, f in enumerate(folder_order({f: min(p["taken"] for p in ps) for f, ps in groups.items()})):
        ps = sorted(groups[f], key=lambda p: p["taken"])
        out.append({"id": f"ch_{i + 1:02d}", "folder": f, "label": clean_label(f) or f,
                    "photo_ids": [p["id"] for p in ps], "event_ids": list(dict.fromkeys(p["event_id"] for p in ps)),
                    "start": ps[0]["taken"], "end": ps[-1]["taken"], "n": len(ps),
                    "place": None, "country": None, "mode": "folder"})
    return out


def offline_book_title(chapters: list[dict], date_from: str, language: str) -> str:
    countries = [c["country"] for c in chapters if c.get("country")]
    year = date_from[:4]
    if countries:
        top = Counter(countries).most_common()
        if len(top) == 1 or top[0][1] / len(countries) > 0.8:
            return f"{country_name(top[0][0])} {year}"
        places = [c["place"] for c in chapters if c.get("place")]
        if places:
            return f"{places[0]} – {places[-1]}"
    return f"{MONTHS[lang_of(language)][int(date_from[5:7]) - 1].capitalize()} {year}"


def build_plan(work: Work, brief: Brief, *, ai=None, profile: str = "square-21",
               batch: bool | None = None, progress: Callable = _noop) -> BookPlan:
    cat = Catalog(work.catalog)
    try:
        return _build(cat, work, brief, ai, profile, batch, progress)
    finally:
        cat.close()


def _build(cat: Catalog, work: Work, brief: Brief, ai, profile: str, batch, progress) -> BookPlan:
    photos = [p for p in cat.joined() if p.get("phash")]
    if not photos:
        raise RuntimeError("no analysed photos in the catalog: run `autobook scan` first")
    events = {e["id"]: e for e in cat.events()}
    blur = set(cat.kv_get("blur", []))
    roots = cat.kv_get("roots", [])
    excluded = set(brief.excluded_event_ids)
    rejected = [Rejected(id=p["id"], reason="excluded_event") for p in photos if p["event_id"] in excluded]
    photos = [p for p in photos if p["event_id"] not in excluded]
    mode = brief.favourites_mode if brief.favourite_markers else "ignore"
    markers = brief.favourite_markers if mode != "ignore" else []
    for p in photos:
        p["blur"] = p["id"] in blur
        p["local"] = p.get("local_score") or 0.0
        p["fav"] = fav_level(p["path"], roots, markers)

    # 1. AI scoring (optional; not needed when the book is exactly the favourites; only the
    # favourites compete when the AI picks the best of them)
    ai_scores: dict[str, dict] = {}
    scored = ai is not None and mode != "only"
    if scored:
        from autobook.ai.score import score_photos

        progress("score", 0, 1)
        ai_scores = score_photos(ai, work, events, [p for p in photos if p["fav"] or mode != "best"],
                                 priorities=brief.priorities, batch=batch, progress=progress)
    for p in photos:
        p["ai"] = ai_scores.get(p["id"])
        flags = set((p["ai"] or {}).get("flags", []))
        if p["ai"] is not None:  # the model looked at the photo: its judgement replaces the blur detector
            p["blur"] = "blurry" in flags
        if "screenshot" in flags or "document" in flags:
            p["screenshot"] = 1
        p["final"] = final_score(p["local"], p["ai"], brief.priorities)
        if mode == "best" and p["fav"] > 1:  # "the best of the favourites": higher marks win close calls
            p["final"] = round(p["final"] + BEST_LEVEL_BONUS * (p["fav"] - 1), 4)
    merge_similar(photos)
    # who competes: with AI only photos it has judged (the rest are 3rd+ shots of a burst whose best
    # shots were judged; their offline score is on another scale) plus the favourites
    if mode in ("only", "best"):
        pool = [p for p in photos if p["fav"]]
    elif scored:
        pool = [p for p in photos if p["ai"] is not None or p["fav"]]
    else:
        pool = photos
    by_id = {p["id"]: p for p in photos}
    book_label = dominant_label([p["path"] for p in photos], roots)

    # 2. chapters
    ev_list = [e for e in events.values() if e["id"] not in excluded]
    by_folder = brief.chaptering == "folder" and bool(chapters := folder_chapters(pool, roots))
    if brief.chaptering == "theme" and ai_scores:
        chapters = theme_chapters(pool)
        ch_photos = {c["id"]: [by_id[i] for i in c["photo_ids"]] for c in chapters}
    elif by_folder:
        ch_photos = {c["id"]: [by_id[i] for i in c["photo_ids"]] for c in chapters}
    else:  # (no folders after all: chapters as "auto" would make them)
        chapters = make_chapters(ev_list, "auto" if brief.chaptering == "folder" else brief.chaptering,
                                 max_chapters=brief.max_chapters)
        ch_photos = {c["id"]: [p for p in pool if p["event_id"] in set(c["event_ids"])] for c in chapters}

    # 3. quotas: never fewer than the chapter's favourites (the must-haves; in "best" mode they are
    # the candidates instead)
    favs_of = {cid: [p for p in ps if p["fav"] and mode != "best"] for cid, ps in ch_photos.items()}
    if mode == "only":
        quotas = {cid: len(f) for cid, f in favs_of.items()}
    else:
        sizes = {cid: len({p["dup_group"] for p in ps if not p["blur"] and not p.get("screenshot")})
                 for cid, ps in ch_photos.items()}
        main = set(brief.main_event_ids)
        weights = ({c["id"]: (1.6 if main & set(c["event_ids"]) else 0.6) for c in chapters} if main else None)
        quotas = allocate(sizes, brief.target_photos, weights=weights,
                          exponent=0.5 if brief.scope == "highlights" else 0.85)
        quotas = {cid: max(q, len(favs_of[cid])) for cid, q in quotas.items()}

    # what each chapter still needs on top of its favourites (and how many of that should be places)
    fill: dict[str, dict] = {}
    for c in chapters:
        cid, favs, k = c["id"], favs_of[c["id"]], quotas[c["id"]]
        fav_groups = {p["dup_group"] for p in favs}
        cands = [p for p in ch_photos[cid] if p["dup_group"] not in fav_groups
                 and not p.get("screenshot")]
        k_fill = 0 if mode == "only" else max(0, min(k - len(favs), len({p["dup_group"] for p in cands})))
        k_place = None
        if brief.people_share is not None and mode != "only":
            _, kl = split_quota(k, brief.people_share)
            k_place = max(0, min(k_fill, kl - sum(1 for p in favs if is_people(p) is False)))
        fill[cid] = {"cands": cands, "k": k_fill, "k_place": k_place}

    # 4. selection per chapter: favourites + AI editor (or offline pick) for the rest
    ranked: dict[str, dict] = {}
    if scored:
        from autobook.ai.rank import rank_chapters

        jobs = [{"chapter": c, "candidates": fill[c["id"]]["cands"], "k": fill[c["id"]]["k"],
                 "fixed": favs_of[c["id"]], "k_place": fill[c["id"]]["k_place"]} for c in chapters]
        progress("rank", 0, 1)
        ranked = rank_chapters(ai, work, jobs, priorities=brief.priorities, batch=batch, progress=progress,
                               people_share=brief.people_share)

    prof = load_profile(profile)
    page_w, page_h = prof.page_width_mm + 2 * prof.bleed_mm, prof.page_height_mm + 2 * prof.bleed_mm
    folders_of: dict[str, list[str]] = {}
    plan_chapters: list[Chapter] = []
    hero_ids: list[str] = []
    selected_all: set[str] = set()
    for c in chapters:
        cid, favs = c["id"], favs_of[c["id"]]
        f = fill[cid]
        if quotas[cid] <= 0 or not ch_photos[cid]:
            continue
        if cid in ranked:
            r = ranked[cid]
            picked = [by_id[i] for i in r["selected"] if i in by_id]
            ai_hero = by_id.get(r["hero"])
            strong = set(r["strong"])
        else:
            picked, _ = pick_mixed(f["cands"], f["k"], f["k_place"])
            ai_hero, strong = None, None
        picked_ids = {p["id"] for p in picked}
        alts = sorted((p for p in f["cands"] if p["id"] not in picked_ids), key=lambda p: -p["final"])
        sel = one_per_group([*favs, *picked], alts, keep_favourites=mode != "best")
        strong = (strong or set()) | strong_favourites(sel)
        if not strong:
            strong = None  # offline: weights_for marks the top share by score
        # the opener fills a whole page: a favourite first (best level), then the AI's hero, then the best
        # fitting photo of the selection, then of the chapter; it must be sharp enough and fit the page.
        # In "best" mode every candidate is a favourite, so the AI's hero comes first.
        by_level = sorted(favs or [p for p in sel if p["fav"]], key=lambda p: (-p["fav"], -p["final"]))
        hero_first = [ai_hero] if ai_hero else []
        order = ((hero_first + by_level if mode == "best" else by_level + hero_first)
                 + sorted(sel, key=lambda p: -p["final"])
                 + sorted((p for p in ch_photos[cid] if not p["blur"] and not p.get("screenshot")),
                          key=lambda p: -p["final"]))
        hero = next((p for p in order if hero_fits(p, page_w, page_h)), None)
        style = "hero" if hero is not None else "plain"
        w = weights_for(sel, strong)
        if hero is not None and hero["id"] in w and len(sel) > 1:
            # the hero opens the chapter on its own page; outside "only" mode refill its slot
            sel = [p for p in sel if p["id"] != hero["id"]]
            w.pop(hero["id"], None)
            refill = None if mode == "only" else next(
                (p for p in alts if not p["blur"] and p["dup_group"] != hero["dup_group"]
                 and p["dup_group"] not in {s["dup_group"] for s in sel}), None)
            if refill is not None:
                sel = sorted([*sel, refill], key=lambda p: p["taken"])
                w[refill["id"]] = 1.0
                alts = [a for a in alts if a["id"] != refill["id"]]
        if hero is not None:
            hero_ids.append(hero["id"])
        selected_all.update(p["id"] for p in sel)
        plan_chapters.append(Chapter(
            id=cid,
            title=c.get("place") or format_range(_d(c["start"]), _d(c["end"]), brief.language),
            subtitle="",
            date_from=c["start"][:10], date_to=c["end"][:10],
            place=c.get("place"), country=c.get("country"), event_ids=c["event_ids"],
            divider=Divider(style=style, photo=hero["id"] if hero else None),
            photos=[_plan_photo(p, w.get(p["id"], 1.0), strong) for p in sel],
            alternatives=[a["id"] for a in alts[:max(6, f["k"])]],
        ))
        ch_label = dominant_label([p["path"] for p in ch_photos[cid]], roots)
        if ch_label and ch_label != book_label:  # one folder per chapter: the person already named it
            plan_chapters[-1].title = ch_label
        if c.get("theme"):
            plan_chapters[-1].title = c["theme"].capitalize()
        if c.get("label"):  # chapters by folder: the folder's name
            plan_chapters[-1].title = c["label"]
        folders_of[cid] = folder_hints([p["path"] for p in ch_photos[cid]], roots)
    if not plan_chapters:
        raise RuntimeError("nothing to put in the book (no favourites found for the chosen marks?)")

    # 5. titles
    date_from = min(c.date_from for c in plan_chapters)
    date_to = max(c.date_to for c in plan_chapters)
    if ai is not None:
        from autobook.ai.titles import book_title, chapter_titles

        progress("titles", 0, 1)
        ch_input = [{"id": c.id, "place": c.place, "start": c.date_from, "end": c.date_to,
                     "folders": folders_of.get(c.id, []),
                     "photo_ids": ([c.divider.photo] if c.divider.photo else [])
                     + [p.id for p in sorted(c.photos, key=lambda p: -p.scores.final)]}
                    for c in plan_chapters]
        titles = chapter_titles(ai, work, ch_input, language=brief.language, tone=brief.title_tone,
                                subtitles=brief.subtitles)
        for c in plan_chapters:
            t = titles.get(c.id)
            if t:  # chapters by folder keep the folder's name; the AI may still add a subtitle
                c.title, c.subtitle = (c.title if by_folder else t["title"] or c.title), t.get("subtitle", "")
    # cover: favourites first (best level), never a full-page chapter opener; taken off the inner pages
    # below so it is not shown twice. Candidates that fit the front panel come first.
    cover_cands = cover_candidates(selected_all or set(hero_ids), by_id, prof)
    title = brief.book_title or book_label or offline_book_title(chapters, date_from, brief.language)
    subtitle = ""
    cover = cover_cands[0] if cover_cands else None
    if ai is not None:
        bt = book_title(ai, work, [c.model_dump() for c in plan_chapters], cover_cands[:8],
                        language=brief.language, tone=brief.title_tone, date_from=date_from, date_to=date_to,
                        folders=folder_hints([p["path"] for p in photos], roots), subtitles=brief.subtitles)
        if bt:
            title = brief.book_title or bt["book_title"] or title
            subtitle = bt["subtitle"]
            cover = bt["cover_photo"] or cover
    for ch in plan_chapters:
        if cover and len(ch.photos) > 1 and any(p.id == cover for p in ch.photos):
            ch.photos = [p for p in ch.photos if p.id != cover]
            selected_all.discard(cover)
            if mode != "only":
                _refill(ch, by_id[cover], by_id, None)
            selected_all.update(p.id for p in ch.photos)

    # 6. rejections
    shown = selected_all | set(hero_ids) | ({cover} if cover else set())
    shown_groups = {by_id[i]["dup_group"] for i in shown if i in by_id}
    for p in photos:
        if p["id"] in shown:
            continue
        if p.get("screenshot"):
            reason = "screenshot"
        elif p["blur"]:
            reason = "blur"
        elif p["dup_group"] in shown_groups:
            reason = f"near_dup:{p['dup_group']}"
        else:
            reason = "not_selected"
        rejected.append(Rejected(id=p["id"], reason=reason))

    plan = BookPlan(
        selection="ai" if ai is not None else "local",
        book=BookMeta(title=title, subtitle=subtitle, date_from=date_from, date_to=date_to,
                      language=brief.language, cover_photo=cover, cover_candidates=cover_cands[:6],
                      profile=profile, photos_per_page=brief.photos_per_page),
        chapters=plan_chapters, rejected=rejected)
    if ai is not None:  # where the faces are, so trimming keeps them (cheap; a failure only costs that)
        from autobook.ai.focus import book_photo_ids, find_focus

        try:
            find_focus(ai, work, book_photo_ids(plan), batch=batch)
        except Exception as e:
            plan.warnings.append(f"focus points: {e}")
        plan.warnings[:0] = ai.error_summary()
    return plan


def one_per_group(sel: list[dict], alts: list[dict], keep_favourites: bool = True) -> list[dict]:
    """One photo per moment (duplicate group); favourites always stay, even two of the same moment
    (unless keep_favourites is off: then the AI picks among them like among any photos).
    Dropped slots are refilled from the alternatives. Returns the selection in time order."""
    keep, groups = [], set()
    for p in sorted(sel, key=lambda p: (-p["fav"], -p["final"])):
        if (p["fav"] and keep_favourites) or p["dup_group"] not in groups:
            keep.append(p)
            groups.add(p["dup_group"])
    missing = len(sel) - len(keep)
    for p in alts:
        if missing <= 0:
            break
        if not p["blur"] and p["dup_group"] not in groups:
            keep.append(p)
            groups.add(p["dup_group"])
            missing -= 1
    return sorted(keep, key=lambda p: (p["taken"], p["id"]))


def strong_favourites(sel: list[dict], share: float = STRONG_FAV_SHARE) -> set[str]:
    """Favourites marked as the best (level 2+) get a bigger place on the page; if many are, only
    the highest-marked (then best-scored) up to `share` of the chapter, or the pages stop working."""
    best = sorted((p for p in sel if p["fav"] >= 2), key=lambda p: (-p["fav"], -p["final"]))
    return {p["id"] for p in best[:max(1, round(share * len(sel)))]}


def cover_candidates(ids: set[str], by_id: dict[str, dict], prof) -> list[str]:
    """Favourites first (best level), then by score; photos that fit the front panel before others."""
    cover = prof.cover or {}
    panel_w = (cover.get("front_back_width_mm") or 2 * prof.page_width_mm) / 2
    panel_h = cover.get("height_mm") or prof.page_height_mm
    ranked = sorted(ids, key=lambda i: (-by_id[i]["fav"], -by_id[i]["final"]))
    fits = [i for i in ranked if hero_fits(by_id[i], panel_w, panel_h)]
    return fits + [i for i in ranked if i not in fits]


def _plan_photo(p: dict, weight: float, strong: set[str] | None) -> PlanPhoto:
    a = p.get("ai") or {}
    return PlanPhoto(
        id=p["id"], path=p["path"], ts=p["taken"], ts_source=p["ts_source"],
        gps=(p["lat"], p["lon"]) if p.get("lat") is not None else None,
        width=p["width"], height=p["height"], weight=weight,
        strong=weight > 1.0 if strong is None else p["id"] in strong,
        scores=Scores(local=round(p["local"], 3), tech=a.get("tech"), aesthetic=a.get("aesthetic"),
                      interest=a.get("interest"), final=round(p["final"], 3)),
        reason=a.get("reason", ""), dup_group=p.get("dup_group"))

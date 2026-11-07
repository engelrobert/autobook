"""More or fewer photos in a finished selection, without a new AI run (pipeline/resize.py)."""

from __future__ import annotations

from autobook.brief.models import Brief
from autobook.catalog import Catalog
from autobook.pipeline.resize import estimate_pages, fit_points, reserve, resize
from autobook.pipeline.select import build_plan


def _plan(work, n=20):
    return build_plan(work, Brief(target_photos=n), profile="square-21")


def test_the_reserve_holds_what_could_still_go_in(work_copy):
    plan = _plan(work_copy)
    pool = reserve(work_copy, plan)
    shown = {p.id for p in plan.all_photos()} | {c.divider.photo for c in plan.chapters} | {plan.book.cover_photo}
    cat = Catalog(work_copy.catalog)
    try:
        rows = {p["id"]: p for p in cat.joined()}
        blur = set(cat.kv_get("blur", []))
    finally:
        cat.close()
    for ch in plan.chapters:
        moments = {rows[p.id]["dup_group"] for p in ch.photos}
        ids = [c.id for c in pool[ch.id]]
        assert ids and not set(ids) & shown and not set(ids) & blur
        assert all(rows[i]["event_id"] in ch.event_ids for i in ids)
        assert not {rows[i]["dup_group"] for i in ids} & moments  # no second shot of a moment already shown
        assert len({rows[i]["dup_group"] for i in ids}) == len(ids)  # one per moment
        scores = [c.score for c in pool[ch.id]]
        assert scores == sorted(scores, reverse=True)
    # a photo you took out stays out
    ch = plan.chapters[0]
    gone = pool[ch.id][0].id
    ch.excluded = [gone]
    assert gone not in {c.id for c in reserve(work_copy, plan)[ch.id]}


def test_resize_reaches_the_target_and_keeps_your_choices(work_copy):
    plan = _plan(work_copy)
    n = sum(len(c.photos) for c in plan.chapters)
    mine = plan.chapters[0].photos[0]
    mine.by_user = True
    mine.scores.final = 0.0  # the weakest of all, but put in by hand
    before = plan.model_dump()
    pool = reserve(work_copy, plan)
    bigger, added, removed = resize(plan, pool, set(), target=n + 7)
    assert sum(len(c.photos) for c in bigger.chapters) == n + 7 and len(added) == 7 and not removed
    assert plan.model_dump() == before  # nothing changed in the plan given (nothing saved either)
    for c in bigger.chapters:
        assert [p.ts for p in c.photos] == sorted(p.ts for p in c.photos)
    smaller, added, removed = resize(plan, pool, set(), target=n - 6)
    assert sum(len(c.photos) for c in smaller.chapters) == n - 6 and len(removed) == 6 and not added
    assert mine.id not in removed and all(len(c.photos) >= 1 for c in smaller.chapters)
    fav = plan.chapters[1].photos[0].id
    _, _, removed = resize(plan, pool, {fav}, target=len(plan.chapters))
    assert fav not in removed and mine.id not in removed


def test_one_chapter_more_or_less(work_copy):
    plan = _plan(work_copy)
    pool = reserve(work_copy, plan)
    ch = plan.chapters[0]
    more, added, _ = resize(plan, pool, set(), chapter=ch.id, delta=2)
    assert len(more.chapters[0].photos) == len(ch.photos) + 2 and added == [c.id for c in pool[ch.id][:2]]
    assert [len(c.photos) for c in more.chapters[1:]] == [len(c.photos) for c in plan.chapters[1:]]
    fewer, _, removed = resize(plan, pool, set(), chapter=ch.id, delta=-1)
    assert len(fewer.chapters[0].photos) == len(ch.photos) - 1 and removed[0] in fewer.chapters[0].alternatives


def test_fit_points_fill_whole_pages_the_printer_takes():
    from autobook.plan.models import BookMeta, BookPlan, Chapter

    plan = BookPlan(book=BookMeta(title="t", date_from="2026-01-01", date_to="2026-01-02", photos_per_page=4.5),
                    chapters=[Chapter(id=f"c{i}", title="c", date_from="2026-01-01", date_to="2026-01-01")
                              for i in range(3)])
    points = fit_points(plan, capacity=400, min_pages=24, step=2, max_pages=150)
    assert points[0]["pages"] == 24 and all(p["pages"] % 2 == 0 for p in points)
    assert all(b["photos"] > a["photos"] for a, b in zip(points, points[1:]))
    assert points[-1]["photos"] <= 400
    assert estimate_pages([9, 9, 9], 4.5) == 1 + 3 + 6
    assert estimate_pages([9, 9, 9], 4.5, min_pages=24, step=2) == 24 and estimate_pages([9], 4.5, step=2) == 4

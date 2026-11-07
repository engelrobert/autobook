import json
from collections import Counter

from autobook.brief.models import Brief
from autobook.brief.questions import (
    apply_answers,
    default_questions,
    validate_ai_questions,
    wizard,
)
from autobook.catalog import Catalog
from autobook.pipeline.select import build_plan
from autobook.plan.models import BookPlan, export_schemas


def test_scan_summary(scanned, trip):
    s = json.loads(scanned.summary.read_text())
    assert s["photos"] == len(trip[1])
    assert len(s["events"]) >= 3
    assert {"Lisbon", "Porto", "Lagos"} <= {e["place"] for e in s["events"]}
    assert s["duplicates_collapsed"] > 0 and s["blurry"] > 0
    cat = Catalog(scanned.catalog)
    assert sum(1 for p in cat.joined() if p["ts_source"] == "mtime") >= 1  # the no-EXIF photo
    assert any(p["format"] in ("HEIF", "HEIC") for p in cat.joined())


def test_rescan_reuses_hashes(scanned, trip):
    from autobook.pipeline.scan import run_scan

    before = {p["id"] for p in Catalog(scanned.catalog).joined()}
    run_scan([trip[0]], scanned)
    assert {p["id"] for p in Catalog(scanned.catalog).joined()} == before


def test_offline_plan(work_copy):
    plan = build_plan(work_copy, Brief(target_photos=30, chaptering="place"))
    assert plan.selection == "local"
    heroes = sum(1 for c in plan.chapters if c.divider.style == "hero" and c.divider.photo)
    assert 26 <= plan.photo_count() <= 30  # the quota is for photo pages
    assert plan.photo_count() + heroes <= 30 + len(plan.chapters)  # each hero opener adds one full page
    assert [c.date_from for c in plan.chapters] == sorted(c.date_from for c in plan.chapters)
    ids = [p.id for p in plan.all_photos()]
    assert len(ids) == len(set(ids))
    groups = Counter(p.dup_group for p in plan.all_photos())
    assert max(groups.values()) == 1  # one shot per burst
    reasons = Counter(r.reason.split(":")[0] for r in plan.rejected)
    assert reasons["blur"] > 0 and reasons["not_selected"] > 0
    assert plan.book.title == "Portugal Rundreise"  # from the folder name, date prefix removed
    assert plan.book.cover_photo not in {p.id for p in plan.all_photos()}  # cover is not repeated inside
    assert plan.book.cover_photo not in {c.divider.photo for c in plan.chapters}
    # the synthetic photos are only 1200 px: too soft for a full page, so the openers are plain pages
    assert all(c.divider.style == "plain" and c.divider.photo is None for c in plan.chapters)
    assert all(c.subtitle == "" for c in plan.chapters)  # subtitles are off by default


def test_hero_style_moves_hero_out_of_pages(work_copy, monkeypatch):
    import autobook.pipeline.select as select

    monkeypatch.setattr(select, "HERO_MIN_DPI", 100)  # let the small synthetic photos count as sharp enough
    plan = build_plan(work_copy, Brief(target_photos=24))
    for c in plan.chapters:
        assert c.divider.style == "hero" and c.divider.photo
        assert c.divider.photo not in {p.id for p in c.photos}


def test_main_events_get_more_photos(work_copy):
    events = [e["id"] for e in Catalog(work_copy.catalog).events()]
    base = build_plan(work_copy, Brief(target_photos=30, chaptering="event"))
    boosted = build_plan(work_copy, Brief(target_photos=30, chaptering="event", main_event_ids=[events[-1]]))
    last = lambda p: len(p.chapters[-1].photos)  # noqa: E731
    assert last(boosted) > last(base)


def test_plan_roundtrip_and_history(work_copy, tmp_path):
    plan = build_plan(work_copy, Brief(target_photos=20))
    plan.save(work_copy.plan, work_copy.plan_history)
    plan.chapters[0].title = "Changed"
    plan.save(work_copy.plan, work_copy.plan_history)
    assert BookPlan.load(work_copy.plan).chapters[0].title == "Changed"
    assert len(list(work_copy.plan_history.glob("plan-*.json"))) == 1
    export_schemas(tmp_path / "schemas")
    schema = json.loads((tmp_path / "schemas" / "book_plan.schema.json").read_text())
    assert "chapters" in schema["properties"]


def test_default_questions_skip_what_data_answers():
    summary = {"photos": 50, "gps_share": 0.0, "events": [
        {"id": "ev_01", "start": "2025-01-01T10:00", "end": "2025-01-01T12:00", "n": 50, "unique": 45, "place": None}]}
    qs = default_questions(summary)
    fields = [q.maps_to for q in qs]
    assert "main_event_ids" not in fields and "focus" in fields
    assert 3 <= len(qs) <= 6
    chapter_q = next(q for q in qs if q.maps_to == "chaptering")
    assert "place" not in [o.value for o in chapter_q.options]


def test_apply_answers_and_wizard():
    summary = {"photos": 400, "gps_share": 0.9, "events": [
        {"id": f"ev_{i:02d}", "start": f"2025-0{1 + i}-01T10:00", "end": f"2025-0{1 + i}-02T10:00", "n": 40,
         "unique": 35, "place": f"P{i}"} for i in range(5)]}
    qs = default_questions(summary)
    assert qs[0].maps_to == "main_event_ids"  # events spread over months
    b = apply_answers(qs, {"scope": "complete", "focus": "balanced", "main": ["ev_01"]}, summary)
    assert b.scope == "complete" and b.people_share == 0.5 and b.main_event_ids == ["ev_01"]
    assert apply_answers(qs, {}, summary).people_share == 0.8  # family first by default
    assert b.target_photos > 100 and b.target_pages >= 24
    answers = wizard(qs, lambda text, default: "2" if "highlights" in text.lower() else default)
    assert answers["scope"] == "complete"


def test_ai_questions_are_validated():
    summary = {"events": [{"id": "ev_01"}]}
    raw = [{"id": "x", "text": "?", "kind": "single", "options": [{"value": "complete", "label": "c"}],
            "default": "zzz", "maps_to": "scope", "why": ""},
           {"id": "y", "text": "?", "kind": "single", "options": [], "default": "", "maps_to": "hack", "why": ""},
           {"id": "z", "text": "?", "kind": "multi", "options": [{"value": "ev_09", "label": "?"}],
            "default": "ev_09", "maps_to": "main_event_ids", "why": ""}]
    qs = validate_ai_questions(raw, summary)
    assert [q.maps_to for q in qs] == ["scope"] and qs[0].default == "complete"

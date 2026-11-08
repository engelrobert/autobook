import time
from datetime import date, datetime, timedelta

from autobook.cluster.chapters import make_chapters
from autobook.cluster.events import describe_events, split_events
from autobook.i18n import format_range
from autobook.select.allocate import allocate
from autobook.select.selector import choose_hero, pick, weights_for


def _photo(i, t: datetime, lat=None, lon=None, **kw):
    return {"id": f"p{i:03d}", "taken": t.isoformat(timespec="seconds"), "lat": lat, "lon": lon, **kw}


def test_split_by_time_gap_and_gps_jump():
    t0 = datetime(2025, 7, 1, 9)
    ps = [_photo(i, t0 + timedelta(minutes=20 * i), 38.7, -9.1) for i in range(10)]
    ps += [_photo(10 + i, t0 + timedelta(hours=26, minutes=20 * i), 38.7, -9.1) for i in range(10)]  # next day
    ps += [_photo(20 + i, t0 + timedelta(hours=30, minutes=20 * i), 41.15, -8.61) for i in range(10)]  # 270 km jump
    events = split_events(ps)
    assert [len(e) for e in events] == [10, 10, 10]


def test_tiny_events_are_merged():
    t0 = datetime(2025, 7, 1, 9)
    ps = [_photo(i, t0 + timedelta(minutes=10 * i)) for i in range(8)]
    ps.append(_photo(99, t0 + timedelta(hours=8)))  # lone photo 6.8 h later
    events = split_events(ps)
    assert len(events) == 1 and len(events[0]) == 9


def test_describe_and_chapters_by_place_and_day():
    t0 = datetime(2025, 7, 1, 9)
    groups = [[_photo(i, t0 + timedelta(days=d, minutes=10 * i), 38.7, -9.1) for i in range(12)] for d in range(2)]
    groups.append([_photo(50 + i, t0 + timedelta(days=3, minutes=10 * i), 41.1, -8.6) for i in range(12)])
    events = describe_events(groups)
    for e, place in zip(events, ["Lisbon", "Lisbon", "Porto"]):
        e["place"], e["country"] = place, "PT"
    by_place = make_chapters(events, "place")
    assert [c["place"] for c in by_place] == ["Lisbon", "Porto"] and by_place[0]["n"] == 24
    by_day = make_chapters(events, "day")
    assert len(by_day) == 3
    assert len(make_chapters(events, "event", max_chapters=2)) == 2


def test_allocate_respects_total_caps_and_weights():
    q = allocate({"a": 100, "b": 25, "c": 3}, 60)
    assert sum(q.values()) == 60 and q["c"] <= 3 and q["a"] > q["b"] > 0
    q2 = allocate({"a": 50, "b": 50}, 40, weights={"a": 3.0})
    assert q2["a"] > q2["b"]
    assert allocate({"a": 5, "b": 0}, 100) == {"a": 5, "b": 0}


def test_pick_one_per_dup_group_with_time_spread():
    t0 = datetime(2025, 7, 1, 9)
    cands = []
    for i in range(30):
        cands.append(_photo(i, t0 + timedelta(minutes=i), final=0.9 - i * 0.001, dup_group="g0" if i < 10 else f"g{i}",
                            blur=False))
    for i in range(30, 40):
        cands.append(_photo(i, t0 + timedelta(hours=5, minutes=i), final=0.6, dup_group=f"g{i}", blur=False))
    sel, alts = pick(cands, 8)
    assert len(sel) == 8
    assert sum(1 for p in sel if p["dup_group"] == "g0") == 1
    assert any(p["taken"] >= (t0 + timedelta(hours=5)).isoformat() for p in sel)  # later hour not starved
    assert sel == sorted(sel, key=lambda p: p["taken"])


def test_hero_avoids_blur_and_weights_mark_top_share():
    ps = [{"id": "a", "final": 0.99, "blur": True, "width": 3, "height": 2},
          {"id": "b", "final": 0.8, "blur": False, "width": 3, "height": 2},
          {"id": "c", "final": 0.7, "blur": False, "width": 2, "height": 3}]
    assert choose_hero(ps)["id"] == "b"
    w = weights_for(ps * 2)
    assert max(w.values()) > 1.0


def test_date_ranges_in_languages():
    a, b = date(2026, 7, 1), date(2026, 7, 14)
    assert format_range(a, b, "en") == "1–14 July 2026"
    assert format_range(a, b, "de") == "1.–14. Juli 2026"
    assert format_range(date(2026, 6, 30), date(2026, 7, 2), "en") == "30 June – 2 July 2026"
    assert format_range(a, a, "fr") == "1 juillet 2026"


def test_folders_people_sorted_their_photos_into():
    from autobook.cluster.folders import folder_groups
    from autobook.pipeline.select import folder_chapters

    root = "/trip/Urlaub"
    t0 = datetime(2026, 7, 1, 9)

    def ph(i, folder, hours):
        path = f"{root}/{folder}/IMG_{i}.jpg" if folder else f"{root}/IMG_{i}.jpg"
        return {"id": f"p{i:03d}", "path": path, "taken": (t0 + timedelta(hours=hours)).isoformat(), "event_id": "e1"}

    ps = [ph(1, "10 Heimweg", 50), ph(2, "2 Strand", 20), ph(3, "2 Strand/Favoriten", 21),
          ph(4, "DCIM/1 Anreise", 1), ph(5, "1 Anreise", 2), ph(6, "", 22)]  # 6 lies in the root, during the beach
    groups = folder_groups(ps, [root])
    assert [(g["label"], g["n"]) for g in groups] == [("Anreise", 2), ("Strand", 2), ("Heimweg", 1)]  # numbered order
    chs = folder_chapters(ps, [root])
    assert [c["label"] for c in chs] == ["Anreise", "Strand", "Heimweg"]
    assert chs[1]["photo_ids"] == ["p002", "p003", "p006"]  # the loose photo joins the chapter of its time
    # names without numbers: the order of the photos
    plain = [ph(1, "Strand", 20), ph(2, "Anreise", 1), ph(3, "Berge", 30)]
    assert [c["label"] for c in folder_chapters(plain, [root])] == ["Anreise", "Strand", "Berge"]
    # not sorted into folders: one folder, or most photos loose, or only camera folders
    assert folder_groups([ph(1, "Strand", 1), ph(2, "Strand", 2)], [root]) == []
    assert folder_groups([ph(1, "A", 1), ph(2, "B", 2), *[ph(10 + i, "", 3) for i in range(3)]], [root]) == []
    assert folder_groups([ph(1, "DCIM/100CANON", 1), ph(2, "Favoriten", 2)], [root]) == []


def test_chapters_by_folder_from_scan_to_plan(trip, tmp_path):
    """A trip sorted into "01 Lissabon", "02 Porto", "03 Lagos": offered and preselected, chapters
    in that order with the folder names, favourite folders and loose photos taken along."""
    import shutil

    from autobook.brief.models import Brief
    from autobook.brief.questions import build_questions
    from autobook.pipeline.scan import run_scan
    from autobook.pipeline.select import build_plan
    from autobook.settings import Work

    root = tmp_path / "Portugal"
    folders = {38.7: "01 Lissabon", 41.1: "02 Porto", 37.1: "03 Lagos"}
    porto_favs = 0
    for p in trip[1]:
        if p.lat is None:  # no EXIF: lies loose in the main folder
            dst = root / p.path.name
        else:
            folder = folders[min(folders, key=lambda lat: abs(lat - p.lat))]
            if folder == "02 Porto" and porto_favs < 3:
                folder, porto_favs = folder + "/Favoriten", porto_favs + 1
            dst = root / folder / p.path.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p.path, dst)
    work = Work(tmp_path / "work")
    summary = run_scan([root], work)
    q = next(q for q in build_questions(summary, work, None, "de") if q.maps_to == "chaptering")
    assert q.default == "folder" and q.options[0].label == "Nach deinen Ordnern (Lissabon, Porto, Lagos)"
    plan = build_plan(work, Brief(chaptering="folder", target_photos=24, language="de"))
    assert [c.title for c in plan.chapters] == ["Lissabon", "Porto", "Lagos"]
    assert plan.chapters[0].date_from < plan.chapters[1].date_from < plan.chapters[2].date_from

    # with AI the folder chapters get titles of their own (the folder name is the AI's strongest hint) …
    from autobook.ai.client import AIClient
    from autobook.catalog import Catalog
    from tests.conftest import FakeOpenAI

    sdk = FakeOpenAI()
    brief = Brief(chaptering="folder", target_photos=24, language="de")
    with_ai = build_plan(work, brief, ai=AIClient(Catalog(work.catalog), sdk_client=sdk))
    assert all(c.title.startswith("Days in") for c in with_ai.chapters)
    sent = " ".join(b.get("text", "") for call in sdk.responses.calls for b in call["input"][0]["content"])
    assert '"01 Lissabon"' in sent and '"02 Porto"' in sent
    # … and when new titles are suggested, the folder's name is one of the choices
    import json

    from fastapi.testclient import TestClient

    from autobook.web.server import create_app, register_book

    with_ai.save(work.plan)
    work.brief.write_text(brief.dump(), encoding="utf-8")
    lib = tmp_path / "lib"
    lib.mkdir()
    register_book(lib, work.root)
    c = TestClient(create_app(lib, "tok", ai_factory=lambda w, e: AIClient(Catalog(w.catalog), sdk_client=sdk)))
    h = {"x-autobook-token": "tok"}
    job = c.post(f"/api/books/{work.root.name}/titles", json={"tone": "warm"}, headers=h).json()["job"]
    for _ in range(300):
        j = c.get(f"/api/job/{job}", headers=h).json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j.get("error")
    assert list(j["result"]["folders"].values()) == ["Lissabon", "Porto", "Lagos"]
    assert json.loads(work.plan.read_text())["chapters"][0]["title"].startswith("Days in")  # unchanged

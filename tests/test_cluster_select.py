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

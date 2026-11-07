"""Fixes after the first live tests (2026-10-30): near-duplicates, folder names, map openers,
cover repetition, cost estimate, call timing, error summary, even scoring batches."""

from autobook.ai.client import AIClient, AIRequest, image_block
from autobook.ai.score import build_requests
from autobook.brief.models import Brief
from autobook.catalog import Catalog
from autobook.cluster.folders import dominant_label, folder_hints, labels_for
from autobook.pipeline.select import build_plan, hero_fits, merge_similar, pick_mixed
from tests.conftest import FakeOpenAI, fake_answer


def _client(work, sdk, **kw):
    return AIClient(Catalog(work.catalog), sdk_client=sdk, **kw)


def test_folder_labels_skip_camera_names():
    roots = ["/home/r/Pictures/2026-05-03 Vogtsbauernhof + Park mit allen Sinnen"]
    p = roots[0] + "/DCIM/100CANON/IMG_1.jpg"
    assert labels_for(p, roots) == ["2026-05-03 Vogtsbauernhof + Park mit allen Sinnen"]
    assert labels_for("/x/Pictures/Camera/IMG.jpg", ["/x/Pictures"]) == []
    paths = [roots[0] + f"/Pixel 7a/a{i}.jpg" for i in range(5)]
    assert folder_hints(paths, roots) == ["2026-05-03 Vogtsbauernhof + Park mit allen Sinnen"]
    assert dominant_label(paths, roots) == "Vogtsbauernhof + Park mit allen Sinnen"
    mixed = ["/t/Italy/Rome/a.jpg", "/t/Italy/Rome/b.jpg", "/t/Italy/Florence/c.jpg"]
    assert dominant_label(mixed, ["/t"]) == "Italy" and dominant_label(mixed[:2], ["/t"]) == "Rome"


def test_ai_similar_shots_join_one_group():
    photos = [{"id": "a", "dup_group": "a", "ai": {"similar_to": None}},
              {"id": "b", "dup_group": "b", "ai": {"similar_to": "a"}},
              {"id": "c", "dup_group": "c", "ai": {"similar_to": "b"}},
              {"id": "d", "dup_group": "d", "ai": None}]
    assert merge_similar(photos) == 2
    assert {p["dup_group"] for p in photos[:3]} == {"a"} and photos[3]["dup_group"] == "d"


def test_similar_to_from_scoring_reaches_the_selection(work_copy):
    def answer(params):
        out = fake_answer(params)
        if params["text"]["format"]["name"] == "score":  # every photo "looks like" the first of its request
            first = out["photos"][0]["id"]
            for rec in out["photos"][1:]:
                rec["similar_to"] = first
            out["photos"][0]["similar_to"] = ""
        return out
    plan = build_plan(work_copy, Brief(target_photos=30), ai=_client(work_copy, FakeOpenAI(answer)), batch=False)
    # one group per scoring request → at most one photo per request on the pages
    assert len({p.dup_group for p in plan.all_photos()}) == plan.photo_count()


def test_scoring_requests_are_evenly_sized(work_copy):
    photos = [{"id": p["id"]} for p in Catalog(work_copy.catalog).joined()][:32]
    reqs, _ = build_requests(work_copy, {}, {"ev": photos}, priorities=[], version="v", prompt="s", size=256,
                             chunk=30)
    sizes = [sum(1 for b in r.content if b["type"] == "input_image") for r in reqs]
    assert sizes == [16, 16]


def test_folder_names_reach_the_title_prompts(work_copy, fake_sdk):
    build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, fake_sdk), batch=False)
    texts = {c["text"]["format"]["name"]: " ".join(b["text"] for b in c["input"][0]["content"] if b["type"] == "input_text")
             for c in fake_sdk.responses.calls}
    assert '"2026-07 Portugal Rundreise"' in texts["title"] and '"2026-07 Portugal Rundreise"' in texts["book"]


def test_ai_cover_is_not_repeated_inside(work_copy, fake_sdk):
    plan = build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, fake_sdk),
                      batch=False)
    inside = {p.id for p in plan.all_photos()} | {c.divider.photo for c in plan.chapters if c.divider.style == "hero"}
    assert plan.book.cover_photo and plan.book.cover_photo not in inside


def test_failed_calls_are_reported_in_the_plan(work_copy):
    def answer(params):
        if params["text"]["format"]["name"] == "rank":
            raise RuntimeError("rate limited")
        return fake_answer(params)
    plan = build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, FakeOpenAI(answer)), batch=False)
    assert plan.warnings and plan.warnings[0].startswith("AI rank: ")
    assert "rate limited" in plan.warnings[0] and "offline selection" in plan.warnings[0]
    ok = build_plan(work_copy, Brief(target_photos=20), ai=_client(work_copy, FakeOpenAI()), batch=False)
    assert ok.warnings == []


def test_call_time_is_recorded_for_direct_calls(work_copy, fake_sdk):
    build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, fake_sdk), batch=False)
    rows = Catalog(work_copy.catalog).cost_summary()
    assert rows and all(r["avg_seconds"] is not None and r["avg_seconds"] >= 0 for r in rows)


def test_cost_estimate_follows_task_and_effort(work_copy):
    jpeg = b"\xff\xd8x"
    score = AIRequest("s", "x", [image_block(jpeg)] * 30, {}, "score", image_tokens=30 * 519)
    title = AIRequest("t", "x", [image_block(jpeg)] * 8, {}, "title", image_tokens=8 * 230)
    at = {e: _client(work_copy, FakeOpenAI(), effort=e) for e in ("high", "max")}
    # measured on 2026-10-30: 30-photo scoring at max ≈ $0.007, at high ≈ $0.003; titles ≈ $0.0004
    assert 0.005 < at["max"].estimate(score) < 0.012
    assert 0.002 < at["high"].estimate(score) < 0.005
    assert at["max"].estimate(title) < 0.002


def test_big_chapters_are_ranked_in_time_segments(work_copy):
    from autobook.ai.rank import MAX_IMAGES, rank_chapters, segments

    cands = [{"id": f"x{i:03d}", "taken": f"2026-07-01T{8 + i // 60:02d}:{i % 60:02d}:00", "final": (i % 7) / 7}
             for i in range(130)]
    segs = segments(cands, 80)
    assert all(len(part) <= MAX_IMAGES for part, _ in segs) and sum(k for _, k in segs) == 80
    assert [k for _, k in segs] == [round(80 * len(p) / 130) for p, _ in segs]
    # end to end with the fake model: one chapter, quota larger than one call can handle
    photos = Catalog(work_copy.catalog).joined()
    for p in photos:
        p["final"] = p["local_score"] or 0.0
    sdk = FakeOpenAI()
    ch = {"id": "ch_01", "place": "x", "start": photos[0]["taken"], "end": photos[-1]["taken"]}
    out = rank_chapters(_client(work_copy, sdk), work_copy, [{"chapter": ch, "candidates": photos, "k": 32}])
    calls = [c for c in sdk.responses.calls if c["text"]["format"]["name"] == "rank"]
    assert len(calls) == 2 and "part 1 of 2" in calls[0]["input"][0]["content"][0]["text"]
    sel = out["ch_01"]["selected"]
    assert len(sel) == 32 == len(set(sel)) and out["ch_01"]["hero"] in sel


def test_photos_per_page_sets_the_page_budget(work_copy):
    from autobook.export.fotobuch import solver_settings
    from autobook.export.profile import load_profile

    plan = build_plan(work_copy, Brief(target_photos=30, photos_per_page=5))
    assert plan.book.photos_per_page == 5
    s = solver_settings(plan, load_profile("a4-landscape"))
    assert s["book_layout_solver.page_target"] >= round(plan.photo_count() / 5)
    assert s["book_layout_solver.photos_per_page_max"] >= 6


def test_chapter_openers_need_a_sharp_fitting_photo():
    a4_landscape = (303.0, 216.0)  # page incl. bleed, mm
    square = (216.0, 216.0)
    pixel_land, pixel_port = {"width": 4080, "height": 3072}, {"width": 3072, "height": 4080}
    assert hero_fits(pixel_land, *a4_landscape)  # 4:3 landscape photo on a landscape page
    assert not hero_fits(pixel_port, *a4_landscape)  # a portrait selfie would lose 47 %
    assert hero_fits(pixel_port, *square)  # on a square page only 25 % are cut
    assert not hero_fits({"width": 1600, "height": 1200}, *a4_landscape)  # ~135 dpi: too soft


def test_mix_keeps_places_in_a_family_book():
    def ph(i, people, final):
        tags = ["kids"] if people else ["landscape"]
        return {"id": f"p{i}", "taken": f"2025-09-01T{10 + i // 60:02d}:{i % 60:02d}:00", "final": final,
                "dup_group": f"g{i}", "blur": False, "ai": {"tags": tags}}
    cands = [ph(i, True, 0.9) for i in range(30)] + [ph(30 + i, False, 0.5) for i in range(10)]
    sel, _ = pick_mixed(cands, 10, 2)  # 2 of 10 = the 80/20 mix
    assert len(sel) == 10 and sum(1 for p in sel if p["ai"]["tags"] == ["landscape"]) == 2
    only_people, _ = pick_mixed(cands, 10, None)
    assert sum(1 for p in only_people if p["ai"]["tags"] == ["landscape"]) == 0


def test_ranking_is_told_the_mix_and_corrected(work_copy):
    from autobook.ai.rank import enforce_mix

    cands = [{"id": f"p{i}", "final": 1 - i / 100, "dup_group": f"g{i}", "blur": False,
              "ai": {"tags": ["kids"] if i < 20 else ["water"]}} for i in range(30)]
    clean = {"selected": [f"p{i}" for i in range(10)], "hero": "p0", "strong": ["p1"], "notes": ""}
    fixed = enforce_mix(clean, cands, k_place=3)
    places = [pid for pid in fixed["selected"] if int(pid[1:]) >= 20]
    assert len(fixed["selected"]) == 10 and len(places) == 2  # 3 asked, 1 tolerance
    assert "p0" in fixed["selected"] and "p1" in fixed["selected"]  # hero and strong stay
    sdk = FakeOpenAI()
    build_plan(work_copy, Brief(target_photos=24, people_share=0.8), ai=_client(work_copy, sdk), batch=False)
    rank_text = " ".join(b["text"] for c in sdk.responses.calls if c["text"]["format"]["name"] == "rank"
                         for b in c["input"][0]["content"] if b["type"] == "input_text")
    assert "photos with people as the main subject" in rank_text


def test_ai_judgement_replaces_the_blur_detector(work_copy, fake_sdk):
    blurry_locally = set(Catalog(work_copy.catalog).kv_get("blur", []))
    plan = build_plan(work_copy, Brief(target_photos=40), ai=_client(work_copy, fake_sdk), batch=False)
    # the fake model never flags "blurry", so no photo is rejected for blur any more
    assert blurry_locally and not [r for r in plan.rejected if r.reason == "blur"]


def test_subtitles_on_request_come_from_one_call(work_copy, fake_sdk):
    plan = build_plan(work_copy, Brief(target_photos=24, subtitles=True), ai=_client(work_copy, fake_sdk),
                      batch=False)
    subs = [c.subtitle for c in plan.chapters]
    assert all(s.startswith("A fake subtitle") for s in subs) and len(set(subs)) == len(subs)
    assert plan.book.subtitle == "fake subtitle"

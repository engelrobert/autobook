"""Favourites: detection of the person's marks, the questions, must-haves in the selection,
the wider AI editor, and the bug fixes from the Jura analysis (2026-10-30)."""

import json

from autobook.ai.client import AIClient
from autobook.ai.favourites import check_markers
from autobook.brief.models import Brief
from autobook.brief.questions import (
    apply_answers,
    build_questions,
    favourite_counter,
    favourite_questions,
)
from autobook.catalog import Catalog
from autobook.discover.favourites import detect, level, marks_of
from autobook.pipeline.select import build_plan, one_per_group
from tests.conftest import FakeOpenAI


def _client(work, sdk, **kw):
    return AIClient(Catalog(work.catalog), sdk_client=sdk, **kw)


ROOT = "/p/2025-08-30 Jura"


def test_marks_and_levels_follow_the_persons_conventions():
    def lv(rel, markers=None):
        markers = markers or [{"kind": "folder", "pattern": "favoriten"}, {"kind": "prefix", "pattern": "+"}]
        return level(f"{ROOT}/{rel}", [ROOT], markers)
    assert lv("4 Baume (02.09.25)/Favoriten/P1.jpg") == 1
    assert lv("4 Baume (02.09.25)/Favoriten/+P2.jpg") == 2  # "+" inside a favourites folder: one of the best
    assert lv("Favoriten 7/+(1)PXL_3.jpg") == 2  # numbered folder, "+" followed by punctuation
    assert lv("++P1065583.JPG") == 2
    assert lv("1 Campingplatz/PXL_4.PORTRAIT.ORIGINAL.jpg") == 0
    assert lv("_DSC0001.jpg", [{"kind": "prefix", "pattern": "_"}]) == 0  # Sony's AdobeRGB prefix is no mark
    assert marks_of(f"{ROOT}/beste Bilder/x.jpg", [ROOT])["folder"] == "beste"
    assert marks_of(f"{ROOT}/Stoptop/x.jpg", [ROOT])["folder"] is None  # a word inside another word


def test_detect_counts_overlap_and_plus_levels():
    paths = ([f"{ROOT}/d1/Favoriten/p{i}.jpg" for i in range(6)] + [f"{ROOT}/d1/Favoriten/+q{i}.jpg" for i in range(3)]
             + [f"{ROOT}/d2/++r{i}.jpg" for i in range(3)] + [f"{ROOT}/d2/IMG_{i}.jpg" for i in range(20)]
             + [f"{ROOT}/d2/!x.jpg"])  # one "!" is below the minimum of 3
    cands = {(c["kind"], c["pattern"]): c for c in detect([(p, p) for p in paths], [ROOT])}
    assert set(cands) == {("folder", "favoriten"), ("prefix", "+")}
    assert cands[("folder", "favoriten")]["photos"] == 9
    plus = cands[("prefix", "+")]
    assert plus["photos"] == 6 and plus["inside_other"] == 3 and plus["by_count"] == {"+": 3, "++": 3}


def test_scan_stores_candidates_and_questions_ask_about_them(fav_work):
    summary = json.loads(fav_work.summary.read_text())
    fav = summary["favourites"]
    assert {c["pattern"] for c in fav["candidates"]} == {"favoriten", "+"} and fav["any"] > 10
    qs = build_questions(summary, fav_work, ai=None)
    assert [q.id for q in qs[:2]] == ["favmarks", "favmode"]
    assert len(qs[0].default) == 2 and qs[1].default == "fill"
    brief = apply_answers(qs, {}, summary, favourite_counter(fav_work))
    assert {m["pattern"] for m in brief.favourite_markers} == {"favoriten", "+"}
    assert brief.favourites_mode == "fill" and brief.target_photos >= round(fav["any"] * 1.15)
    only = apply_answers(qs, {"favmode": "only"}, summary, favourite_counter(fav_work))
    assert only.target_photos == fav["any"]
    none = apply_answers(qs, {"favmarks": []}, summary, favourite_counter(fav_work))
    assert none.favourite_markers == []
    assert favourite_questions({"favourites": {"candidates": []}}, []) == []


def test_ai_check_of_marks_is_validated(fav_work):
    summary = json.loads(fav_work.summary.read_text())
    cands = summary["favourites"]["candidates"]
    sdk = FakeOpenAI()
    out = check_markers(_client(fav_work, sdk), cands, ["d1 (10)", "d1/Favoriten (3)"])
    assert [(m["kind"], m["pattern"]) for m in out] == [(c["kind"], c["pattern"]) for c in cands]  # no "invented"
    call = sdk.responses.calls[0]
    assert all(b["type"] == "input_text" for b in call["input"][0]["content"])  # names only, no photos


def _brief(work, **kw):
    summary = json.loads(work.summary.read_text())
    marks = [{"kind": c["kind"], "pattern": c["pattern"], "level": 1} for c in summary["favourites"]["candidates"]]
    return Brief(favourite_markers=marks, **kw)


def _favs(work, brief):
    from autobook.discover.favourites import level as lv

    cat = Catalog(work.catalog)
    roots = cat.kv_get("roots", [])
    return {p["id"]: lv(p["path"], roots, brief.favourite_markers) for p in cat.joined()}


def test_favourites_are_must_haves_and_the_ai_fills_the_gaps(fav_work, fake_sdk):
    brief = _brief(fav_work, target_photos=30, favourites_mode="fill")
    favs = {i for i, l in _favs(fav_work, brief).items() if l}
    plan = build_plan(fav_work, brief, ai=_client(fav_work, fake_sdk), batch=False)
    in_book = {p.id for p in plan.all_photos()} | {c.divider.photo for c in plan.chapters} | {plan.book.cover_photo}
    assert favs <= in_book  # every favourite is in (on a page, as an opener or on the cover)
    assert plan.photo_count() + sum(1 for c in plan.chapters if c.divider.photo) >= len(favs)
    best = {i for i, l in _favs(fav_work, brief).items() if l >= 2}
    big = [p.id for p in plan.all_photos() if p.strong]
    assert big  # "+" favourites get more space ...
    for c in plan.chapters:  # ... but only up to a share of the chapter, or the pages stop working
        marked = [p for p in c.photos if p.id in best]
        assert sum(p.strong for p in marked) <= max(1, round(0.2 * len(c.photos)) + 1)
        assert not marked or any(p.strong for p in marked)
    assert plan.book.cover_candidates and plan.book.cover_candidates[0] in favs
    rank_calls = [c for c in fake_sdk.responses.calls if c["text"]["format"]["name"] == "rank"]
    texts = " ".join(b["text"] for c in rank_calls for b in c["input"][0]["content"] if b["type"] == "input_text")
    assert "Already in the book" in texts and "[fixed]" in texts  # favourites shown as context


def test_only_favourites_mode_skips_scoring(fav_work, fake_sdk):
    brief = _brief(fav_work, favourites_mode="only")
    favs = {i for i, l in _favs(fav_work, brief).items() if l}
    plan = build_plan(fav_work, brief, ai=_client(fav_work, fake_sdk), batch=False)
    in_book = {p.id for p in plan.all_photos()} | {c.divider.photo for c in plan.chapters if c.divider.photo}
    in_book |= {plan.book.cover_photo}
    assert in_book == favs
    assert not [c for c in fake_sdk.responses.calls if c["text"]["format"]["name"] in ("score", "rank")]


def test_best_of_the_favourites_mode(fav_work, fake_sdk):
    """Many favourites: the AI picks the target number among them; nothing else gets in."""
    brief = _brief(fav_work, favourites_mode="best", target_photos=8)
    levels = _favs(fav_work, brief)
    favs = {i for i, l in levels.items() if l}
    assert len(favs) > 12
    plan = build_plan(fav_work, brief, ai=_client(fav_work, fake_sdk), batch=False)
    in_book = {p.id for p in plan.all_photos()} | {c.divider.photo for c in plan.chapters if c.divider.photo}
    in_book |= {plan.book.cover_photo}
    assert in_book <= favs and len(in_book) < len(favs)
    groups = [p.dup_group for p in plan.all_photos()]
    assert len(groups) == len(set(groups))  # one shot per moment again, even among favourites
    scored = [b for c in fake_sdk.responses.calls if c["text"]["format"]["name"] == "score"
              for b in c["input"][0]["content"] if b["type"] == "input_text" and b["text"].startswith("[id=")]
    assert 0 < len(scored) <= len(favs)  # only the favourites are looked at
    rank_calls = [c for c in fake_sdk.responses.calls if c["text"]["format"]["name"] == "rank"]
    assert rank_calls and not any("[fixed]" in b.get("text", "") for c in rank_calls
                                  for b in c["input"][0]["content"])


def test_questions_suggest_the_best_of_many_favourites(fav_work):
    summary = json.loads(fav_work.summary.read_text())
    many = {**summary, "favourites": {**summary["favourites"], "any": 900}, "photos": 3000}
    qs = favourite_questions(many, [])
    assert qs[1].default == "best" and "best" in [o.value for o in qs[1].options]
    few = favourite_questions(summary, [])
    assert "best" not in [o.value for o in few[1].options]  # fewer favourites than a book: nothing to pick
    brief = apply_answers(qs, {}, many, lambda m: 900)
    assert brief.favourites_mode == "best" and brief.target_photos <= 250


def test_with_ai_only_judged_photos_compete_and_one_per_moment(work_copy, fake_sdk):
    plan = build_plan(work_copy, Brief(target_photos=40), ai=_client(work_copy, fake_sdk), batch=False)
    scored = set(Catalog(work_copy.catalog).ai_latest("score"))
    assert {p.id for p in plan.all_photos()} <= scored
    groups = [p.dup_group for p in plan.all_photos()]
    assert len(groups) == len(set(groups))


def test_one_per_group_keeps_favourites_and_refills():
    def ph(i, g, fav=0, final=0.5):
        return {"id": f"p{i}", "dup_group": g, "fav": fav, "final": final, "blur": False,
                "taken": f"2025-01-01T10:{i:02d}:00"}
    sel = [ph(1, "a", 1), ph(2, "a", 1), ph(3, "b", 0, 0.9), ph(4, "b", 0, 0.8)]
    alts = [ph(5, "b"), ph(6, "c")]
    out = [p["id"] for p in one_per_group(sel, alts)]
    assert out == ["p1", "p2", "p3", "p6"]  # both favourites stay; p4 (same moment as p3) → p6


def test_editor_sees_a_wider_pool(work_copy):
    from autobook.ai.rank import rank_chapters

    photos = Catalog(work_copy.catalog).joined()
    for i, p in enumerate(photos):
        p["final"] = 1 - i / len(photos)
        p["ai"] = {"interest": 9 if i == len(photos) - 1 else 5, "tags": ["kids"]}
    sdk = FakeOpenAI()
    ch = {"id": "ch_01", "place": "x", "start": photos[0]["taken"], "end": photos[-1]["taken"]}
    rank_chapters(_client(work_copy, sdk), work_copy, [{"chapter": ch, "candidates": photos, "k": 5}])
    shown = sum(sum(1 for b in c["input"][0]["content"] if b["type"] == "input_image") for c in sdk.responses.calls)
    assert shown == 15 + 1  # 3 × k by score, plus the very interesting photo with the lowest score


def test_eval_with_favourites_as_gold(fav_work):
    from autobook.eval.metrics import run_eval

    report = run_eval(fav_work, None, ["random", "local"], brief=_brief(fav_work, target_photos=30),
                      favourites=True)
    assert "same moment" in report and "| local |" in report


def test_book_language_is_guessed_from_folder_names(fav_work):
    from autobook.brief.questions import guess_language

    assert guess_language(["2025-08-30 bis 09-06 Urlaub im Jura", "5 Lac de Chalain (03.09.25)"]) == "de"
    assert guess_language(["Summer trip to Cornwall"]) == "en"
    assert guess_language(["IMG", "DCIM", "Lac du Lit du Roi"]) is None  # place names alone say nothing
    summary = json.loads(fav_work.summary.read_text())
    lang = next(q for q in build_questions(summary, fav_work, ai=None, ui_language="fr") if q.maps_to == "language")
    assert lang.default == "fr"  # one word ("Favoriten") is not enough: the interface language stays

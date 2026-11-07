"""Compare selection variants against a gold set of favourites.

Gold set: work/gold.json = {"favourites": [photo ids]}, made in the review UI's
"Gold set" tab (or imported from star ratings). Variants:
  random  stratified random pick with the same per-chapter quotas (baseline)
  local   offline heuristic (sharpness, exposure, colour, duplicates, time spread)
  ai      AI scoring + ranking + titles (uses cached AI results when present)
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from autobook.brief.models import Brief
from autobook.catalog import Catalog
from autobook.plan.models import BookPlan
from autobook.settings import Work

BAD_FLAGS = {"blurry", "eyes_closed", "screenshot", "accidental", "document"}


def load_gold(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("favourites", []))


def random_variant(plan: BookPlan, cat: Catalog, seed: int = 1) -> set[str]:
    rng = random.Random(seed)
    photos = cat.joined()
    by_event: dict[str, list[str]] = {}
    for p in photos:
        by_event.setdefault(p["event_id"], []).append(p["id"])
    out: set[str] = set()
    for ch in plan.chapters:
        pool = [pid for e in ch.event_ids for pid in by_event.get(e, [])]
        k = len(ch.photos) + (1 if ch.divider.photo and ch.divider.style == "hero" else 0)
        out.update(rng.sample(pool, min(k, len(pool))))
    return out


def selected_ids(plan: BookPlan) -> set[str]:
    ids = {p.id for p in plan.all_photos()}
    ids |= {c.divider.photo for c in plan.chapters if c.divider.photo}
    return ids


def metrics(selected: set[str], gold: set[str], cat: Catalog) -> dict:
    rows = {p["id"]: p for p in cat.joined()}
    blur = set(cat.kv_get("blur", []))
    ai = cat.ai_latest("score")
    hits = selected & gold
    groups = Counter(rows[i]["dup_group"] for i in selected if i in rows)
    moments = sum(1 for g in gold if g in rows and rows[g]["dup_group"] in groups)
    dup_leak = sum(n - 1 for n in groups.values() if n > 1)
    events = {rows[i]["event_id"] for i in rows}
    covered = {rows[i]["event_id"] for i in selected if i in rows}
    bad = [i for i in selected if i in blur or rows.get(i, {}).get("screenshot")
           or BAD_FLAGS & set((ai.get(i) or {}).get("flags", []))]
    return {
        "selected": len(selected),
        "recall": round(len(hits) / max(1, len(gold)), 3),
        "moment_recall": round(moments / max(1, len(gold)), 3),  # this shot or another of the same moment
        "precision": round(len(hits) / max(1, len(selected)), 3),
        "dup_leak": dup_leak,
        "event_coverage": round(len(covered) / max(1, len(events)), 3),
        "bad_picks": len(bad),
    }


def swaps_in_review(work: Work) -> int | None:
    """Photos changed between the first saved plan and the current one."""
    hist = sorted(work.plan_history.glob("plan-*.json")) if work.plan_history.exists() else []
    if not hist or not work.plan.exists():
        return None
    first = selected_ids(BookPlan.load(hist[0]))
    now = selected_ids(BookPlan.load(work.plan))
    return len(first ^ now) // 2


def favourite_gold(work: Work, brief: Brief) -> set[str]:
    """The person's own favourites as gold set: the confirmed marks, or every detected mark."""
    from autobook.discover.favourites import level

    cat = Catalog(work.catalog)
    try:
        roots = cat.kv_get("roots", [])
        markers = brief.favourite_markers or [{"kind": c["kind"], "pattern": c["pattern"]}
                                              for c in cat.kv_get("favourite_candidates", [])]
        return {p["id"] for p in cat.joined() if level(p["path"], roots, markers) > 0}
    finally:
        cat.close()


def run_eval(work: Work, gold_path: Path | None, variants: list[str], *, ai_factory=None,
             brief: Brief | None = None, favourites: bool = False) -> str:
    """Build each variant's selection with the same brief and write a markdown report.

    favourites=True: the gold set is the person's favourite marks, and the variants choose without
    knowing them (otherwise favourites would be must-haves and recall trivially 100 %)."""
    from autobook.pipeline.select import build_plan

    brief = brief or (Brief.model_validate_json(work.brief.read_text(encoding="utf-8"))
                      if work.brief.exists() else Brief())
    if favourites:
        gold = favourite_gold(work, brief)
        brief = brief.model_copy(update={"favourites_mode": "ignore"})
    else:
        gold = load_gold(gold_path)
    cat = Catalog(work.catalog)
    results: dict[str, dict] = {}
    local_plan = build_plan(work, brief)
    for v in variants:
        before = cat.total_cost()
        if v == "random":
            sel = random_variant(local_plan, cat)
        elif v == "local":
            sel = selected_ids(local_plan)
        elif v == "ai":
            if ai_factory is None:
                results[v] = {"error": "no API key / AI disabled"}
                continue
            sel = selected_ids(build_plan(work, brief, ai=ai_factory()))
        else:
            results[v] = {"error": "unknown variant"}
            continue
        m = metrics(sel, gold, cat)
        m["cost_usd"] = round(cat.total_cost() - before, 4)
        results[v] = m
    swaps = swaps_in_review(work)
    cat.close()
    lines = [f"# Selection evaluation ({len(gold)} gold favourites)", "",
             "| variant | selected | recall | same moment | precision | dup leak | event coverage | bad picks | cost $ |",
             "|---|---|---|---|---|---|---|---|---|"]
    for v, m in results.items():
        if "error" in m:
            lines.append(f"| {v} | {m['error']} | | | | | | | |")
        else:
            lines.append(f"| {v} | {m['selected']} | {m['recall']} | {m['moment_recall']} | {m['precision']} | "
                         f"{m['dup_leak']} | {m['event_coverage']} | {m['bad_picks']} | {m['cost_usd']} |")
    lines += ["", "recall = share of gold favourites that made it into the book; same moment = the favourite "
              "or another shot of the same moment; bad picks = blurry, eyes closed, screenshots or accidental "
              "shots in the selection."]
    if swaps is not None:
        lines.append(f"\nPhotos changed during review since the first saved plan: {swaps}")
    report = "\n".join(lines) + "\n"
    (work.root / "eval_report.md").write_text(report, encoding="utf-8")
    (work.root / "eval_report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return report

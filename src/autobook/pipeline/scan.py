"""`autobook scan`: discovery, metadata, thumbnails, analysis, duplicates, events, places."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from autobook.analyze.dedupe import duplicate_groups
from autobook.analyze.quality import analyze_image
from autobook.catalog import Catalog
from autobook.cluster.events import describe_events, split_events
from autobook.cluster.places import name_events
from autobook.discover.favourites import describe as describe_favourites
from autobook.discover.favourites import detect as detect_favourites
from autobook.discover.metadata import PhotoMeta, content_id, read_metadata
from autobook.discover.walk import scan as walk
from autobook.select.scoring import local_scores
from autobook.settings import Work
from autobook.thumbs import make_thumbs

Progress = Callable[[str, int, int], None]


def _noop(stage: str, done: int, total: int) -> None:
    pass


def run_scan(roots: list[Path], work: Work, *, progress: Progress = _noop,
             workers: int | None = None) -> dict:
    """Scan roots into the work dir's catalog. Safe to re-run: unchanged files are skipped."""
    work.ensure()
    cat = Catalog(work.catalog)
    found = walk([Path(r) for r in roots], exclude=[work.root])
    workers = workers or min(8, (os.cpu_count() or 2))
    total = len(found.files)
    errors: list[tuple[str, str]] = [(str(p), why) for p, why in found.skipped]

    # 1. metadata + content hash (skip files whose path/size/mtime did not change)
    fields = list(PhotoMeta.__dataclass_fields__)
    known = {(r["path"], r["size"], r["mtime"]): PhotoMeta(**{k: r[k] for k in fields})
             for r in cat.photos(active_only=False)}

    def meta_job(p: Path) -> PhotoMeta | tuple[str, str]:
        try:
            st = p.stat()
            hit = known.get((str(p), st.st_size, st.st_mtime))
            if hit:
                return hit
            return read_metadata(p, content_id(p))
        except (ValueError, OSError) as e:
            return (str(p), str(e))

    metas: dict[str, PhotoMeta] = {}
    with ThreadPoolExecutor(workers) as ex:
        for i, res in enumerate(ex.map(meta_job, found.files), 1):
            if isinstance(res, tuple):
                errors.append(res)
            elif res.id in metas:  # identical file in two places: keep the first path
                errors.append((res.path, f"duplicate of {metas[res.id].path}"))
            else:
                metas[res.id] = res
            progress("metadata", i, total)
    cat.deactivate_all()
    for m in metas.values():
        cat.upsert_photo(m)
    cat.commit()

    # 2. thumbnails + image analysis (only for photos without analysis)
    done_analysis = cat.analysis()
    todo = [m for m in metas.values() if m.id not in done_analysis or done_analysis[m.id]["phash"] is None]

    def analysis_job(m: PhotoMeta) -> tuple[str, dict] | tuple[str, str]:
        try:
            thumb = make_thumbs(m.path, m.id, work.root / "thumbs")
            return m.id, analyze_image(thumb, path=m.path, camera=m.camera, fmt=m.format)
        except Exception as e:  # corrupt files must not stop a 1000-photo scan
            return m.id, f"analysis failed: {e}"

    with ThreadPoolExecutor(workers) as ex:
        for i, (pid, res) in enumerate(ex.map(analysis_job, todo), 1):
            if isinstance(res, str):
                errors.append((metas[pid].path, res))
            else:
                cat.upsert_analysis(pid, **res)
            progress("analysis", i, len(todo))
    cat.commit()

    # 3. events, places, duplicates, local scores (always recomputed: cheap)
    photos = [p for p in cat.joined() if p.get("phash")]
    groups = split_events(photos)
    events = name_events(describe_events(groups))
    cat.replace_events(events)
    event_of = {pid: e["id"] for e in events for pid in e["data"]["photo_ids"]}
    dup = duplicate_groups(photos)
    for p in photos:
        p["event_id"] = event_of.get(p["id"])
    scores = local_scores(photos)
    for p in photos:
        cat.upsert_analysis(p["id"], event_id=p["event_id"], dup_group=dup.get(p["id"], p["id"]),
                            local_score=scores.get(p["id"], {}).get("local_score"))
    blur = {pid for pid, s in scores.items() if s["blur"]}
    cat.kv_set("blur", sorted(blur))
    cat.kv_set("scan_errors", errors)
    roots_abs = [str(Path(r).resolve()) for r in roots]
    cat.kv_set("roots", roots_abs)
    cat.kv_set("favourite_candidates", detect_favourites([(p["id"], p["path"]) for p in photos], roots_abs))
    cat.commit()

    summary = build_summary(cat, events, len(photos), errors)
    work.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    cat.close()
    return summary


def build_summary(cat: Catalog, events: list[dict], n_photos: int, errors: list) -> dict:
    photos = cat.joined()
    by_event: dict[str, list[dict]] = {}
    for p in photos:
        by_event.setdefault(p["event_id"], []).append(p)
    ev_out = []
    for e in events:
        ps = sorted(by_event.get(e["id"], []), key=lambda p: -(p["local_score"] or 0))
        dup_groups = {p["dup_group"] for p in ps}
        ev_out.append({
            "id": e["id"], "start": e["start"], "end": e["end"], "n": e["n"],
            "unique": len(dup_groups), "place": e.get("place"), "country": e.get("country"),
            "lat": e.get("lat"), "lon": e.get("lon"), "days": e["data"]["days"],
            "sample": [p["id"] for p in ps[:3]],
        })
    gps = sum(1 for p in photos if p["lat"] is not None)
    cams = sorted({p["camera"] for p in photos if p["camera"]})
    return {
        "photos": n_photos,
        "date_from": photos[0]["taken"] if photos else None,
        "date_to": photos[-1]["taken"] if photos else None,
        "days": len({p["taken"][:10] for p in photos}),
        "gps_share": round(gps / max(1, len(photos)), 2),
        "mtime_share": round(sum(1 for p in photos if p["ts_source"] == "mtime") / max(1, len(photos)), 2),
        "cameras": cams,
        "countries": sorted({e["country"] for e in ev_out if e["country"]}),
        "duplicates_collapsed": len(photos) - len({p["dup_group"] for p in photos}),
        "blurry": len(cat.kv_get("blur", [])),
        "events": ev_out,
        "errors": len(errors),
        "favourites": favourite_summary(cat.kv_get("favourite_candidates", [])),
    }


def favourite_summary(cands: list[dict]) -> dict:
    """Detected favourite marks for the questions (without the photo id lists)."""
    union = set().union(*(set(c["ids"]) for c in cands)) if cands else set()
    return {"candidates": [{k: v for k, v in c.items() if k != "ids"} | {"label": describe_favourites(c)}
                           for c in cands],
            "any": len(union)}

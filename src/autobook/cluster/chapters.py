"""Events -> chapters, following the brief's chaptering choice."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from autobook.cluster.events import haversine_km


def _gap_seconds(a: dict, b: dict) -> float:
    return (datetime.fromisoformat(b["start"]) - datetime.fromisoformat(a["end"])).total_seconds()


def _merge(a: dict, b: dict) -> dict:
    return {
        "event_ids": a["event_ids"] + b["event_ids"],
        "start": min(a["start"], b["start"]),
        "end": max(a["end"], b["end"]),
        "n": a["n"] + b["n"],
        "places": a["places"] + b["places"],
        "countries": a["countries"] + b["countries"],
        "coords": a["coords"] + b["coords"],
    }


def _from_event(e: dict) -> dict:
    return {
        "event_ids": [e["id"]],
        "start": e["start"],
        "end": e["end"],
        "n": e["n"],
        "places": [e["place"]] * e["n"] if e.get("place") else [],
        "countries": [e["country"]] * e["n"] if e.get("country") else [],
        "coords": [(e["lat"], e["lon"])] if e.get("lat") is not None else [],
    }


def _same_place(a: dict, b: dict, km: float = 10.0) -> bool:
    if a["places"] and b["places"] and Counter(a["places"]).most_common(1)[0][0] == \
            Counter(b["places"]).most_common(1)[0][0]:
        return True
    if a["coords"] and b["coords"]:
        return haversine_km(a["coords"][-1], b["coords"][0]) <= km
    return False


def resolve_mode(mode: str, events: list[dict]) -> str:
    if mode != "auto":
        return mode
    gps_share = sum(1 for e in events if e.get("lat") is not None) / max(1, len(events))
    days = {e["start"][:10] for e in events}
    if gps_share >= 0.5 and len({e.get("place") for e in events}) > 1:
        return "place"
    return "day" if len(days) > 1 else "event"


def make_chapters(events: list[dict], mode: str = "auto", *, max_chapters: int = 12,
                  min_photos: int = 8) -> list[dict]:
    """Group consecutive events into chapters. Order always stays chronological."""
    if not events:
        return []
    mode = resolve_mode(mode, events)
    chs = [_from_event(e) for e in events]
    if mode in ("day", "place", "theme"):  # theme falls back to place grouping until AI tags exist
        merged = [chs[0]]
        for c in chs[1:]:
            last = merged[-1]
            same = (last["start"][:10] == c["start"][:10]) if mode == "day" else _same_place(last, c)
            merged[-1:] = [_merge(last, c)] if same else [last, c]
        chs = merged

    def merge_at(i: int) -> None:  # merge chapter i with the neighbour closer in time
        if len(chs) < 2:
            return
        if i == 0:
            j = 1
        elif i == len(chs) - 1:
            j = i - 1
        else:
            j = i - 1 if _gap_seconds(chs[i - 1], chs[i]) <= _gap_seconds(chs[i], chs[i + 1]) else i + 1
        a, b = sorted((i, j))
        chs[a:b + 1] = [_merge(chs[a], chs[b])]

    while len(chs) > 1:
        small = [i for i, c in enumerate(chs) if c["n"] < min_photos]
        if not small:
            break
        merge_at(min(small, key=lambda i: chs[i]["n"]))
    while len(chs) > max_chapters:
        i = min(range(len(chs) - 1), key=lambda k: chs[k]["n"] + chs[k + 1]["n"])
        chs[i:i + 2] = [_merge(chs[i], chs[i + 1])]

    out = []
    for i, c in enumerate(chs):
        counts = Counter(c["places"])
        ordered = list(dict.fromkeys(c["places"]))
        if not ordered:
            label = None
        elif len(ordered) == 1 or counts.most_common(1)[0][1] / max(1, len(c["places"])) > 0.7:
            label = counts.most_common(1)[0][0]
        else:
            label = f"{ordered[0]} – {ordered[-1]}"
        out.append({
            "id": f"ch_{i + 1:02d}",
            "event_ids": c["event_ids"],
            "start": c["start"],
            "end": c["end"],
            "n": c["n"],
            "place": label,
            "country": Counter(c["countries"]).most_common(1)[0][0] if c["countries"] else None,
            "coords": c["coords"],
            "mode": mode,
        })
    return out

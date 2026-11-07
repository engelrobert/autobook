"""Split a time-ordered photo stream into events by time gaps and GPS jumps."""

from __future__ import annotations

import math
import statistics
from datetime import datetime


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def _ts(p: dict) -> float:
    return datetime.fromisoformat(p["taken"]).timestamp()


def split_events(photos: list[dict], *, min_gap_hours: float = 3.0, gap_factor: float = 4.0,
                 jump_km: float = 30.0, jump_min_gap_minutes: float = 30.0,
                 min_event_size: int = 3) -> list[list[dict]]:
    """Return lists of photos (time-ordered), one list per event.

    A new event starts when the gap to the previous photo exceeds
    max(min_gap_hours, gap_factor * median gap), or when the position jumps more
    than jump_km and at least jump_min_gap_minutes passed (so one long drive with
    photos along the way is not cut into pieces).
    """
    photos = sorted(photos, key=lambda p: (p["taken"], p["id"]))
    if not photos:
        return []
    times = [_ts(p) for p in photos]
    gaps = [b - a for a, b in zip(times, times[1:])]
    median_gap = statistics.median(gaps) if gaps else 0.0
    threshold = max(min_gap_hours * 3600, gap_factor * median_gap)

    events: list[list[dict]] = [[photos[0]]]
    last_pos = (photos[0]["lat"], photos[0]["lon"]) if photos[0].get("lat") is not None else None
    for i in range(1, len(photos)):
        p = photos[i]
        gap = times[i] - times[i - 1]
        pos = (p["lat"], p["lon"]) if p.get("lat") is not None else None
        jump = (pos is not None and last_pos is not None and haversine_km(pos, last_pos) > jump_km
                and gap >= jump_min_gap_minutes * 60)
        if gap > threshold or jump:
            events.append([])
        events[-1].append(p)
        if pos is not None:
            last_pos = pos

    # merge tiny events into the closer neighbour (in time), if it is within a day
    changed = True
    while changed and len(events) > 1:
        changed = False
        for i, ev in enumerate(events):
            if len(ev) >= min_event_size:
                continue
            before = _ts(ev[0]) - _ts(events[i - 1][-1]) if i > 0 else math.inf
            after = _ts(events[i + 1][0]) - _ts(ev[-1]) if i + 1 < len(events) else math.inf
            if min(before, after) > 24 * 3600:
                continue
            if before <= after:
                events[i - 1].extend(ev)
            else:
                events[i + 1][:0] = ev
            del events[i]
            changed = True
            break
    return events


def describe_events(groups: list[list[dict]]) -> list[dict]:
    """Event records: id, idx, start, end, n, median GPS, photo ids, days."""
    out = []
    for i, ev in enumerate(groups):
        gps = [(p["lat"], p["lon"]) for p in ev if p.get("lat") is not None]
        lat = statistics.median(g[0] for g in gps) if gps else None
        lon = statistics.median(g[1] for g in gps) if gps else None
        days = sorted({p["taken"][:10] for p in ev})
        out.append({
            "id": f"ev_{i + 1:02d}",
            "idx": i,
            "start": ev[0]["taken"],
            "end": ev[-1]["taken"],
            "n": len(ev),
            "lat": lat,
            "lon": lon,
            "data": {"photo_ids": [p["id"] for p in ev], "days": days,
                     "gps_share": round(len(gps) / len(ev), 2),
                     "track": [[round(a, 5), round(b, 5)] for a, b in gps[:: max(1, len(gps) // 50)]]},
        })
    return out

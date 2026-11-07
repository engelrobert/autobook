"""Local (offline) quality score and the blend with AI scores."""

from __future__ import annotations

from collections import defaultdict


def _rank_pct(values: list[float]) -> list[float]:
    """Percentile rank in [0, 1] (ties share the average rank)."""
    n = len(values)
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        r = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def local_scores(photos: list[dict]) -> dict[str, dict]:
    """photo id -> {"local_score", "blur"}; photos need event_id + analysis fields.

    Sharpness counts half against the whole collection (absolute blur) and half
    within the event (relative: the sharpest shot of a dim evening still wins).
    """
    out: dict[str, dict] = {}
    valid = [p for p in photos if p.get("sharpness") is not None]
    if not valid:
        return out
    global_rank = dict(zip((p["id"] for p in valid), _rank_pct([p["sharpness"] for p in valid])))
    by_event: dict[str, list[dict]] = defaultdict(list)
    for p in valid:
        by_event[p.get("event_id") or "_"].append(p)
    for evp in by_event.values():
        ev_rank = dict(zip((p["id"] for p in evp), _rank_pct([p["sharpness"] for p in evp])))
        for p in evp:
            g, e = global_rank[p["id"]], ev_rank[p["id"]]
            s_sharp = 0.5 * g + 0.5 * e
            expo_pen = (4 * max(0.0, p["clip_low"] - 0.05) + 4 * max(0.0, p["clip_high"] - 0.04)
                        + 3 * max(0.0, abs(p["brightness"] - 0.47) - 0.22))
            s_expo = max(0.0, 1.0 - expo_pen)
            s_color = min(1.0, (p.get("colorfulness") or 0) / 70.0)
            score = 0.55 * s_sharp + 0.27 * s_expo + 0.18 * s_color
            if p.get("screenshot"):
                score -= 0.6
            blur = g < 0.08 and e < 0.25
            if blur:
                score -= 0.25
            out[p["id"]] = {"local_score": round(max(0.0, min(1.0, score)), 4), "blur": blur}
    return out


AI_FLAG_PENALTY = {"blurry": 0.35, "eyes_closed": 0.25, "screenshot": 0.6, "document": 0.4,
                   "accidental": 0.6, "badly_exposed": 0.2}


def ai_score(ai: dict) -> float:
    """Normalise an AI score record to [0, 1]."""
    s = (0.25 * ai.get("tech", 5) + 0.35 * ai.get("aesthetic", 5) + 0.40 * ai.get("interest", 5)) / 10.0
    for f in ai.get("flags", []) or []:
        s -= AI_FLAG_PENALTY.get(f, 0.0)
    return max(0.0, min(1.0, s))


def final_score(local: float, ai: dict | None, priorities: list[str] | None = None) -> float:
    """Blend local and AI scores; brief priorities give a small bonus via AI tags."""
    if not ai:
        return local
    s = 0.25 * local + 0.75 * ai_score(ai)
    tags = set(ai.get("tags", []) or [])
    for pr in priorities or []:
        if pr in tags:
            s += 0.05
    return round(max(0.0, min(1.0, s)), 4)


# ── people / places mix ──────────────────────────────────────────────────────
PEOPLE_TAGS = {"people", "group", "portrait", "kids"}


def is_people(p: dict) -> bool | None:
    """True when the AI tagged people as the subject, False for places/landscapes/details,
    None when there are no AI tags (offline): then no mix can be enforced."""
    tags = (p.get("ai") or {}).get("tags")
    if not tags:
        return None if not p.get("ai") else False
    return bool(PEOPLE_TAGS & set(tags))


def split_quota(k: int, people_share: float | None) -> tuple[int, int]:
    """(photos with people, photos of the place) for a quota of k."""
    if people_share is None:
        return k, 0
    kp = round(k * people_share)
    return kp, k - kp

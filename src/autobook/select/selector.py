"""Pick k photos from a chapter: best shot per duplicate group, then score with time diversity."""

from __future__ import annotations

import math
from datetime import datetime

STRONG_WEIGHT = 1.8
NORMAL_WEIGHT = 1.0


def _t(p: dict) -> float:
    return datetime.fromisoformat(p["taken"]).timestamp()


def best_per_group(cands: list[dict], key: str = "final") -> list[dict]:
    best: dict[str, dict] = {}
    for p in cands:
        g = p.get("dup_group") or p["id"]
        if g not in best or p[key] > best[g][key]:
            best[g] = p
    return list(best.values())


def pick(cands: list[dict], k: int, *, key: str = "final", diversity: float = 0.12,
         exclude_bad: bool = True) -> tuple[list[dict], list[dict]]:
    """Return (selected in time order, alternatives by score).

    Greedy, MMR-like: each step takes the photo maximising score minus a penalty
    that grows with the number of already chosen photos close in time, so one
    busy hour cannot eat the whole chapter.
    """
    pool = best_per_group(cands, key)
    if exclude_bad:
        good = [p for p in pool if not p.get("blur") and not p.get("screenshot")]
        if len(good) >= k:
            pool = good
    if not pool or k <= 0:
        return [], sorted(cands, key=lambda p: -p[key])
    times = [_t(p) for p in pool]
    span = max(times) - min(times) if len(times) > 1 else 0
    tau = max(300.0, span / max(1, k))
    chosen: list[int] = []
    left = set(range(len(pool)))
    while left and len(chosen) < k:
        def gain(i: int) -> float:
            sim = sum(math.exp(-abs(times[i] - times[j]) / tau) for j in chosen)
            return pool[i][key] - diversity * sim
        i = max(left, key=gain)
        chosen.append(i)
        left.remove(i)
    selected = sorted((pool[i] for i in chosen), key=lambda p: (p["taken"], p["id"]))
    chosen_ids = {p["id"] for p in selected}
    alternatives = sorted((p for p in cands if p["id"] not in chosen_ids), key=lambda p: -p[key])
    return selected, alternatives


def choose_hero(photos: list[dict], key: str = "final") -> dict | None:
    """Best photo for a full-page divider: high score, landscape preferred, never blurry."""
    ok = [p for p in photos if not p.get("blur") and not p.get("screenshot")] or photos
    if not ok:
        return None

    def s(p: dict) -> float:
        aspect = (p.get("width") or 1) / max(1, p.get("height") or 1)
        return p[key] + (0.05 if aspect >= 1.2 else 0.0)
    return max(ok, key=s)


def weights_for(selected: list[dict], strong_ids: set[str] | None = None, key: str = "final",
                strong_share: float = 0.15) -> dict[str, float]:
    """Area weights: strong photos (AI-marked or top share by score) get more space."""
    if not selected:
        return {}
    if strong_ids is None:
        n = max(1 if len(selected) >= 4 else 0, round(len(selected) * strong_share))
        strong_ids = {p["id"] for p in sorted(selected, key=lambda p: -p[key])[:n]}
    return {p["id"]: (STRONG_WEIGHT if p["id"] in strong_ids else NORMAL_WEIGHT) for p in selected}

"""Split the photo budget across chapters."""

from __future__ import annotations

import math


def allocate(sizes: dict[str, int], total: int, *, weights: dict[str, float] | None = None,
             exponent: float = 0.5, min_each: int = 1) -> dict[str, int]:
    """Quota per chapter ∝ weight × size^exponent, capped at size (largest remainder method).

    exponent 0.5 ("highlights") flattens big chapters; ~0.85 ("complete") stays
    close to proportional. Every non-empty chapter gets at least min_each.
    """
    weights = weights or {}
    keys = [k for k, n in sizes.items() if n > 0]
    quotas = {k: 0 for k in sizes}
    if not keys or total <= 0:
        return quotas
    total = min(total, sum(sizes[k] for k in keys))
    for k in keys:
        quotas[k] = min(min_each, sizes[k])
    remaining = total - sum(quotas.values())
    while remaining > 0:
        open_keys = [k for k in keys if quotas[k] < sizes[k]]
        if not open_keys:
            break
        raw = {k: weights.get(k, 1.0) * sizes[k] ** exponent for k in open_keys}
        s = sum(raw.values())
        share = {k: remaining * raw[k] / s for k in open_keys}
        add = {k: min(int(math.floor(share[k])), sizes[k] - quotas[k]) for k in open_keys}
        given = sum(add.values())
        if given == 0:  # hand out single photos by largest remainder
            for k in sorted(open_keys, key=lambda k: share[k] - math.floor(share[k]), reverse=True):
                if remaining == 0:
                    break
                quotas[k] += 1
                remaining -= 1
            continue
        for k, a in add.items():
            quotas[k] += a
        remaining -= given
    return quotas

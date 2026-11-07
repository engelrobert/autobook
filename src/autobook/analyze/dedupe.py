"""Group bursts and near-duplicates so only the best shot of each group competes."""

from __future__ import annotations

from datetime import datetime

from autobook.analyze.quality import hamming


class _UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def duplicate_groups(photos: list[dict], *, burst_seconds: float = 3.0, burst_hamming: int = 18,
                     similar_seconds: float = 120.0, similar_hamming: int = 10,
                     copy_hamming: int = 4) -> dict[str, str]:
    """Return photo id -> group id (the group id is the id of its first photo in time).

    photos: dicts with id, taken (ISO), phash; sorted by time.
    Rules (any one links two photos):
      - burst: <= burst_seconds apart and phash distance <= burst_hamming
      - similar shot: <= similar_seconds apart and distance <= similar_hamming
      - copy (e.g. resized duplicate in another folder): distance <= copy_hamming at any time
    """
    n = len(photos)
    uf = _UnionFind(n)
    times = [datetime.fromisoformat(p["taken"]).timestamp() for p in photos]
    hashes = [p.get("phash") or "" for p in photos]
    for i in range(n):
        if not hashes[i]:
            continue
        for j in range(i + 1, n):
            dt = times[j] - times[i]
            if dt > similar_seconds:
                break
            if not hashes[j]:
                continue
            d = hamming(hashes[i], hashes[j])
            if (dt <= burst_seconds and d <= burst_hamming) or d <= similar_hamming:
                uf.union(i, j)
    if n <= 4000:  # global copies; O(n^2) is fine at this size
        ints = [int(h, 16) if h else None for h in hashes]
        for i in range(n):
            if ints[i] is None:
                continue
            for j in range(i + 1, n):
                if ints[j] is not None and bin(ints[i] ^ ints[j]).count("1") <= copy_hamming:
                    uf.union(i, j)
    return {photos[i]["id"]: photos[uf.find(i)]["id"] for i in range(n)}

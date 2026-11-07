"""Folder names people gave their photos ("2026-05-03 Vogtsbauernhof + Park mit allen Sinnen").

They are often the best hint about the place or occasion: GPS only knows the
nearest town. Camera- and app-generated names (DCIM, 100CANON, Camera, …) are ignored.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

GENERIC = re.compile(
    r"^(dcim|camera( roll)?|cameras?|pictures|photos?|bilder|fotos?|images?|img|pics|downloads?|"
    r"screenshots?|whatsapp.*|telegram.*|signal.*|backups?|imports?|imported|exports?|takeout|"
    r"google photos|icloud.*|new folder|neuer ordner|untitled.*|unbenannt.*|tmp|temp|"
    r"phone|handy|smartphone|iphone|android|pixel.*|galaxy.*|gopro|dji.*|drone|sd ?card|card\d*|"
    r"favou?rite[sn]?|favoriten|best of|beste|auswahl|selected|selection|picks|edited|bearbeitet|raw|jpe?g|"
    r"\d{3}[a-z_]*|\d{3}_?\d{4}|\d+)$", re.I)
ORDINAL_PREFIX = re.compile(r"^\s*\d{1,2}[\s._)-]+(?=\D)")  # "2 Cascades …", "01_Rome"
DATE_SUFFIX = re.compile(r"[\s,_-]*\(?\s*\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?\s*\)?\s*$")  # "… (31.08.25)"
DATE_PREFIX = re.compile(r"^\s*\d{4}[-_.]?\d{2}(?:[-_.]?\d{2})?"  # 2026-05-03 / 2026-07, also ranges:
                         r"(?:\s*(?:bis|to|au|-|–|_)\s*(?:\d{4}[-_.]?)?\d{2}(?:[-_.]?\d{2})?)?"  # "… bis 09-06"
                         r"(?=[\s_-]|$)[\s_-]*")


def labels_for(path: str, roots: list[str]) -> list[str]:
    """Meaningful folder names of one photo, from the scan root down to its folder."""
    p = Path(path)
    for root in sorted(roots, key=len, reverse=True):
        r = Path(root)
        try:
            rel = p.parent.relative_to(r)
        except ValueError:
            continue
        parts = [r.name, *rel.parts]
        return [x for x in parts if x and not GENERIC.match(x.strip())]
    return [x for x in [p.parent.name] if x and not GENERIC.match(x)]


def folder_hints(paths: list[str], roots: list[str], min_share: float = 0.2, limit: int = 3) -> list[str]:
    """Folder names covering at least min_share of the photos, most common first."""
    if not paths:
        return []
    counts: Counter[str] = Counter()
    for path in paths:
        counts.update(set(labels_for(path, roots)))
    return [name for name, n in counts.most_common() if n / len(paths) >= min_share][:limit]


def dominant_label(paths: list[str], roots: list[str], share: float = 0.8) -> str | None:
    """The deepest folder name shared by at least `share` of the photos, without a leading date."""
    if not paths:
        return None
    counts: Counter[str] = Counter()
    depth: dict[str, int] = {}
    for path in paths:
        labels = labels_for(path, roots)
        for i, name in enumerate(labels):
            depth[name] = max(depth.get(name, 0), i)
        counts.update(set(labels))
    common = [n for n, c in counts.items() if c / len(paths) >= share]
    if not common:
        return None
    best = max(common, key=lambda n: depth[n])
    return clean_label(best)


def clean_label(name: str) -> str | None:
    """Folder name as a title: without leading dates, numbering and trailing dates."""
    s = DATE_PREFIX.sub("", name)
    s = ORDINAL_PREFIX.sub("", s)
    s = DATE_SUFFIX.sub("", s)
    return s.strip(" -_,") or None


# ── chapters from the folders people sorted their photos into ───────────────────────────────
FOLDER_SHARE = 0.8  # at least this share of the photos must be in folders for them to count as chapters


def chapter_folder(path: str, roots: list[str]) -> str | None:
    """The folder directly below the scan root that a photo belongs to; deeper folders belong to it.
    Camera and favourite folders don't count ("DCIM/Strand/x.jpg" and "Strand/Favoriten/x.jpg" are
    both "Strand"). None for a photo lying in the root itself."""
    p = Path(path)
    for root in sorted(roots, key=len, reverse=True):
        try:
            rel = p.parent.relative_to(Path(root))
        except ValueError:
            continue
        return next((x for x in rel.parts if x and not GENERIC.match(x.strip())), None)
    return None


def _natural(name: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def folder_order(first_taken: dict[str, str]) -> list[str]:
    """Folder names in book order: as named when they are all numbered or dated ("01 Anreise",
    "2026-05-03 …"), else by their first photo."""
    names = list(first_taken)
    if all(ORDINAL_PREFIX.match(n) or DATE_PREFIX.match(n) for n in names):
        return sorted(names, key=_natural)
    return sorted(names, key=lambda n: (first_taken[n], _natural(n)))


def folder_groups(photos: list[dict], roots: list[str]) -> list[dict]:
    """The folders as chapters: [{folder, label, n}] in book order, or [] when the photos are not
    sorted into folders (fewer than two, or less than FOLDER_SHARE of the photos in them)."""
    counts: Counter[str] = Counter()
    first: dict[str, str] = {}
    for p in photos:
        f = chapter_folder(p["path"], roots)
        if f is None:
            continue
        counts[f] += 1
        first[f] = min(first.get(f, p["taken"]), p["taken"])
    if len(counts) < 2 or sum(counts.values()) < FOLDER_SHARE * len(photos):
        return []
    return [{"folder": f, "label": clean_label(f) or f, "n": counts[f]} for f in folder_order(first)]

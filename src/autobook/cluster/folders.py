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

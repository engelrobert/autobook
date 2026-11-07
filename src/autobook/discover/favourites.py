"""Find how a person marked their favourites: folder names and file-name marks.

Seen in practice (2026-10-30): folders "Favoriten", "Favoriten 7", "beste"; file names starting
with "+" (one of the best) or "++" (even better), often inside a Favoriten folder. Marks add up:
a favourite folder counts 1, every leading "+" one more.

`detect` proposes candidates; the person confirms them in the questions (an AI check of the
names helps, see autobook.ai.favourites). `level` then gives each photo its favourite level.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

FOLDER_WORDS = re.compile(
    r"(?i)(?<![a-zäöü])(favoriten|favou?rites?|favs?|beste?|best of|auswahl|ausgew(?:ä|ae)hlt|highlights?|"
    r"top|lieblings\w*|selected|picks|fotobuch|druck|print)(?![a-zäöü])")
# leading characters that are not favourite marks (Sony "_DSC", hidden files, copies, temp files)
NOT_PREFIX = set("_-.(~$")
SUFFIX_WORDS = re.compile(r"(?i)[ _-](fav\w*|best\w*|beste|top|lieblings\w*)$")
MIN_PHOTOS = 3
MAX_LEVEL = 3


def _rel_parts(path: str, roots: list[str]) -> list[str]:
    p = Path(path)
    for root in sorted(roots, key=len, reverse=True):
        try:
            return list(p.parent.relative_to(Path(root)).parts)
        except ValueError:
            continue
    return [p.parent.name]


def _prefix(name: str) -> str:
    """Leading mark of a file name: the run of "+" if it starts with one ("+(1)…" → "+"),
    else the leading non-alphanumeric run without spaces."""
    plus = re.match(r"\s*(\++)", name)
    if plus:
        return plus.group(1)
    m = re.match(r"[^A-Za-z0-9]+", name)
    return "".join(m.group(0).split()) if m else ""


def _folder_word(parts: list[str]) -> str | None:
    for part in reversed(parts):
        m = FOLDER_WORDS.search(part)
        if m:
            return m.group(1).lower()
    return None


def marks_of(path: str, roots: list[str]) -> dict[str, object]:
    """Raw marks of one photo: {"folder": word|None, "prefix": str, "suffix": word|None}."""
    stem = Path(path).stem
    parts = _rel_parts(path, roots)
    prefix = _prefix(Path(path).name)
    if prefix and prefix[0] in NOT_PREFIX:
        prefix = ""
    sm = SUFFIX_WORDS.search(stem)
    return {"folder": _folder_word(parts), "prefix": prefix, "suffix": sm.group(1).lower() if sm else None,
            "folder_name": next((p for p in reversed(parts) if FOLDER_WORDS.search(p)), None)}


def detect(photos: list[tuple[str, str]], roots: list[str]) -> list[dict]:
    """photos: [(id, path)]. Returns marker candidates with photo counts, overlaps and examples.

    A candidate: {kind: folder|prefix|suffix, pattern, photos, ids, inside_other, examples}.
    For prefix "+" every number of pluses counts ("+", "++"); `by_count` shows how many of each.
    """
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    examples: dict[tuple[str, str], Counter] = defaultdict(Counter)
    plus_counts: Counter = Counter()
    for pid, path in photos:
        m = marks_of(path, roots)
        if m["folder"]:
            key = ("folder", m["folder"])
            groups[key].append(pid)
            examples[key][m["folder_name"]] += 1
        if m["prefix"]:
            pre = m["prefix"]
            key = ("prefix", "+") if set(pre) == {"+"} else ("prefix", pre)
            groups[key].append(pid)
            examples[key][Path(path).name] += 1
            if key == ("prefix", "+"):
                plus_counts[len(pre)] += 1
        if m["suffix"]:
            key = ("suffix", m["suffix"])
            groups[key].append(pid)
            examples[key][Path(path).name] += 1
    out = []
    for (kind, pattern), ids in groups.items():
        if len(ids) < MIN_PHOTOS:
            continue
        others = set().union(*(set(v) for k, v in groups.items() if k != (kind, pattern) and len(v) >= MIN_PHOTOS))
        cand = {"kind": kind, "pattern": pattern, "photos": len(ids), "ids": ids,
                "inside_other": sum(1 for i in ids if i in others),
                "examples": [e for e, _ in examples[(kind, pattern)].most_common(3)]}
        if (kind, pattern) == ("prefix", "+"):
            cand["by_count"] = {"+" * n: c for n, c in sorted(plus_counts.items())}
        out.append(cand)
    return sorted(out, key=lambda c: -c["photos"])


def describe(c: dict) -> str:
    """Human label for a candidate, used in the questions."""
    n = c["photos"]
    if c["kind"] == "folder":
        names = ", ".join(f"'{e}'" for e in c["examples"][:2])
        return f"{n} photos in folders like {names}"
    if c["kind"] == "prefix":
        detail = ""
        if c.get("by_count") and len(c["by_count"]) > 1:
            detail = " (" + ", ".join(f"{v}× '{k}'" for k, v in c["by_count"].items()) + ")"
        inside = f", {c['inside_other']} of them also in a marked folder" if c.get("inside_other") else ""
        return f"{n} photos whose name starts with '{c['pattern']}'{detail}{inside}"
    return f"{n} photos whose name ends with '{c['pattern']}'"


def level(path: str, roots: list[str], markers: list[dict]) -> int:
    """Favourite level of a photo for the confirmed markers (0 = not a favourite).

    Folder and suffix markers add their level (default 1); the "+" prefix adds one per "+".
    """
    if not markers:
        return 0
    m = marks_of(path, roots)
    total = 0
    for mk in markers:
        kind, pattern, lvl = mk.get("kind"), mk.get("pattern"), int(mk.get("level", 1) or 1)
        if kind == "folder" and m["folder"] == pattern:
            total += lvl
        elif kind == "prefix" and m["prefix"]:
            if pattern == "+" and set(m["prefix"]) == {"+"}:
                total += len(m["prefix"]) * lvl
            elif m["prefix"] == pattern:
                total += lvl
        elif kind == "suffix" and m["suffix"] == pattern:
            total += lvl
    return min(total, MAX_LEVEL)

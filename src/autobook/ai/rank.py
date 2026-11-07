"""Editing pass: the model picks exactly k photos per chapter from the top candidates."""

from __future__ import annotations

import hashlib
import math

from autobook.ai.client import (
    AIClient,
    AIRequest,
    estimate_image_tokens,
    image_block,
    load_prompt,
)
from autobook.select.scoring import is_people, split_quota
from autobook.settings import Work
from autobook.thumbs import resized_for_ai

SCHEMA = {
    "type": "object",
    "properties": {
        "selected": {"type": "array", "items": {"type": "string"}},
        "hero": {"type": "string"},
        "strong": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["selected", "hero", "strong", "notes"],
    "additionalProperties": False,
}
MAX_IMAGES = 60


def _key(chapter_id: str, cand_ids: list[str], k: int, priorities: list[str], version: str,
         model: str, effort: str) -> str:
    h = hashlib.sha1("|".join([*cand_ids, str(k), *priorities]).encode()).hexdigest()[:16]
    return f"rank|{chapter_id}|{model}|{effort}|{version}|{h}"


SEGMENT_SIZE = 50  # candidates per call when a chapter is too big for one
FIXED_CONTEXT = 24  # favourites shown per call as "already in the book" (small thumbnails)


def share_out(total: int, weights: list[int]) -> list[int]:
    """Split `total` in proportion to `weights` (largest remainder), never above each weight."""
    s = sum(weights) or 1
    raw = [total * w / s for w in weights]
    out = [min(int(r), w) for r, w in zip(raw, weights)]
    for i in sorted(range(len(weights)), key=lambda i: raw[i] - int(raw[i]), reverse=True):
        if sum(out) >= min(total, sum(weights)):
            break
        if out[i] < weights[i]:
            out[i] += 1
    return out


def segments(cands: list[dict], k: int, max_images: int = MAX_IMAGES) -> list[tuple[list[dict], int]]:
    """Split time-ordered candidates into parts that fit one call; the quota is shared out in
    proportion to each part's size (largest remainder), so every part of the day stays represented."""
    if len(cands) <= max_images:
        return [(cands, k)]
    n = math.ceil(len(cands) / SEGMENT_SIZE)
    size = math.ceil(len(cands) / n)
    parts = [cands[i:i + size] for i in range(0, len(cands), size)]
    raw = [k * len(part) / len(cands) for part in parts]
    ks = [min(int(r), len(part)) for r, part in zip(raw, parts)]
    for i in sorted(range(len(parts)), key=lambda i: raw[i] - int(raw[i]), reverse=True):
        if sum(ks) >= k:
            break
        if ks[i] < len(parts[i]):
            ks[i] += 1
    return list(zip(parts, ks))


def enforce_mix(clean: dict, cands: list[dict], k_place: int, tolerance: int = 1) -> dict:
    """If the model picked clearly fewer place photos than asked, swap its weakest people photos
    (never the hero or a strong one) for the best unused place photos of other moments."""
    by = {p["id"]: p for p in cands}
    sel = list(clean["selected"])
    need = (k_place - tolerance) - sum(1 for pid in sel if is_people(by[pid]) is False)
    if k_place <= 0 or need <= 0:
        return clean
    groups = {by[pid]["dup_group"] for pid in sel}
    extra = [p for p in sorted(cands, key=lambda p: -p["final"]) if p["id"] not in sel
             and is_people(p) is False and not p.get("blur") and p["dup_group"] not in groups][:need]
    weakest = [pid for pid in sorted(sel, key=lambda pid: by[pid]["final"])
               if is_people(by[pid]) and pid != clean["hero"] and pid not in clean["strong"]]
    for new, old in zip(extra, weakest):
        sel[sel.index(old)] = new["id"]
    return {**clean, "selected": sel}


def rank_chapters(ai: AIClient, work: Work, jobs: list[dict], *, priorities: list[str] | None = None,
                  size: int = 512, batch: bool | None = None, progress=None,
                  people_share: float | None = None) -> dict[str, dict]:
    """jobs: [{chapter: {id, place, start, end}, candidates: [photo dicts with final, ai], k,
              fixed: [favourites already in the book] (optional), k_place: places wanted (optional)}].

    The editor sees a wide pool: the top max(3k, k+10) by score plus every photo the scoring call
    found very interesting (interest >= 8), so good photos with a modest overall score still get a
    chance. Fixed photos are shown as context ("already in the book") so the pick complements them.
    people_share: wanted share of photos with people as the subject (None = no target); used when a
    job has no explicit k_place.
    Returns chapter id -> {selected: [ids], hero: id, strong: [ids], notes} with ids validated.
    Big chapters are ranked in time segments (one call each) and merged; a failed segment falls
    back to its best-scored photos. Chapters with nothing to choose are left out.
    """
    version, prompt = load_prompt("rank")
    priorities = priorities or []
    cat = ai.catalog
    parts_of: dict[str, list[tuple[dict, list[dict], int, str | None]]] = {}  # ch -> (aliases, cands, k, key)
    k_place: dict[tuple[str, int], int] = {}
    done: dict[tuple[str, int], dict] = {}
    reqs: list[AIRequest] = []
    meta: dict[str, tuple[str, int]] = {}
    for job in jobs:
        ch, k = job["chapter"], job["k"]
        by_score = sorted(job["candidates"], key=lambda p: -p["final"])
        top = by_score[:max(3 * k, k + 10)]
        keen = [p for p in by_score[len(top):] if (p.get("ai") or {}).get("interest", 0) >= 8]
        pool = top + keen
        if k <= 0 or len(pool) <= k:
            continue  # nothing to choose
        pool.sort(key=lambda p: (p["taken"], p["id"]))
        segs = segments(pool, k)
        total_place = job.get("k_place")
        if total_place is None and people_share is not None:
            total_place = split_quota(k, people_share)[1]
        places = share_out(total_place, [k_i for _, k_i in segs]) if total_place is not None else None
        fixed = sorted(job.get("fixed") or [], key=lambda p: p["taken"])
        parts_of[ch["id"]] = []
        for i, (cands, k_i) in enumerate(segs):
            aliases = {f"p{j + 1:02d}": p for j, p in enumerate(cands)}
            ids = {a: p["id"] for a, p in aliases.items()}
            if len(cands) <= k_i:  # take the whole segment
                parts_of[ch["id"]].append((ids, cands, k_i, None))
                done[(ch["id"], i)] = validate({}, ids, cands, k_i)
                continue
            kl = places[i] if places is not None else None
            kp = k_i - kl if kl is not None else None
            k_place[(ch["id"], i)] = kl or 0
            lo = cands[0]["taken"] if i > 0 else ""
            hi = segs[i + 1][0][0]["taken"] if i + 1 < len(segs) else "9999"
            ctx = [p for p in fixed if lo <= p["taken"] < hi][:FIXED_CONTEXT]
            key = _key(f"{ch['id']}#{i}" if len(segs) > 1 else ch["id"],
                       [p["id"] for p in cands] + ["fixed:" + p["id"] for p in ctx], k_i,
                       priorities + [f"places={kl}"], version, ai.model, ai.effort)
            parts_of[ch["id"]].append((ids, cands, k_i, key))
            hit = cat.ai_get(key)
            if hit is not None:
                done[(ch["id"], i)] = hit
                continue
            part = (f" This is part {i + 1} of {len(segs)} of the chapter ({cands[0]['taken'][11:16]}–"
                    f"{cands[-1]['taken'][11:16]}); the other parts are chosen separately." if len(segs) > 1 else "")
            intro = (f"Chapter: {ch.get('place') or 'unnamed'}, {ch['start'][:10]} to {ch['end'][:10]}.{part} "
                     f"Select exactly {k_i} of these {len(cands)} photos."
                     + (f" Aim for about {kp} photos with people as the main subject and about {kl} photos of"
                        " the place itself (landscape, sights, nature, details, food) without people as the"
                        " main subject." if kl is not None else "")
                     + (f" Reader priorities: {', '.join(priorities)}." if priorities else ""))
            content: list[dict] = [{"type": "text", "text": intro}]
            img_tokens = 0
            if ctx:
                content.append({"type": "text", "text": (
                    f"Already in the book from this part (the person's own favourites), {len(ctx)} photos. "
                    "Do not select them again; choose photos that add moments, places or people they do not "
                    "show yet, not more of the same scene:")})
                for p in ctx:
                    content.append({"type": "text", "text": f"[fixed] time {p['taken'][11:16]}"})
                    content.append(image_block(resized_for_ai(work.root / "thumbs", p["id"], 256)))
                    img_tokens += estimate_image_tokens(256, 192)
                content.append({"type": "text", "text": "Candidates to choose from:"})
            for alias, p in aliases.items():
                a = p.get("ai") or {}
                line = (f"[id={alias}] time {p['taken'][11:16]}, group {p.get('dup_group', '')[-4:]}, "
                        f"tech {a.get('tech', '?')}, aesthetic {a.get('aesthetic', '?')}, "
                        f"interest {a.get('interest', '?')}, tags {', '.join(a.get('tags', [])) or '-'}")
                content.append({"type": "text", "text": line})
                content.append(image_block(resized_for_ai(work.root / "thumbs", p["id"], size)))
                img_tokens += estimate_image_tokens(size, int(size * 0.75))
            cid = f"rank-{ch['id']}" + (f"-{i}" if len(segs) > 1 else "")
            reqs.append(AIRequest(custom_id=cid, system=prompt, content=content, schema=SCHEMA, kind="rank",
                                  image_tokens=img_tokens))
            meta[cid] = (ch["id"], i)

    results = ai.run(reqs, batch=batch, progress=progress) if reqs else {}
    for cid, res in results.items():
        ch_id, i = meta[cid]
        ids, cands, k_i, key = parts_of[ch_id][i]
        if res.error or not res.data:
            continue
        clean = enforce_mix(validate(res.data, ids, cands, k_i), cands, k_place.get((ch_id, i), 0))
        cat.ai_put(key, None, "rank", clean)
        done[(ch_id, i)] = clean
    cat.commit()

    out: dict[str, dict] = {}
    for ch_id, parts in parts_of.items():
        merged = [done.get((ch_id, i)) or validate({}, ids, cands, k_i)  # failed part: best scores
                  for i, (ids, cands, k_i, _) in enumerate(parts)]
        if not any((ch_id, i) in done for i in range(len(parts))):
            continue  # every call failed: the caller uses its offline choice for the whole chapter
        final = {p["id"]: p["final"] for _, cands, _, _ in parts for p in cands}
        selected = [pid for m in merged for pid in m["selected"]]
        out[ch_id] = {"selected": selected,
                      "hero": max((m["hero"] for m in merged), key=lambda pid: final.get(pid, 0)),
                      "strong": [pid for m in merged for pid in m["strong"]],
                      "notes": " / ".join(m["notes"] for m in merged if m["notes"])}
    return out


def validate(data: dict, aliases: dict[str, str], cands: list[dict], k: int) -> dict:
    """Map aliases back to ids; force exactly k unique selections (fill/trim by score)."""
    def resolve(x) -> str | None:
        return aliases.get(str(x).strip("[]").replace("id=", "").strip())

    by_score = [p["id"] for p in sorted(cands, key=lambda p: -p["final"])]
    selected: list[str] = []
    for x in data.get("selected", []):
        pid = resolve(x)
        if pid and pid not in selected:
            selected.append(pid)
    if len(selected) > k:
        rank = {pid: i for i, pid in enumerate(by_score)}
        selected = sorted(selected, key=lambda pid: rank[pid])[:k]
    for pid in by_score:
        if len(selected) >= k:
            break
        if pid not in selected:
            selected.append(pid)
    hero = resolve(data.get("hero"))
    if hero not in selected:
        hero = next(pid for pid in by_score if pid in selected)
    strong = [pid for pid in (resolve(x) for x in data.get("strong", [])) if pid in selected]
    return {"selected": selected, "hero": hero, "strong": list(dict.fromkeys(strong)),
            "notes": str(data.get("notes", ""))[:300]}

"""Scoring pass: the model rates every candidate, 20-40 photos of one event per request."""

from __future__ import annotations

import math

from PIL import Image

from autobook.ai.client import (
    AIClient,
    AIRequest,
    estimate_image_tokens,
    image_block,
    load_prompt,
)
from autobook.settings import Work
from autobook.thumbs import resized_for_ai

TAGS = ["people", "group", "portrait", "kids", "landscape", "city", "architecture", "food", "nature",
        "animals", "water", "night", "activity", "detail", "transport", "sign"]
FLAGS = ["blurry", "eyes_closed", "screenshot", "document", "accidental", "badly_exposed"]

SCHEMA = {
    "type": "object",
    "properties": {
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "tech": {"type": "integer"},
                    "aesthetic": {"type": "integer"},
                    "interest": {"type": "integer"},
                    "flags": {"type": "array", "items": {"type": "string", "enum": FLAGS}},
                    "tags": {"type": "array", "items": {"type": "string", "enum": TAGS}},
                    "reason": {"type": "string"},
                    "similar_to": {"type": "string"},
                },
                "required": ["id", "tech", "aesthetic", "interest", "flags", "tags", "reason", "similar_to"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["photos"],
    "additionalProperties": False,
}


def _clamp(v, lo=1, hi=10) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return 5


def _alias(x) -> str:
    return str(x or "").strip().strip("[]").replace("id=", "").strip()


def cache_key(pid: str, model: str, effort: str, version: str, size: int) -> str:
    return f"score|{pid}|{model}|{effort}|{version}|{size}"


def candidates_for_scoring(photos: list[dict], per_group: int = 2) -> list[dict]:
    """Best `per_group` shots of each duplicate group by local score, plus every favourite;
    screenshots skipped."""
    by_group: dict[str, list[dict]] = {}
    for p in photos:
        if p.get("screenshot") and not p.get("fav"):
            continue
        by_group.setdefault(p.get("dup_group") or p["id"], []).append(p)
    out = []
    for ps in by_group.values():
        ranked = sorted(ps, key=lambda p: -(p.get("local_score") or 0))
        out.extend(p for i, p in enumerate(ranked) if i < per_group or p.get("fav"))
    return sorted(out, key=lambda p: (p["taken"], p["id"]))


def build_requests(work: Work, events: dict[str, dict], photos_by_event: dict[str, list[dict]], *,
                   priorities: list[str], version: str, prompt: str, size: int,
                   chunk: int) -> tuple[list[AIRequest], dict[str, dict[str, str]]]:
    reqs: list[AIRequest] = []
    alias_maps: dict[str, dict[str, str]] = {}
    pri = (f" The reader cares most about: {', '.join(priorities)}." if priorities else "")
    for ev_id, photos in photos_by_event.items():
        ev = events.get(ev_id, {})
        # equal-sized requests (32 photos → 16 + 16, not 30 + 2): photos are judged against their neighbours
        n_req = math.ceil(len(photos) / chunk)
        size_each = math.ceil(len(photos) / n_req)
        for k, start in enumerate(range(0, len(photos), size_each)):
            part = photos[start:start + size_each]
            cid = f"score-{ev_id}-{k}"
            aliases = {f"p{i + 1:02d}": p["id"] for i, p in enumerate(part)}
            alias_maps[cid] = aliases
            intro = (f"Event: {ev.get('place') or 'unknown place'}, {ev.get('start', '')[:10]} to "
                     f"{ev.get('end', '')[:10]}, {len(part)} photos.{pri}\n"
                     f"Allowed tags: {', '.join(TAGS)}.")
            content: list[dict] = [{"type": "text", "text": intro}]
            img_tokens = 0
            for alias, p in aliases.items():
                jpeg = resized_for_ai(work.root / "thumbs", p, size)
                content.append({"type": "text", "text": f"[id={alias}]"})
                content.append(image_block(jpeg))
                w, h = _thumb_dims(work, p, size)
                img_tokens += estimate_image_tokens(w, h)
            reqs.append(AIRequest(custom_id=cid, system=prompt, content=content, schema=SCHEMA, kind="score",
                                  image_tokens=img_tokens))
    return reqs, alias_maps


def _thumb_dims(work: Work, pid: str, size: int) -> tuple[int, int]:
    from autobook.thumbs import AI_SIZE, thumb_path

    p = thumb_path(work.root / "thumbs", pid, min(size, AI_SIZE))
    try:
        with Image.open(p) as im:
            return im.size
    except OSError:
        return size, size


def score_photos(ai: AIClient, work: Work, events: dict[str, dict], photos: list[dict], *,
                 priorities: list[str] | None = None, size: int = 768, chunk: int = 30,
                 batch: bool | None = None, progress=None) -> dict[str, dict]:
    """Return photo id -> {tech, aesthetic, interest, flags, tags, reason}; cached results reused."""
    version, prompt = load_prompt("score")
    cat = ai.catalog
    results: dict[str, dict] = {}
    todo_by_event: dict[str, list[dict]] = {}
    for p in candidates_for_scoring(photos):
        hit = cat.ai_get(cache_key(p["id"], ai.model, ai.effort, version, size))
        if hit is not None:
            results[p["id"]] = hit
        else:
            todo_by_event.setdefault(p.get("event_id") or "_", []).append(p)
    if not todo_by_event:
        return results
    reqs, alias_maps = build_requests(work, events, todo_by_event, priorities=priorities or [],
                                      version=version, prompt=prompt, size=size, chunk=chunk)
    out = ai.run(reqs, batch=batch, progress=progress)
    errors = []
    for cid, res in out.items():
        if res.error or not res.data:
            errors.append(f"{cid}: {res.error}")
            continue
        aliases = alias_maps[cid]
        for rec in res.data.get("photos", []):
            pid = aliases.get(_alias(rec.get("id")))
            if not pid:
                continue
            similar = aliases.get(_alias(rec.get("similar_to")))
            clean = {
                "tech": _clamp(rec.get("tech")),
                "aesthetic": _clamp(rec.get("aesthetic")),
                "interest": _clamp(rec.get("interest")),
                "flags": [f for f in rec.get("flags", []) if f in FLAGS],
                "tags": [t for t in rec.get("tags", []) if t in TAGS][:3],
                "reason": str(rec.get("reason", ""))[:120],
                "similar_to": similar if similar and similar != pid else None,
            }
            results[pid] = clean
            cat.ai_put(cache_key(pid, ai.model, ai.effort, version, size), pid, "score", clean)
    cat.kv_set("score_errors", errors)
    cat.commit()
    return results

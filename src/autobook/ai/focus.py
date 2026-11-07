"""Where the people or the main subject are in each photo, so a trimmed photo keeps them.

One small pass over the photos in the book (30 per request, 512 px). Results are cached in the
catalog per photo (kind "focus"), whichever model gave them, so a later layout never asks twice.
"""

from __future__ import annotations

from autobook.ai.client import AIClient, AIRequest, estimate_image_tokens, image_block, load_prompt
from autobook.ai.score import _alias, _thumb_dims
from autobook.settings import Work
from autobook.thumbs import resized_for_ai

SCHEMA = {
    "type": "object",
    "properties": {
        "focus": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}},
                "required": ["id", "x", "y"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["focus"],
    "additionalProperties": False,
}


def _unit(v) -> float:
    try:
        return round(max(0.0, min(1.0, float(v))), 3)
    except (TypeError, ValueError):
        return 0.5


def known_focus(cat) -> dict[str, tuple[float, float]]:
    """Photo id -> (x, y) of every photo the AI has already looked at."""
    return {pid: (_unit(v.get("x")), _unit(v.get("y"))) for pid, v in cat.ai_latest("focus").items()}


def missing_focus(cat, ids) -> list[str]:
    known = known_focus(cat)
    return [i for i in dict.fromkeys(ids) if i and i not in known]


def find_focus(ai: AIClient, work: Work, ids, *, size: int = 512, chunk: int = 30,
               batch: bool | None = None, progress=None) -> dict[str, tuple[float, float]]:
    """Photo id -> (x, y) for the given photos; asks the AI only for those it hasn't seen."""
    cat = ai.catalog
    todo = missing_focus(cat, ids)
    if todo:
        version, prompt = load_prompt("focus")
        reqs, aliases = [], {}
        for k, start in enumerate(range(0, len(todo), chunk)):
            part = todo[start:start + chunk]
            cid = f"focus-{k}"
            aliases[cid] = {f"p{i + 1:02d}": pid for i, pid in enumerate(part)}
            content: list[dict] = [{"type": "text", "text": f"{len(part)} photos."}]
            tokens = 0
            for alias, pid in aliases[cid].items():
                content.append({"type": "text", "text": f"[id={alias}]"})
                content.append(image_block(resized_for_ai(work.root / "thumbs", pid, size)))
                tokens += estimate_image_tokens(*_thumb_dims(work, pid, size))
            reqs.append(AIRequest(custom_id=cid, system=prompt, content=content, schema=SCHEMA, kind="focus",
                                  image_tokens=tokens))
        out = ai.run(reqs, batch=batch, progress=progress)
        for cid, res in out.items():
            if res.error or not res.data:
                continue
            for rec in res.data.get("focus", []):
                pid = aliases[cid].get(_alias(rec.get("id")))
                if pid:
                    cat.ai_put(f"focus|{pid}|{ai.model}|{ai.effort}|{version}|{size}", pid, "focus",
                               {"x": _unit(rec.get("x")), "y": _unit(rec.get("y"))})
        cat.commit()
    known = known_focus(cat)
    return {i: known[i] for i in ids if i in known}


def book_photo_ids(plan) -> list[str]:
    """Every photo the book shows: chapter photos, chapter pages and the cover (and its candidates)."""
    ids = [p.id for p in plan.all_photos()]
    ids += [c.divider.photo for c in plan.chapters if c.divider.photo]
    ids += [plan.book.cover_photo, *plan.book.cover_candidates]
    return [i for i in dict.fromkeys(ids) if i]

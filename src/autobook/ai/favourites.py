"""AI check of detected favourite marks (text only: folder names and file-name patterns, no photos)."""

from __future__ import annotations

import hashlib
import json

from autobook.ai.client import AIClient, AIRequest, load_prompt

SCHEMA = {
    "type": "object",
    "properties": {"markers": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["folder", "prefix", "suffix"]},
                       "pattern": {"type": "string"},
                       "is_favourite": {"type": "boolean"},
                       "level": {"type": "integer"},
                       "reason": {"type": "string"}},
        "required": ["kind", "pattern", "is_favourite", "level", "reason"],
        "additionalProperties": False}}},
    "required": ["markers"],
    "additionalProperties": False,
}


def heuristic(candidates: list[dict]) -> list[dict]:
    """Offline guess: every detected mark is a favourite mark of level 1 ("+" counts per plus)."""
    return [{"kind": c["kind"], "pattern": c["pattern"], "is_favourite": True, "level": 1,
             "reason": "matches a common way to mark favourites"} for c in candidates]


def check_markers(ai: AIClient | None, candidates: list[dict], folder_names: list[str]) -> list[dict]:
    """Suggestions for the questions: [{kind, pattern, is_favourite, level, reason}], one per candidate."""
    if not candidates:
        return []
    if ai is None:
        return heuristic(candidates)
    version, prompt = load_prompt("favourites")
    stats = [{k: c[k] for k in ("kind", "pattern", "photos", "inside_other", "examples") if k in c}
             | ({"by_count": c["by_count"]} if c.get("by_count") else {}) for c in candidates]
    text = (f"Detected marks:\n{json.dumps(stats, ensure_ascii=False)}\n"
            f"All folder names (photo counts):\n{json.dumps(folder_names[:80], ensure_ascii=False)}")
    key = f"favourites|{ai.model}|{ai.effort}|{version}|{hashlib.sha1(text.encode()).hexdigest()[:16]}"
    hit = ai.catalog.ai_get(key)
    if hit is not None:
        return hit["markers"]
    res = ai.run([AIRequest("favourites", prompt, [{"type": "text", "text": text}], SCHEMA, kind="favourites")],
                 batch=False)["favourites"]
    if res.error or not res.data:
        return heuristic(candidates)
    known = {(c["kind"], c["pattern"]) for c in candidates}
    out = {}
    for m in res.data.get("markers", []):
        k = (m.get("kind"), m.get("pattern"))
        if k in known and k not in out:
            out[k] = {"kind": k[0], "pattern": k[1], "is_favourite": bool(m.get("is_favourite")),
                      "level": max(1, min(2, int(m.get("level") or 1))), "reason": str(m.get("reason", ""))[:160]}
    result = [out.get((c["kind"], c["pattern"])) or heuristic([c])[0] for c in candidates]
    ai.catalog.ai_put(key, None, "favourites", {"markers": result})
    ai.catalog.commit()
    return result

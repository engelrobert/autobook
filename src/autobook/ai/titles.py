"""Chapter titles (all chapters in one call, so they don't repeat each other) and the book title /
cover photo. Subtitles are only requested when the brief asks for them."""

from __future__ import annotations

import hashlib

from autobook.ai.client import (
    AIClient,
    AIRequest,
    estimate_image_tokens,
    image_block,
    load_prompt,
)
from autobook.i18n import format_range
from autobook.settings import Work
from autobook.thumbs import resized_for_ai


def _schema(fields: dict[str, dict], wrap: str | None = None) -> dict:
    obj = {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}
    if wrap is None:
        return obj
    return {"type": "object", "properties": {wrap: {"type": "array", "items": obj}}, "required": [wrap],
            "additionalProperties": False}


def chapters_schema(subtitles: bool) -> dict:
    fields = {"id": {"type": "string"}, "title": {"type": "string"}}
    if subtitles:
        fields["subtitle"] = {"type": "string"}
    return _schema(fields, wrap="chapters")


def book_schema(subtitles: bool) -> dict:
    fields = {"book_title": {"type": "string"}, "cover_photo": {"type": "string"}}
    if subtitles:
        fields["subtitle"] = {"type": "string"}
    return _schema(fields)


def _words(s: str, n: int) -> str:
    s = " ".join(str(s).replace('"', "").replace("#", "").split())
    w = s.split(" ")
    return " ".join(w[:n]) if len(w) > n else s


def _key(kind: str, payload: str, version: str, ai: AIClient) -> str:
    return f"{kind}|{ai.model}|{ai.effort}|{version}|{hashlib.sha1(payload.encode()).hexdigest()[:16]}"


def _photo_blocks(work: Work, photo_ids: list[str], size: int, labels: bool = True) -> tuple[list[dict], dict[str, str], int]:
    blocks, aliases, tokens = [], {}, 0
    for i, pid in enumerate(photo_ids):
        alias = f"p{i + 1:02d}"
        aliases[alias] = pid
        if labels:
            blocks.append({"type": "text", "text": f"[id={alias}]"})
        blocks.append(image_block(resized_for_ai(work.root / "thumbs", pid, size)))
        tokens += estimate_image_tokens(size, int(size * 0.75))
    return blocks, aliases, tokens


def _folders_line(folders: list[str] | None) -> str:
    if not folders:
        return ""
    return "\nFolder names given by the person: " + "; ".join(f'"{f}"' for f in folders) + "."


def _resolve(aliases: dict[str, str], x) -> str | None:
    return aliases.get(str(x).strip("[]").replace("id=", "").strip())


def chapter_titles(ai: AIClient, work: Work, chapters: list[dict], *, language: str, tone: str,
                   subtitles: bool = False, size: int = 384, per_chapter: int = 6,
                   progress=None) -> dict[str, dict]:
    """chapters: [{id, place, start, end, photo_ids (best first), folders}] in book order.

    One call for the whole book: the model sees every chapter, so titles (and subtitles) do not
    repeat. Returns chapter id -> {title, subtitle}; missing on failure (caller keeps its title).
    """
    if not chapters:
        return {}
    version, prompt = load_prompt("titles")
    head = (f"Language: {language}. Tone: {tone}. Subtitles: {'yes' if subtitles else 'no'}.\n"
            f"The book has {len(chapters)} chapters, in this order:")
    content: list[dict] = [{"type": "text", "text": head}]
    payload = [head]
    tokens = 0
    for ch in chapters:
        ids = ch["photo_ids"][:per_chapter]
        text = (f"Chapter {ch['id']}: place {ch.get('place') or 'unknown'}, "
                f"{format_range(_d(ch['start']), _d(ch['end']), language)}." + _folders_line(ch.get("folders")))
        blocks, _, t = _photo_blocks(work, ids, size, labels=False)
        content += [{"type": "text", "text": text}, *blocks]
        payload += [text, *ids]
        tokens += t
    key = _key("titles", "|".join(payload), version, ai)
    hit = ai.catalog.ai_get(key)
    if hit is not None:
        return hit
    req = AIRequest("titles", prompt, content, chapters_schema(subtitles), kind="title", image_tokens=tokens)
    res = ai.run([req], batch=False, progress=progress)["titles"]
    if res.error or not res.data:
        return {}
    known = {ch["id"] for ch in chapters}
    out = {}
    for rec in res.data.get("chapters", []):
        cid = str(rec.get("id", "")).replace("Chapter", "").strip()
        if cid in known and rec.get("title"):
            out[cid] = {"title": _words(rec["title"], 4),
                        "subtitle": _words(rec.get("subtitle", ""), 10) if subtitles else ""}
    ai.catalog.ai_put(key, None, "title", out)
    ai.catalog.commit()
    return out


def book_title(ai: AIClient, work: Work, chapters: list[dict], cover_candidates: list[str], *,
               language: str, tone: str, date_from: str, date_to: str, size: int = 512,
               folders: list[str] | None = None, subtitles: bool = False) -> dict | None:
    version, prompt = load_prompt("book")
    lines = "\n".join(f"- {c.get('title') or c.get('place')} ({c.get('place') or '-'})" for c in chapters)
    text = (f"Language: {language}. Tone: {tone}. Subtitle: {'yes' if subtitles else 'no'}.\nDates: "
            f"{format_range(_d(date_from), _d(date_to), language)}.\nChapters:\n{lines}"
            + _folders_line(folders))
    ids = cover_candidates[:8]
    key = _key("book", "|".join([text, *ids]), version, ai)
    hit = ai.catalog.ai_get(key)
    if hit is not None:
        return hit
    blocks, aliases, tokens = _photo_blocks(work, ids, size)
    req = AIRequest("book", prompt, [{"type": "text", "text": text}, *blocks], book_schema(subtitles),
                    kind="book", image_tokens=tokens)
    res = ai.run([req], batch=False)["book"]
    if res.error or not res.data:
        return None
    clean = {"book_title": _words(res.data["book_title"], 4),
             "subtitle": _words(res.data.get("subtitle", ""), 8) if subtitles else "",
             "cover_photo": _resolve(aliases, res.data.get("cover_photo"))}
    ai.catalog.ai_put(key, None, "book", clean)
    ai.catalog.commit()
    return clean


def _d(iso: str):
    from datetime import date

    return date.fromisoformat(iso[:10])

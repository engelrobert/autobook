"""The dynamic question screen: generate 3-6 questions, turn answers into a Brief.

Offline, a data-driven default question set is used. With AI, the model tailors the
questions to the scan summary; its output is validated against the allowed fields
and values, and anything invalid falls back to the defaults.
"""

from __future__ import annotations

import json
from pathlib import Path

from autobook.ai.score import TAGS
from autobook.brief.models import QA, Brief, Option, Question
from autobook.brief.texts import tr
from autobook.cluster.places import country_name

LANGS = {"en": "English", "de": "Deutsch", "fr": "Français", "es": "Español", "it": "Italiano",
         "nl": "Nederlands", "pt": "Português"}
ALLOWED = {
    "main_event_ids": None,  # options are event ids (filled per scan)
    "scope": ["highlights", "complete"],
    "target_photos": None,
    "chaptering": ["auto", "place", "day", "event", "theme", "folder"],
    "title_tone": ["plain", "warm", "playful", "poetic"],
    "language": list(LANGS),
    "focus": ["family", "balanced", "places", "any"],
    "book_title": None,
    "priorities": TAGS,
}


BEST_FACTOR = 1.5  # more favourites than this × the usual book size: suggest "the best of the favourites"


def suggested_target(summary: dict, scope: str) -> int:
    unique = sum(e.get("unique", e["n"]) for e in summary.get("events", []))
    if scope == "complete":
        return max(20, min(400, round(unique * 0.6)))
    return max(20, min(250, round(unique * 0.3)))


def _event_label(e: dict, lang: str = "en") -> str:
    from datetime import date

    from autobook.i18n import format_date, format_range

    a, b = date.fromisoformat(e["start"][:10]), date.fromisoformat(e["end"][:10])
    dates = format_date(a, lang) if a == b else format_range(a, b, lang)
    return tr(lang, "event", place=e.get("place") or "?", dates=dates, n=e["n"])


def default_questions(summary: dict, ui_language: str = "en") -> list[Question]:
    """Data-driven defaults; never asks what the data already answers. Texts in the UI language."""
    lang = ui_language
    qs: list[Question] = []
    events = summary.get("events", [])
    # several events far apart in time → ask which ones are the main story
    if len(events) > 1:
        starts = sorted(e["start"] for e in events)
        span_days = (int(starts[-1][:4]) - int(starts[0][:4])) * 365 + abs(
            int(starts[-1][5:7]) - int(starts[0][5:7])) * 30
        if span_days > 21 or len(events) > 8:
            big = sorted(events, key=lambda e: -e["n"])[: max(1, len(events) // 2)]
            qs.append(Question(
                id="main", text=tr(lang, "main.text"), kind="multi",
                options=[Option(value=e["id"], label=_event_label(e, lang)) for e in events],
                default=[e["id"] for e in big], maps_to="main_event_ids", why=tr(lang, "main.why")))
    qs.append(Question(
        id="scope", text=tr(lang, "scope.text"), kind="single",
        options=[Option(value="highlights", label=tr(lang, "scope.highlights", n=suggested_target(summary, "highlights"))),
                 Option(value="complete", label=tr(lang, "scope.complete", n=suggested_target(summary, "complete")))],
        default="highlights", maps_to="scope"))
    has_places = summary.get("gps_share", 0) >= 0.5 and len({e.get("place") for e in events}) > 1
    opts = [Option(value=v, label=tr(lang, f"chapters.{v}"))
            for v in (["place"] if has_places else []) + ["day", "event", "theme"]]
    qs.append(Question(id="chapters", text=tr(lang, "chapters.text"), kind="single",
                       options=opts, default="place" if has_places else "day", maps_to="chaptering"))
    qs.append(Question(id="tone", text=tr(lang, "tone.text"), kind="single",
                       options=[Option(value=v, label=tr(lang, f"tone.{v}")) for v in ALLOWED["title_tone"]],
                       default="warm", maps_to="title_tone"))
    qs.append(Question(id="language", text=tr(lang, "language.text"), kind="single",
                       options=[Option(value=k, label=v) for k, v in LANGS.items()],
                       default=ui_language if ui_language in LANGS else "en", maps_to="language"))
    qs.append(Question(id="focus", text=tr(lang, "focus.text"), kind="single",
                       options=[Option(value=v, label=tr(lang, f"focus.{v}"))
                                for v in ("family", "balanced", "places", "any")],
                       default="family", maps_to="focus", why=tr(lang, "focus.why")))
    return qs[:6]


def favourite_label(c: dict, lang: str = "en") -> str:
    """'564 photos whose name starts with '+' (563× '+', 1× '++'), 463 of them also in a marked folder'."""
    if c["kind"] == "folder":
        examples = ", ".join(f"„{e}“" if lang == "de" else f"'{e}'" for e in c.get("examples", [])[:2])
        text = tr(lang, "fav.folder", n=c["photos"], examples=examples or c["pattern"])
    else:
        text = tr(lang, f"fav.{c['kind']}", n=c["photos"], pattern=c["pattern"])
        counts = c.get("by_count") or {}
        if len(counts) > 1:
            text += tr(lang, "fav.counts", counts=", ".join(f"{n}× '{k}'" for k, n in counts.items()))
    if c.get("inside_other"):
        text += tr(lang, "fav.inside", n=c["inside_other"])
    return text


def favourite_questions(summary: dict, suggestions: list[dict], lang: str = "en") -> list[Question]:
    """Ask which detected marks are favourite marks and how to use them (only when marks were found)."""
    cands = (summary.get("favourites") or {}).get("candidates") or []
    if not cands:
        return []
    sugg = {(s["kind"], s["pattern"]): s for s in suggestions}
    opts, default = [], []
    for c in cands:
        s = sugg.get((c["kind"], c["pattern"]), {"is_favourite": True, "level": 1, "reason": ""})
        meaning = tr(lang, "fav.best" if s["level"] >= 2 else "fav.good")
        value = f"{c['kind']}:{c['pattern']}:{s['level']}"
        opts.append(Option(value=value, label=f"{favourite_label(c, lang)} → {meaning}"))
        if s["is_favourite"]:
            default.append(value)
    n_any = (summary.get("favourites") or {}).get("any", 0)
    total = summary.get("photos", 0) or 1
    target = suggested_target(summary, "highlights")
    fill_to = max(target, round(n_any * 1.15))
    modes = [Option(value="fill", label=tr(lang, "favmode.fill", n=fill_to)),
             Option(value="only", label=tr(lang, "favmode.only", n=n_any))]
    if n_any > target:  # more favourites than a usual book holds: let the AI pick the best of them
        modes.append(Option(value="best", label=tr(lang, "favmode.best", n=target)))
    modes.append(Option(value="ignore", label=tr(lang, "favmode.ignore")))
    if n_any > BEST_FACTOR * target:
        default_mode = "best"
    elif n_any >= 0.9 * total:
        default_mode = "only"
    else:
        default_mode = "fill"
    return [
        Question(id="favmarks", text=tr(lang, "favmarks.text"), kind="multi",
                 options=opts, default=default, maps_to="favourite_markers", why=tr(lang, "favmarks.why")),
        Question(id="favmode", text=tr(lang, "favmode.text", n=n_any), kind="single",
                 options=modes, default=default_mode, maps_to="favourites_mode",
                 why=tr(lang, "favmode.why_best", n=n_any) if default_mode == "best" else ""),
    ]


def folder_name_counts(work) -> list[str]:
    """'name (photos)' for every folder below the scan roots, for the AI check of favourite marks."""
    from collections import Counter

    from autobook.catalog import Catalog
    from autobook.discover.favourites import _rel_parts

    cat = Catalog(work.catalog)
    try:
        roots = cat.kv_get("roots", [])
        counts = Counter("/".join(_rel_parts(p["path"], roots)) or "." for p in cat.joined())
    finally:
        cat.close()
    return [f"{name} ({n})" for name, n in counts.most_common()]


def favourite_counter(work):
    """Function markers -> number of photos with a favourite level > 0 (for the target size)."""
    from autobook.catalog import Catalog
    from autobook.discover.favourites import level

    def count(markers: list[dict]) -> int:
        cat = Catalog(work.catalog)
        try:
            roots = cat.kv_get("roots", [])
            return sum(1 for p in cat.joined() if level(p["path"], roots, markers) > 0)
        finally:
            cat.close()
    return count


# everyday words people put into photo folder names, by language (no place-name words like "lac", "see")
LANG_WORDS = {
    "de": {"urlaub", "ferien", "ausflug", "wanderung", "geburtstag", "hochzeit", "weihnachten", "ostern", "bis",
           "und", "mit", "am", "im", "beim", "zum", "zur", "bei", "tag", "favoriten", "beste", "bilder", "sommer",
           "herbst", "frühling", "allen", "sinnen", "kinder", "familie", "oma", "opa", "besuch", "fahrt"},
    "fr": {"vacances", "anniversaire", "mariage", "noël", "avec", "chez", "et", "journée", "sortie", "promenade",
           "été", "hiver", "famille", "meilleures", "voyage"},
    "en": {"holiday", "holidays", "vacation", "trip", "birthday", "wedding", "christmas", "and", "with", "day",
           "best", "favourites", "favorites", "summer", "family", "kids", "the", "visit"},
    "es": {"vacaciones", "cumpleaños", "boda", "navidad", "día", "verano", "familia", "viaje"},
    "it": {"vacanze", "compleanno", "matrimonio", "natale", "giorno", "estate", "famiglia", "viaggio"},
    "nl": {"vakantie", "verjaardag", "bruiloft", "kerst", "met", "dag", "zomer", "familie", "naar", "uitje"},
}


def guess_language(names: list[str]) -> str | None:
    """The book's language from the person's folder names, when they clearly point to one."""
    import re

    hits = {lang: 0 for lang in LANG_WORDS}
    for name in set(names):
        words = set(re.findall(r"[^\W\d_]+", name.lower()))
        for lang, vocab in LANG_WORDS.items():
            hits[lang] += len(words & vocab)
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    (top, n1), (_, n2) = ranked[0], ranked[1]
    return top if n1 >= 2 and n1 >= 2 * n2 else None


def _folder_names(work) -> list[str]:
    from autobook.catalog import Catalog

    cat = Catalog(work.catalog)
    try:
        roots = cat.kv_get("roots", [])
    finally:
        cat.close()
    names = [Path(r).name for r in roots]
    for f in folder_name_counts(work):
        names.extend(f.rsplit(" (", 1)[0].split("/"))
    return names


def offer_folders(qs: list[Question], work, lang: str) -> None:
    """Photos sorted into folders ("01 Anreise", "02 Strand" …): chapters by those folders, preselected."""
    from autobook.catalog import Catalog
    from autobook.cluster.folders import folder_groups

    cat = Catalog(work.catalog)
    try:
        groups = folder_groups(cat.joined(), cat.kv_get("roots", []))
    finally:
        cat.close()
    q = next((q for q in qs if q.maps_to == "chaptering"), None)
    if not groups or q is None:
        return
    names = ", ".join(g["label"] for g in groups[:3]) + (" …" if len(groups) > 3 else "")
    q.options = [Option(value="folder", label=tr(lang, "chapters.folder", names=names)),
                 *[o for o in q.options if o.value != "folder"]]
    q.default = "folder"


def build_questions(summary: dict, work=None, ai=None, ui_language: str = "en") -> list[Question]:
    """Favourite questions (if marks were found) + AI-tailored or default questions."""
    import re

    from autobook.ai.favourites import check_markers

    cands = (summary.get("favourites") or {}).get("candidates") or []
    folders = folder_name_counts(work) if (cands and work is not None) else []
    fav = favourite_questions(summary, check_markers(ai, cands, folders), ui_language)
    rest = ai_questions(ai, work, summary, ui_language) if ai is not None else default_questions(summary, ui_language)
    if work is not None:
        offer_folders(rest, work, ui_language)
    lang = guess_language(_folder_names(work)) if work is not None else None
    for q in rest:  # the folder names know the family's language better than the interface setting
        if q.maps_to == "language" and lang and any(o.value == lang for o in q.options):
            q.default = lang
            for o in q.options:
                o.label = re.sub(r"\s*\((recommended|empfohlen)\)", "", o.label)
            q.why = tr(ui_language, "language.why")
    return fav + rest


FOCUS_SHARE = {"family": 0.8, "balanced": 0.5, "places": 0.3, "any": None}


QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["single", "multi", "text"]},
                    "options": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}, "label": {"type": "string"}},
                        "required": ["value", "label"], "additionalProperties": False}},
                    "default": {"type": "string"},
                    "maps_to": {"type": "string", "enum": list(ALLOWED)},
                    "why": {"type": "string"},
                },
                "required": ["id", "text", "kind", "options", "default", "maps_to", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def validate_ai_questions(raw: list[dict], summary: dict) -> list[Question]:
    event_ids = {e["id"] for e in summary.get("events", [])}
    out: list[Question] = []
    seen: set[str] = set()
    for q in raw:
        field = q.get("maps_to")
        if field not in ALLOWED or field in seen:
            continue
        allowed = event_ids if field == "main_event_ids" else ALLOWED[field]
        opts = [Option(**o) for o in q.get("options", []) if allowed is None or o.get("value") in allowed]
        if allowed is not None and not opts:
            continue
        kind = "multi" if field in ("main_event_ids", "priorities") else q.get("kind", "single")
        if field == "book_title":
            kind = "text"
        default: str | list[str] = q.get("default", "")
        if kind == "multi":
            default = [d.strip() for d in str(default).split(",") if allowed is None or d.strip() in allowed]
        elif allowed is not None and default not in allowed:
            default = opts[0].value
        out.append(Question(id=q.get("id") or field, text=q["text"], kind=kind, options=opts,
                            default=default, maps_to=field, why=q.get("why", "")))
        seen.add(field)
    return out[:6]


def ai_questions(ai, work, summary: dict, ui_language: str = "en") -> list[Question]:
    """AI-generated questions; falls back to defaults on any problem."""
    from autobook.ai.client import (
        AIRequest,
        estimate_image_tokens,
        image_block,
        load_prompt,
    )
    from autobook.thumbs import resized_for_ai

    version, prompt = load_prompt("questions")
    slim = {k: summary[k] for k in ("photos", "date_from", "date_to", "days", "gps_share", "countries")
            if k in summary}
    slim["countries"] = [country_name(c) for c in slim.get("countries", [])]
    slim["events"] = [{"id": e["id"], "place": e.get("place"), "start": e["start"][:16],
                       "end": e["end"][:16], "photos": e["n"]} for e in summary.get("events", [])]
    allowed = {k: (v if k != "main_event_ids" else [e["id"] for e in summary.get("events", [])])
               for k, v in ALLOWED.items()}
    text = (f"Interface language: {ui_language}.\nSummary:\n{json.dumps(slim, ensure_ascii=False)}\n"
            f"Allowed maps_to fields and option values (null = free):\n{json.dumps(allowed)}\n"
            "For multi-select defaults, separate values with commas.")
    content = [{"type": "text", "text": text}]
    samples = [pid for e in summary.get("events", []) for pid in e.get("sample", [])[:1]][:10]
    for i, pid in enumerate(samples):
        content.append({"type": "text", "text": f"Sample photo {i + 1}"})
        content.append(image_block(resized_for_ai(work.root / "thumbs", pid, 512)))
    req = AIRequest("questions", prompt, content, QUESTIONS_SCHEMA, kind="questions",
                    image_tokens=estimate_image_tokens(512, 384) * len(samples))
    res = ai.run([req], batch=False)["questions"]
    if res.error or not res.data:
        return default_questions(summary, ui_language)
    qs = validate_ai_questions(res.data.get("questions", []), summary)
    return qs if len(qs) >= 2 else default_questions(summary, ui_language)


def apply_answers(questions: list[Question], answers: dict, summary: dict, fav_counter=None) -> Brief:
    """answers: question id -> value (missing = the question's default).

    fav_counter(markers) -> number of favourite photos; without it the scan summary's count is used.
    """
    brief = Brief()
    qa = []
    for q in questions:
        val = answers.get(q.id, q.default)
        qa.append(QA(id=q.id, question=q.text, answer=val))
        f = q.maps_to
        if f == "focus":
            brief.people_share = FOCUS_SHARE.get(str(val), brief.people_share)
        elif f == "target_photos":
            try:
                brief.target_photos = int(val)
            except (TypeError, ValueError):
                pass
        elif f in ("main_event_ids", "priorities"):
            setattr(brief, f, list(val) if isinstance(val, list) else [v for v in str(val).split(",") if v])
        elif f == "book_title":
            brief.book_title = str(val).strip() or None
        elif f in ("scope", "chaptering", "title_tone", "language"):
            setattr(brief, f, val)
        elif f == "favourite_markers":
            marks = []
            for v in (val if isinstance(val, list) else [x for x in str(val).split(",") if x]):
                kind, _, rest = str(v).partition(":")
                pattern, _, lvl = rest.rpartition(":")
                marks.append({"kind": kind, "pattern": pattern, "level": int(lvl or 1)})
            brief.favourite_markers = marks
        elif f == "favourites_mode" and val in ("fill", "only", "best", "ignore"):
            brief.favourites_mode = val
    brief.qa = qa
    if not any(q.maps_to == "target_photos" for q in questions):
        brief.target_photos = suggested_target(summary, brief.scope)
    if brief.favourite_markers and brief.favourites_mode != "ignore":
        n_fav = (fav_counter(brief.favourite_markers) if fav_counter
                 else (summary.get("favourites") or {}).get("any", 0))
        if brief.favourites_mode == "only":
            brief.target_photos = n_fav
        elif brief.favourites_mode == "best":
            brief.target_photos = max(1, min(brief.target_photos, n_fav))
        else:
            brief.target_photos = max(brief.target_photos, round(n_fav * 1.15))
    brief.target_pages = max(24, round(brief.target_photos / brief.photos_per_page))
    return brief


def wizard(questions: list[Question], ask) -> dict:
    """Terminal wizard. `ask(prompt, default) -> str` is injected (typer.prompt in the CLI)."""
    answers: dict = {}
    for q in questions:
        if q.kind in ("single", "multi") and q.options:
            lines = [q.text] + [f"  {i + 1}) {o.label}" for i, o in enumerate(q.options)]
            default_vals = q.default if isinstance(q.default, list) else [q.default]
            default_idx = ",".join(str(i + 1) for i, o in enumerate(q.options) if o.value in default_vals)
            raw = ask("\n".join(lines) + ("\n(comma-separated)" if q.kind == "multi" else ""), default_idx or "1")
            picks = []
            for part in str(raw).split(","):
                part = part.strip()
                if part.isdigit() and 1 <= int(part) <= len(q.options):
                    picks.append(q.options[int(part) - 1].value)
            answers[q.id] = picks if q.kind == "multi" else (picks[0] if picks else q.default)
        else:
            answers[q.id] = ask(q.text, str(q.default or ""))
    return answers

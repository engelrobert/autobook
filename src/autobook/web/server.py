"""Local web UI: book library, new book from a folder, questions and settings, AI run, review
with page previews, export and print file.

Binds to 127.0.0.1 only and requires a random one-time token (passed in the URL once, then kept
in a cookie), so other local users or web pages cannot drive it. A book is a work directory in the
library folder (default ~/autobook); other work directories can be added with `autobook ui --add`.
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import string
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from autobook.brief.models import Brief, Question
from autobook.brief.questions import (
    apply_answers,
    build_questions,
    favourite_counter,
    suggested_target,
)
from autobook.catalog import Catalog
from autobook.cluster.folders import clean_label
from autobook.discover.favourites import FOLDER_WORDS
from autobook.discover.walk import IMAGE_EXTS
from autobook.export.profile import load_profile
from autobook.plan.models import BookPlan
from autobook.settings import Work
from autobook.thumbs import MID_SIZE, ensure_thumb, preview_of

PID = re.compile(r"^p[0-9a-f]{15}$")
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
COOKIE = "autobook_token"
REGISTRY = "books.json"  # in the library: extra work directories outside it
UPDATE_EVERY_S = 6 * 3600  # how often the running app looks for a new version
RELEASE = "release.json"  # in a work dir: the last print-file check (written by export.release.release)
QUESTIONS_META = "questions_meta.json"  # language of the saved questions, and whether the AI wrote them
STATIC = re.compile(r"^(?:[a-z0-9_-]+/){0,2}[a-z0-9_.-]+\.(js|css|json|woff2|svg)$")
MEDIA = {"js": "text/javascript", "css": "text/css", "json": "application/json", "woff2": "font/woff2",
         "svg": "image/svg+xml"}
THUMB_SIZES = (320, MID_SIZE, 1024)
DATE_NAME = re.compile(r"^\s*(\d{4})[-_.]?(\d{2})(?:[-_.]?(\d{2}))?"
                       r"(?:\s*(?:bis|to|–|-)\s*(?:(\d{4})[-_.]?)?(\d{2})(?:[-_.]?(\d{2}))?)?")
WALK_LIMIT = 6000  # files looked at per folder tile (photo count and previews)


def _read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def register_book(library: Path, work: Path) -> None:
    """Remember a work directory outside the library (e.g. made with the CLI)."""
    reg = library / REGISTRY
    data = _read_json(reg, {"extra": []})
    path = str(Path(work).resolve())
    if path not in data["extra"]:
        data["extra"].append(path)
        reg.write_text(json.dumps(data, indent=1), encoding="utf-8")


def list_books(library: Path) -> dict[str, Work]:
    """Book name -> work dir: folders in the library with a catalog, plus registered ones."""
    books: dict[str, Work] = {}
    for d in sorted(library.iterdir()) if library.exists() else []:
        if d.is_dir() and ((d / "catalog.sqlite").exists() or (d / ".autobook-book").exists()):
            books[d.name] = Work(d)
    for path in _read_json(library / REGISTRY, {"extra": []})["extra"]:
        p = Path(path)
        if p.is_dir() and (p / "catalog.sqlite").exists():
            name, k = p.name, 2
            while name in books and books[name].root != p:
                name, k = f"{p.name} ({k})", k + 1
            books[name] = Work(p)
    return books


def _mtime(p: Path) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


def freshness(work: Work) -> dict:
    """Whether the pages and the print file still match the plan (both are made again on demand)."""
    manifest = _read_json(work.manifest)
    if not manifest:
        return {"layout_outdated": True, "print_outdated": True}
    from autobook.export.fotobuch import template_current

    project = Path(manifest["project_dir"])
    pdf, final = _mtime(project / "book.pdf"), _mtime(project / "book_final.pdf")
    layout_outdated = not pdf or _mtime(work.plan) > pdf or not template_current(project)
    return {"layout_outdated": layout_outdated,
            "print_outdated": layout_outdated or not final or final < pdf or not (work.root / RELEASE).exists()}


def book_info(name: str, work: Work) -> dict:
    summary = _read_json(work.summary)
    plan = _read_json(work.plan)
    manifest = _read_json(work.manifest)
    fresh = freshness(work)
    printed = manifest and not fresh["print_outdated"]
    state = ("printed" if printed else "exported" if manifest else "planned" if plan
             else "scanned" if summary else "new")
    cat = Catalog(work.catalog) if work.catalog.exists() else None
    roots = cat.kv_get("roots", []) if cat else []
    if cat:
        cat.close()
    profile = (plan or {}).get("book", {}).get("profile") or _read_json(work.brief, {}).get("profile") or "square-21"
    in_book = sum(len(c.get("photos", [])) + (1 if (c.get("divider") or {}).get("photo") else 0)
                  for c in (plan or {}).get("chapters", []))
    return {"name": name, "state": state, "folder": roots[0] if roots else None, "profile": profile,
            "in_book": in_book,
            "layout_outdated": fresh["layout_outdated"], "print_outdated": fresh["print_outdated"],
            "photos": (summary or {}).get("photos"), "date_from": (summary or {}).get("date_from"),
            "date_to": (summary or {}).get("date_to"),
            "title": (plan or {}).get("book", {}).get("title"),
            "cover": (plan or {}).get("book", {}).get("cover_photo") or _sample_photo(summary),
            "updated": max((p.stat().st_mtime for p in (work.plan, work.summary, work.brief) if p.exists()),
                           default=0)}


def _sample_photo(summary: dict | None) -> str | None:
    """A photo for the card of a book without a plan yet: the first sample of its biggest event."""
    events = [e for e in (summary or {}).get("events", []) if e.get("sample")]
    return max(events, key=lambda e: e["n"])["sample"][0] if events else None


def fs_roots() -> list[dict]:
    home = Path.home()
    roots = [home] + [home / n for n in ("Pictures", "Bilder", "Photos", "Fotos") if (home / n).is_dir()]
    if sys.platform == "win32":
        roots += [Path(f"{d}:\\") for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
    return [{"name": str(r), "path": str(r)} for r in roots]


def name_dates(name: str) -> tuple[str | None, str | None]:
    """'2025-08-30 bis 09-06 Urlaub' -> ('2025-08-30', '2025-09-06'); '2026-07 Portugal' -> ('2026-07', '2026-07')."""
    m = DATE_NAME.match(name)
    if not m:
        return None, None
    y, mo, d, y2, mo2, d2 = m.groups()
    if not (1 <= int(mo) <= 12):
        return None, None
    start = f"{y}-{mo}" + (f"-{d}" if d else "")
    end = f"{y2 or y}-{mo2}" + (f"-{d2}" if d2 else "") if mo2 and 1 <= int(mo2) <= 12 else start
    return start, end


def folder_card(d: Path) -> dict:
    """What a folder tile shows: photos below it (bounded), 3 previews, favourite marks, name and dates."""
    photos, previews, favs, subs, seen, more = 0, [], False, 0, 0, False
    for root, dirnames, files in os.walk(d):
        dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
        if root == str(d):
            subs = len(dirnames)
        if any(FOLDER_WORDS.search(n) for n in dirnames):
            favs = True
        for f in sorted(files):
            seen += 1
            if Path(f).suffix.lower() in IMAGE_EXTS:
                photos += 1
                favs = favs or f.startswith("+")
                if len(previews) < 3 and photos % 7 == 1:  # spread out a little instead of a burst
                    previews.append(str(Path(root) / f))
        if seen > WALK_LIMIT:
            more = True
            break
    start, end = name_dates(d.name)
    return {"name": d.name, "label": clean_label(d.name) or d.name, "path": str(d), "photos": photos,
            "more": more, "subfolders": subs, "previews": previews, "favourites": favs,
            "date_from": start, "date_to": end}


def list_dir(path: Path) -> dict:
    """Sub-folders as tiles for the folder picker, and the photos directly inside this folder."""
    dirs = []
    for d in sorted(path.iterdir(), key=lambda x: x.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            dirs.append(folder_card(d))
        except OSError:
            continue
    here = sum(1 for f in path.iterdir() if f.suffix.lower() in IMAGE_EXTS)
    return {"path": str(path), "name": path.name or str(path), "label": clean_label(path.name) or path.name,
            "parent": str(path.parent) if path.parent != path else None, "photos_here": here, "dirs": dirs}


def create_app(library: Path, token: str, *, ai_factory: Callable | None = None, updater=None,
               key_check: Callable[[str], str] | None = None) -> FastAPI:
    """ai_factory(work, effort) -> AIClient, or None while no API key is set; ai_factory.ready()
    (optional) says whether one is set. updater: autobook.update.Updater (None: no updates).
    key_check(key) -> "ok" | "invalid" | "unchecked" (default: ask OpenAI)."""
    from autobook.update import Updater

    updater = updater or Updater()
    key_check = key_check or check_key
    library = Path(library)
    library.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="autobook", docs_url=None, redoc_url=None, openapi_url=None)
    locks: dict[str, threading.Lock] = {}
    jobs: dict[str, dict] = {}
    static = resources.files("autobook.web").joinpath("static")

    def auth(request: Request) -> None:
        got = (request.cookies.get(COOKIE) or request.headers.get("x-autobook-token")
               or request.query_params.get("t"))
        if not got or not secrets.compare_digest(got, token):
            raise HTTPException(403, "missing or wrong token: open the URL printed by `autobook ui`")

    def book(name: str) -> Work:
        books = list_books(library)
        if name not in books:
            raise HTTPException(404, f"no book called {name!r}")
        return books[name]

    def lock(name: str) -> threading.Lock:
        return locks.setdefault(name, threading.Lock())

    def start_job(fn: Callable, book_name: str | None = None, kind: str = "") -> dict:
        """Run fn(progress) in a thread; the page polls GET /api/job/{id} (browsers give up on
        responses after ~5 minutes, and AI runs can take longer). A page opened later finds the
        book's running job in its state and follows it."""
        jid = secrets.token_hex(8)
        job = jobs[jid] = {"id": jid, "kind": kind, "status": "running", "stage": "", "done": 0, "total": 0,
                           "book": book_name}

        def progress(stage: str, done: int, total: int) -> None:
            job.update(stage=stage, done=done, total=total)

        def run() -> None:
            try:
                job.update(status="done", result=fn(progress))
            except Exception as e:
                job.update(status="error", error=f"{type(e).__name__}: {e}")
        threading.Thread(target=run, daemon=True, name=f"autobook-job-{jid}").start()
        return {"job": jid}

    def running_job(name: str) -> dict | None:
        found = [j for j in jobs.values() if j["book"] == name and j["status"] == "running"]
        return {k: found[-1][k] for k in ("id", "kind", "stage", "done", "total")} if found else None

    def ai_ready() -> bool:
        """An AI client can be made now (a key entered in the page counts at once)."""
        return ai_factory is not None and bool(getattr(ai_factory, "ready", lambda: True)())

    def ai_for(work: Work, effort: str):
        return ai_factory(work, effort) if ai_ready() else None

    def check_now_and_then():  # at start, then every 6 hours (autobook may run for days in the tray)
        while True:
            updater.check()
            time.sleep(UPDATE_EVERY_S)

    if updater.enabled:
        threading.Thread(target=check_now_and_then, daemon=True, name="autobook-update-check").start()

    # ── app: alive check, OpenAI key, updates ────────────────────────────
    @app.get("/api/ping")
    def ping():
        """No token needed: a second start of the app finds this one and only opens the browser."""
        return {"app": "autobook"}

    @app.get("/api/key")
    def key_status(_=Depends(auth)):
        from autobook.settings import get_api_key

        return {"set": bool(get_api_key()), "from_env": bool(os.environ.get("OPENAI_API_KEY")),
                "ai_available": ai_ready()}

    @app.post("/api/key")
    def key_set(body: dict = Body(...), _=Depends(auth)):
        from autobook.settings import set_api_key

        key = str(body.get("key", "")).strip()
        if not key.startswith("sk-") or len(key) < 20 or any(c.isspace() for c in key):
            raise HTTPException(422, "not an OpenAI key")
        verdict = key_check(key)
        if verdict == "invalid":
            raise HTTPException(422, "OpenAI did not accept this key")
        set_api_key(key)
        return {"ok": True, "checked": verdict == "ok", "ai_available": ai_ready()}

    @app.delete("/api/key")
    def key_delete(_=Depends(auth)):
        from autobook.settings import delete_api_key

        delete_api_key()
        return {"ok": True, "ai_available": ai_ready()}

    @app.get("/api/update")
    def update_status(check: bool = False, _=Depends(auth)):
        return updater.check() if check else updater.status()

    @app.post("/api/update/apply")
    def update_apply(_=Depends(auth)):
        """Download the new version, then restart into it. Never while autobook is working."""
        if not updater.status()["available"]:
            raise HTTPException(409, "no update available")
        if any(j["status"] == "running" for j in jobs.values()):
            raise HTTPException(409, "autobook is busy; try again when it is done")

        def make(progress):
            updater.download(lambda done, total: progress("download", done, total))
            # answer the page first, then end this process: the new version starts in its place
            threading.Timer(1.5, updater.apply_and_restart).start()
            return {"restarting": True, "version": updater.status()["version"]}
        return start_job(make, None, "update")

    # ── page and static files ────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if request.query_params.get("t") != token and request.cookies.get(COOKIE) != token:
            return HTMLResponse("<p>Open the link printed by <code>autobook ui</code>.</p>", 403)
        blob = json.dumps(_global(), ensure_ascii=False).replace("</", "<\\/")  # cannot close the tag
        html = static.joinpath("index.html").read_text(encoding="utf-8").replace(
            '<script type="module" src="/static/js/main.js">',
            f'<script id="initial-state" type="application/json">{blob}</script>\n'
            '<script type="module" src="/static/js/main.js">')
        resp = HTMLResponse(html)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict")
        return resp

    @app.get("/static/{path:path}")
    def static_file(path: str, _=Depends(auth)):
        m = STATIC.match(path)
        if not m:
            raise HTTPException(404)
        f = static.joinpath(*path.split("/"))
        if not f.is_file():
            raise HTTPException(404)
        return Response(f.read_bytes(), media_type=MEDIA[m.group(1)], headers={"Cache-Control": "no-cache"})

    @app.get("/thumb/{name}/{size}/{pid}.jpg")
    def thumb(name: str, size: int, pid: str, _=Depends(auth)):
        if size not in THUMB_SIZES or not PID.match(pid):
            raise HTTPException(404)
        p = ensure_thumb(book(name).root / "thumbs", pid, size)
        if p is None:
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    def _global() -> dict:
        from autobook.export.profile import available_profiles, load_profile
        from autobook.export.style import FRAME_MM, SWATCHES, ink_for

        return {"books": [{**book_info(n, w), "job": running_job(n)} for n, w in list_books(library).items()],
                "library": str(library), "ai_available": ai_ready(),
                "version": updater.status()["current"], "updates": updater.enabled or bool(updater.status()["feed"]),
                "swatches": [{"key": k, "bg": v, "ink": ink_for(v)} for k, v in SWATCHES.items()],
                "frames": list(FRAME_MM),
                "profiles": [{"name": n, "description": prof.description, "printer": prof.printer,
                              "width": prof.page_width_mm, "height": prof.page_height_mm,
                              "min_pages": int(prof.print.get("page_count_min", 0) or 0),
                              "max_pages": int(prof.print.get("page_count_max", 0) or 0),
                              "max_file_mb": float(prof.print.get("max_file_mb", 0) or 0),
                              "separate_files": prof.separate_files, "cover_typed": prof.cover_typed,
                              "calculator": prof.cover.get("calculator"),
                              "bindings": prof.cover.get("bindings", ["hardcover"]),
                              "margins": {b: prof.cover.get(f"margin_{b}_mm", prof.bleed_mm)
                                          for b in ("hardcover", "softcover")}}
                             for n, prof in ((n, load_profile(n)) for n in available_profiles())]}

    @app.get("/api/job/{jid}")
    def job_status(jid: str, _=Depends(auth)):
        if jid not in jobs:
            raise HTTPException(404, "unknown job")
        return jobs[jid]

    # ── library ──────────────────────────────────────────────────────────
    @app.get("/api/books")
    def books_list(_=Depends(auth)):
        return _global()

    @app.get("/api/fs")
    def fs(path: str | None = None, _=Depends(auth)):
        if not path:
            return {"path": None, "parent": None, "roots": fs_roots(), "dirs": []}
        p = Path(path).expanduser()
        if not p.is_dir():
            raise HTTPException(404, "not a folder")
        return list_dir(p)

    @app.get("/api/fs/preview")
    def fs_preview(path: str, _=Depends(auth)):
        import autobook.discover.metadata  # noqa: F401 - registers the HEIC opener

        p = Path(path)
        if p.suffix.lower() not in IMAGE_EXTS or not p.is_file():
            raise HTTPException(404)
        try:
            out = preview_of(p, library / ".cache" / "folder-previews")
        except Exception as e:
            raise HTTPException(404, "no preview") from e
        return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/books")
    def new_book(body: dict = Body(...), _=Depends(auth)):
        from autobook.pipeline.scan import run_scan

        folder = Path(str(body.get("folder", ""))).expanduser()
        if not folder.is_dir():
            raise HTTPException(422, "pick a folder with photos")
        base = BAD_NAME.sub("", str(body.get("name") or folder.name)).strip(" .") or "book"
        name, k = base, 2
        existing = list_books(library)
        while name in existing or (library / name).exists():
            name, k = f"{base} ({k})", k + 1
        work = Work(library / name).ensure()
        (work.root / ".autobook-book").write_text(str(folder), encoding="utf-8")

        def scan(progress):
            with lock(name):
                return run_scan([folder], work, progress=progress)
        return {**start_job(scan, name, "scan"), "book": name}

    # ── one book ─────────────────────────────────────────────────────────
    @app.get("/api/books/{name}/state")
    def state(name: str, _=Depends(auth)):
        from autobook.discover.favourites import level

        work = book(name)
        brief = _read_json(work.brief, {})
        markers = brief.get("favourite_markers", []) if brief.get("favourites_mode") != "ignore" else []
        cat = Catalog(work.catalog)
        try:
            blur = set(cat.kv_get("blur", []))
            roots = cat.kv_get("roots", [])
            photos = {p["id"]: {"taken": p["taken"], "event": p["event_id"], "dup": p["dup_group"],
                                "local": p["local_score"], "blur": p["id"] in blur,
                                "w": p["width"], "h": p["height"], "fav": level(p["path"], roots, markers)}
                      for p in cat.joined()}
            cost = cat.total_cost()
        finally:
            cat.close()
        manifest = _read_json(work.manifest)
        summary = _read_json(work.summary)
        info = book_info(name, work)
        suggest = ({"highlights": suggested_target(summary, "highlights"),
                    "complete": suggested_target(summary, "complete"),
                    "favourites": (summary.get("favourites") or {}).get("any", 0)} if summary else {})
        return {"name": name, "info": info, "summary": summary, "suggest": suggest,
                "questions": _read_json(work.questions), "brief": _read_json(work.brief),
                "questions_meta": _read_json(work.root / QUESTIONS_META, {}),
                "plan": _read_json(work.plan), "photos": photos, "cost": round(cost, 4),
                "gold": _read_json(work.root / "gold.json", {"favourites": []}),
                "exported": manifest is not None, "ai_available": ai_ready(),
                "job": running_job(name), "layout_outdated": info["layout_outdated"],
                "print_outdated": info["print_outdated"],
                "release": None if info["print_outdated"] else _read_json(work.root / RELEASE),
                "focus": _focus_points(work), **_sizes(work)}

    def _focus_points(work: Work) -> dict:
        """Where the faces are in the book's photos (for the cut editor's starting point)."""
        from autobook.ai.focus import book_photo_ids, known_focus

        if not work.plan.exists():
            return {}
        cat = Catalog(work.catalog)
        try:
            known = known_focus(cat)
        finally:
            cat.close()
        return {i: known[i] for i in book_photo_ids(BookPlan.load(work.plan)) if i in known}

    def _brief(work: Work) -> Brief | None:
        data = _read_json(work.brief)
        return Brief.model_validate(data) if data else None

    def _sizes(work: Work) -> dict:
        """How far the photo-count slider goes, and the counts that fill whole pages."""
        from autobook.pipeline.resize import estimate_pages, favourite_ids, fit_points, reserve

        if not work.plan.exists():
            return {"capacity": 0, "least": 0, "fit_points": [], "pages_estimate": 0}
        plan, brief = BookPlan.load(work.plan), _brief(work)
        count = sum(len(c.photos) for c in plan.chapters)
        capacity = count + sum(len(v) for v in reserve(work, plan, brief).values())
        favs = favourite_ids(work, plan, brief)  # must-haves and photos put in by hand never go
        least = sum(max(1, sum(1 for p in c.photos if p.by_user or p.id in favs)) for c in plan.chapters)
        prof = load_profile(plan.book.profile)
        most = int(prof.print.get("page_count_max", 0) or 0)
        if most:  # no more than the printer's thickest book holds
            capacity = min(capacity, max(count, round((most - 1 - len(plan.chapters)) * plan.book.photos_per_page)))
        return {"capacity": capacity, "least": min(least, count),
                "fit_points": fit_points(plan, capacity, int(prof.print.get("page_count_min", 0) or 0),
                                         int(prof.print.get("page_count_step", 1) or 1),
                                         int(prof.print.get("page_count_max", 0) or 0)),
                "pages_estimate": estimate_pages([len(c.photos) for c in plan.chapters], plan.book.photos_per_page,
                                                 int(prof.print.get("page_count_min", 0) or 0),
                                                 int(prof.print.get("page_count_step", 1) or 1))}

    @app.post("/api/books/{name}/resize")
    def resize_plan(name: str, body: dict = Body(...), _=Depends(auth)):
        """More or fewer photos (whole book: target; one chapter: chapter + delta). Nothing is
        saved: the page shows the result and saves it as a normal change (with Undo)."""
        from autobook.pipeline.resize import estimate_pages, favourite_ids, reserve, resize

        work = book(name)
        if not work.plan.exists():
            raise HTTPException(409, "no selection yet")
        plan, brief = BookPlan.load(work.plan), _brief(work)
        chapter = body.get("chapter")
        if chapter is not None and chapter not in {c.id for c in plan.chapters}:
            raise HTTPException(422, "unknown chapter")
        new, added, removed = resize(plan, reserve(work, plan, brief), favourite_ids(work, plan, brief),
                                     target=body.get("target"), chapter=chapter, delta=int(body.get("delta", 0)))
        counts = [len(c.photos) for c in new.chapters]
        prof = load_profile(new.book.profile)
        pages = estimate_pages(counts, new.book.photos_per_page, int(prof.print.get("page_count_min", 0) or 0),
                               int(prof.print.get("page_count_step", 1) or 1))
        return {"plan": new.model_dump(mode="json", by_alias=True), "added": added, "removed": removed,
                "count": sum(counts), "pages": pages}

    @app.post("/api/books/{name}/questions")
    def make_questions(name: str, body: dict = Body(default={}), _=Depends(auth)):
        work = book(name)
        if not work.summary.exists():
            raise HTTPException(409, "the folder has not been scanned yet")
        summary = _read_json(work.summary)
        lang = body.get("ui_language", "en")
        use_ai = bool(body.get("ai"))

        def make(progress):
            with lock(name):
                qs = build_questions(summary, work, ai_for(work, body.get("effort", "high")) if use_ai else None, lang)
            work.questions.write_text(json.dumps([q.model_dump() for q in qs], indent=2, ensure_ascii=False),
                                      encoding="utf-8")
            (work.root / QUESTIONS_META).write_text(json.dumps({"lang": lang, "ai": use_ai}), encoding="utf-8")
            return [q.model_dump() for q in qs]
        return start_job(make, name, "questions")

    @app.post("/api/books/{name}/settings")
    def settings(name: str, body: dict = Body(...), _=Depends(auth)):
        """Answers + run settings -> brief.json (no AI run yet)."""
        work = book(name)
        summary = _read_json(work.summary)
        qs = [Question(**q) for q in _read_json(work.questions, [])]
        brief = apply_answers(qs, body.get("answers", {}), summary, favourite_counter(work))
        s = body.get("settings", {})
        if s.get("profile"):
            brief.profile = str(s["profile"])
        if s.get("per_page"):
            brief.photos_per_page = float(s["per_page"])
        if s.get("photos") and brief.favourites_mode != "only":
            brief.target_from_user = True
            brief.target_photos = max(int(s["photos"]), 1)
            if brief.favourites_mode == "best" and brief.favourite_markers:
                brief.target_photos = min(brief.target_photos, favourite_counter(work)(brief.favourite_markers))
        brief.subtitles = bool(s.get("subtitles", brief.subtitles))
        brief.use_ai = bool(s.get("ai", brief.use_ai)) and ai_ready()
        if s.get("effort"):
            brief.effort = str(s["effort"])
        brief.target_pages = max(24, round(brief.target_photos / brief.photos_per_page))
        work.brief.write_text(brief.dump(), encoding="utf-8")
        return json.loads(brief.dump())

    @app.get("/api/books/{name}/estimate")
    def estimate(name: str, _=Depends(auth)):
        from autobook.ai.client import estimate_book

        work = book(name)
        summary = _read_json(work.summary, {})
        brief = Brief.model_validate_json(work.brief.read_text(encoding="utf-8")) if work.brief.exists() else Brief()
        chapters = max(1, len({e["days"][0] for e in summary.get("events", []) if e.get("days")}))
        n = summary.get("photos", 0)
        if brief.favourites_mode == "best" and brief.favourite_markers:
            n = favourite_counter(work)(brief.favourite_markers)  # only the favourites are looked at
        est = estimate_book(n, chapters, brief.effort,
                            score=brief.favourites_mode != "only" or not brief.favourite_markers)
        prof = load_profile(brief.profile)
        photo_pages = max(1, round(brief.target_photos / brief.photos_per_page))
        return {**est, "photos": n, "pages": photo_pages, "chapters": chapters,
                "min_pages": int(prof.print.get("page_count_min", 0) or 0),
                "target_photos": brief.target_photos, "ai": brief.use_ai}

    @app.post("/api/books/{name}/select")
    def select(name: str, _=Depends(auth)):
        from autobook.pipeline.select import build_plan

        work = book(name)
        brief = Brief.model_validate_json(work.brief.read_text(encoding="utf-8"))

        def make(progress):
            with lock(name):
                ai = ai_for(work, brief.effort) if brief.use_ai else None
                plan = build_plan(work, brief, ai=ai, profile=brief.profile, batch=False, progress=progress)
                plan.save(work.plan, work.plan_history)
            return json.loads(work.plan.read_text(encoding="utf-8"))
        return start_job(make, name, "select")

    @app.post("/api/books/{name}/plan")
    def save_plan(name: str, body: dict = Body(...), _=Depends(auth)):
        work = book(name)
        cat = Catalog(work.catalog)
        try:  # photos added in the UI arrive as {id, ...}; fill the rest from the catalog
            for ch in body.get("chapters", []):
                for p in ch.get("photos", []):
                    row = cat.photo(p.get("id", ""))
                    if row is None:
                        raise HTTPException(422, f"unknown photo id {p.get('id')!r}")
                    if not p.get("path"):
                        p.update(path=row["path"], ts=row["taken"], ts_source=row["ts_source"],
                                 width=row["width"], height=row["height"],
                                 gps=[row["lat"], row["lon"]] if row["lat"] is not None else None)
        finally:
            cat.close()
        try:
            plan = BookPlan.model_validate(body)
        except Exception as e:
            raise HTTPException(422, f"invalid plan: {e}") from e
        with lock(name):
            plan.save(work.plan, work.plan_history)
        return {"ok": True, "photos": plan.photo_count()}

    @app.post("/api/books/{name}/export")
    def do_export(name: str, body: dict = Body(default={}), _=Depends(auth)):
        from autobook.export.fotobuch import ExportConflict, export

        work = book(name)

        def make(progress):
            from autobook.pipeline.resize import favourite_ids, reserve, resize

            filled = 0  # photos added instead of blank pages: the page reloads the plan
            with lock(name):
                try:
                    plan = BookPlan.load(work.plan)
                    _focus(work, plan, progress)
                    r = export(plan, work, profile=None, fresh=bool(body.get("fresh")), progress=progress)
                    brief = _brief(work)
                    for _ in range(3):  # too short for the printer: more photos instead of blank pages
                        if not (r.blank_pages and plan.book.auto_fit):
                            break
                        target = sum(len(c.photos) for c in plan.chapters) + \
                            math.ceil(r.blank_pages * plan.book.photos_per_page)
                        plan, added, _ = resize(plan, reserve(work, plan, brief), favourite_ids(work, plan, brief),
                                                target=target)
                        if not added:  # nothing left to add: the blank pages stay
                            break
                        plan.save(work.plan, work.plan_history)
                        r = export(plan, work, profile=None, progress=progress)
                        filled += len(added)
                    if filled:
                        r.warnings.insert(0, f"{filled} photos added instead of blank pages")
                except ExportConflict as e:
                    return {"ok": False, "conflict": True, "error": str(e)}
            return {"ok": True, "pages": r.pages, "mode": r.mode, "warnings": r.warnings, "pdf": str(r.pdf),
                    "blank_pages": r.blank_pages, "filled": filled}
        return start_job(make, name, "export")

    def _focus(work: Work, plan: BookPlan, progress) -> None:
        """Where the faces are, for photos the AI hasn't looked at yet (added later, older books).
        Only for books made with AI; without it the photos are simply centred."""
        from autobook.ai.focus import book_photo_ids, find_focus, missing_focus

        brief = _brief(work)
        if not ai_ready() or plan.selection != "ai" or (brief is not None and not brief.use_ai):
            return
        cat = Catalog(work.catalog)
        try:
            todo = missing_focus(cat, book_photo_ids(plan))
        finally:
            cat.close()
        if not todo:
            return
        progress("focus", 0, 1)
        try:
            find_focus(ai_for(work, brief.effort if brief else "high"), work, todo, batch=False)
        except Exception:  # noqa: BLE001 - the layout goes on with centred photos
            pass

    @app.post("/api/books/{name}/release")
    def do_release(name: str, _=Depends(auth)):
        from autobook.export.release import release

        work = book(name)

        def make(progress):
            progress("release", 0, 1)
            with lock(name):
                return release(work).as_dict()
        return start_job(make, name, "release")

    def _project(work: Work) -> Path:
        m = _read_json(work.manifest)
        if not m:
            raise HTTPException(404, "not exported yet")
        return Path(m["project_dir"])

    @app.get("/api/books/{name}/pdf")
    def pdf(name: str, final: int = 0, _=Depends(auth)):
        p = _project(book(name)) / ("book_final.pdf" if final else "book.pdf")
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="application/pdf", filename=f"{name}{'-druck' if final else ''}.pdf")

    def _print_files(work: Work) -> list[Path]:
        return [Path(f) for f in (_read_json(work.root / RELEASE) or {}).get("files", [])]

    @app.get("/api/books/{name}/print-file/{i}")
    def print_file(name: str, i: int, _=Depends(auth)):
        """One of the files to upload to the printer (the inner pages, the cover)."""
        files = _print_files(book(name))
        if not 0 <= i < len(files) or not files[i].exists():
            raise HTTPException(404)
        f = files[i]
        return FileResponse(f, media_type="application/pdf" if f.suffix == ".pdf" else "image/jpeg", filename=f.name)

    @app.post("/api/books/{name}/open-folder")
    def open_folder(name: str, _=Depends(auth)):
        work = book(name)
        files = [f for f in _print_files(work) if f.exists()]
        folder = files[0].parent if files else _project(work)  # where the files to upload are
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - local folder of this book
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        return {"ok": True, "folder": str(folder)}

    @app.get("/api/books/{name}/pages")
    def pages(name: str, _=Depends(auth)):
        from urllib.parse import quote

        from autobook.web.previews import page_index, page_sizes, render_pages

        work = book(name)
        with lock(name):
            folder = render_pages(work)
        if folder is None:
            return {"pages": []}
        idx = page_index(work, page_sizes(folder))
        stamp = folder.name
        for i, p in enumerate(idx):
            p["image"] = f"/api/books/{quote(name)}/page/{stamp}/{i}.jpg"
        return {"pages": idx}

    @app.get("/api/books/{name}/page/{stamp}/{i}.jpg")
    def page_image(name: str, stamp: str, i: int, _=Depends(auth)):
        from autobook.web.previews import PREVIEWS

        if not stamp.isdigit():
            raise HTTPException(404)
        p = book(name).root / PREVIEWS / stamp / f"page-{i:03d}.jpg"
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/books/{name}/siblings/{pid}")
    def sibling_shots(name: str, pid: str, _=Depends(auth)):
        from autobook.web.previews import siblings

        if not PID.match(pid):
            raise HTTPException(404)
        return {"siblings": siblings(book(name), pid)}

    @app.post("/api/books/{name}/gold")
    def save_gold(name: str, body: dict = Body(...), _=Depends(auth)):
        work = book(name)
        favs = [f for f in body.get("favourites", []) if isinstance(f, str) and PID.match(f)]
        (work.root / "gold.json").write_text(json.dumps({"favourites": sorted(set(favs))}, indent=1),
                                             encoding="utf-8")
        return {"ok": True, "count": len(set(favs))}

    return app


def check_key(key: str) -> str:
    """Ask OpenAI whether the key works (listing models costs nothing): ok | invalid | unchecked (offline)."""
    try:
        import openai

        openai.OpenAI(api_key=key, timeout=15, max_retries=0).models.list()
        return "ok"
    except Exception as e:  # noqa: BLE001
        import openai

        if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return "invalid"
        return "unchecked"


def free_port(preferred: int = 0) -> int:
    """preferred if it is free, otherwise any free port."""
    import socket

    for p in ([preferred] if preferred else []) + [0]:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return s.getsockname()[1]
    raise OSError("no free port")


def make_server(library: Path, token: str, port: int, *, ai_factory=None, updater=None, console: bool = True):
    """The app as a uvicorn server object (run() blocks; should_exit = True stops it).
    console=False: no console logging of uvicorn's own (the desktop app has no console; its
    messages go to the log file set up by the app)."""
    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    config = uvicorn.Config(create_app(library, token, ai_factory=ai_factory, updater=updater),
                            host="127.0.0.1", port=port, log_level="warning",
                            log_config=LOGGING_CONFIG if console else None)
    return uvicorn.Server(config)


def serve(library: Path, *, port: int = 0, open_browser: bool = True, ai_factory=None,
          open_book: str | None = None) -> None:
    import webbrowser
    from urllib.parse import quote

    token = secrets.token_urlsafe(18)
    port = port or free_port()
    url = f"http://127.0.0.1:{port}/?t={token}" + (f"#/b/{quote(open_book)}/selection" if open_book else "")
    print(f"autobook: {url}\n(Ctrl+C to stop)", flush=True)  # visible at once, also when piped
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    make_server(library, token, port, ai_factory=ai_factory).run()

"""autobook command line. Typical run:

    autobook scan ~/Pictures/Portugal2026 --work portugal
    autobook review --work portugal            # questions + review in the browser
    autobook release --work portugal           # 300 DPI PDF + preflight

or non-interactively: autobook all ~/Pictures/Portugal2026 --work portugal --offline
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from autobook.settings import Work

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
WorkOpt = typer.Option(Path("autobook-work"), "--work", "-w", help="Work directory of this book")
ModelOpt = typer.Option("gpt-6-luna", "--model", help="OpenAI model for all AI tasks")
EffortOpt = typer.Option("high", "--effort", help="Reasoning effort: none, low, medium, high, xhigh, max")


def _progress():
    last = {"stage": None}

    def cb(stage: str, done: int, total: int) -> None:
        if stage != last["stage"] and last["stage"] is not None:
            sys.stderr.write("\n")
        last["stage"] = stage
        pct = f"{100 * done // total:3d}%" if total else ""
        sys.stderr.write(f"\r  {stage:<10} {done}/{total} {pct}   ")
        sys.stderr.flush()
    return cb


def _ai_factory(work: Work, max_cost: float, model: str = "gpt-6-luna", effort: str = "high",
                parallel: int = 8):
    from autobook.ai.client import EFFORT_FACTOR
    from autobook.settings import get_api_key

    if effort not in EFFORT_FACTOR:
        raise typer.BadParameter(f"--effort must be one of {', '.join(EFFORT_FACTOR)}")
    key = get_api_key()
    if not key:
        return None

    def make():
        from autobook.ai.client import AIClient
        from autobook.catalog import Catalog

        return AIClient(Catalog(work.catalog), api_key=key, max_cost=max_cost, model=model, effort=effort,
                        concurrency=parallel)
    return make


def _confirm_upload(work: Work, yes: bool) -> None:
    summary = json.loads(work.summary.read_text(encoding="utf-8"))
    if yes:
        return
    typer.confirm(f"Up to {summary['photos']} thumbnails (EXIF stripped) will be sent to the OpenAI API "
                  "for scoring and titles. Continue?", abort=True)


@app.command()
def scan(roots: list[Path] = typer.Argument(..., exists=True, help="Folders (searched recursively)"),
         work: Path = WorkOpt):
    """Find photos, read EXIF/GPS, make thumbnails, detect blur/duplicates, cluster events."""
    from autobook.pipeline.scan import run_scan

    s = run_scan(roots, Work(work), progress=_progress())
    sys.stderr.write("\n")
    typer.echo(f"{s['photos']} photos, {s['days']} days, {len(s['events'])} events, "
               f"{s['duplicates_collapsed']} near-duplicates, {s['blurry']} blurry, {s['errors']} skipped")
    for e in s["events"]:
        typer.echo(f"  {e['id']}  {e['start'][:16]}  {e['n']:4d} photos  {e.get('place') or '-'}")


@app.command()
def brief(work: Path = WorkOpt, ai: bool = typer.Option(False, help="Let the AI tailor the questions"),
          defaults: bool = typer.Option(False, "--defaults", help="Accept all defaults without asking"),
          lang: str = typer.Option("en", help="Interface language for the questions"),
          max_cost: float = typer.Option(5.0), yes: bool = typer.Option(False, "--yes", "-y"),
          model: str = ModelOpt, effort: str = EffortOpt,
          photos: int = typer.Option(None, "--photos", help="Number of photos in the book (default: from the answers)"),
          per_page: float = typer.Option(None, "--per-page", help="Average photos per page (default 4.5)")):
    """Answer 3-6 questions in the terminal; writes brief.json."""
    from autobook.brief.questions import (
        apply_answers,
        build_questions,
        favourite_counter,
        wizard,
    )

    w = Work(work)
    summary = json.loads(w.summary.read_text(encoding="utf-8"))
    factory = _ai_factory(w, max_cost, model, effort) if ai else None
    if ai and factory is None:
        typer.echo("No API key found (set OPENAI_API_KEY or run `autobook set-key`); using default questions.")
    if factory is not None:
        _confirm_upload(w, yes)
    qs = build_questions(summary, w, factory() if factory else None, lang)
    w.questions.write_text(json.dumps([q.model_dump() for q in qs], indent=2, ensure_ascii=False), encoding="utf-8")
    answers = {} if defaults else wizard(qs, lambda text, default: typer.prompt(text, default=default))
    b = apply_answers(qs, answers, summary, favourite_counter(w))
    if per_page:
        b.photos_per_page = per_page
    if photos:
        b.target_photos = photos
    if photos or per_page:
        b.target_pages = max(24, round(b.target_photos / b.photos_per_page))
    w.brief.write_text(b.dump(), encoding="utf-8")
    typer.echo(f"brief.json: {b.scope}, ~{b.target_photos} photos, chapters by {b.chaptering}, "
               f"language {b.language}, ~{b.photos_per_page:g} per page, people "
               f"{'no target' if b.people_share is None else f'{b.people_share:.0%}'}, "
               f"subtitles {'on' if b.subtitles else 'off'}"
               + (f", favourites: {b.favourites_mode}" if b.favourite_markers else ""))


@app.command("select")
def select_cmd(work: Path = WorkOpt, offline: bool = typer.Option(False, help="Local scoring only, no API"),
               profile: str = typer.Option(None, help="Printer profile (default: the book's setting, see `autobook profiles`)"),
               batch: bool = typer.Option(None, "--batch/--no-batch", help="OpenAI Batch API (50% cheaper, can take hours); default: only for runs estimated above $2"),
               max_cost: float = typer.Option(5.0, help="Stop before AI spend would exceed this (USD)"),
               yes: bool = typer.Option(False, "--yes", "-y"), model: str = ModelOpt, effort: str = EffortOpt,
               parallel: int = typer.Option(8, help="AI calls at the same time (lower it if OpenAI reports rate limits)")):
    """Choose photos, chapters, weights and titles; writes book_plan.json."""
    from autobook.brief.models import Brief
    from autobook.pipeline.select import build_plan

    w = Work(work)
    b = Brief.model_validate_json(w.brief.read_text(encoding="utf-8")) if w.brief.exists() else Brief()
    factory = None if offline else _ai_factory(w, max_cost, model, effort, parallel)
    if not offline and factory is None:
        typer.echo("No API key: using local scoring (pass --offline to silence this).")
    if factory is not None:
        _confirm_upload(w, yes)
    plan = build_plan(w, b, ai=factory() if factory else None, profile=profile or b.profile, batch=batch,
                      progress=_progress())
    sys.stderr.write("\n")
    plan.save(w.plan, w.plan_history)
    _print_plan(plan)
    for msg in plan.warnings:
        typer.echo(f"  ! {msg}", err=True)
    if factory is not None:
        from autobook.catalog import Catalog

        typer.echo(f"AI spend so far for this book: ${Catalog(w.catalog).total_cost():.3f}")


def _print_plan(plan) -> None:
    typer.echo(f"“{plan.book.title}” · {plan.photo_count()} photos · {len(plan.chapters)} chapters "
               f"({plan.selection} selection)")
    for c in plan.chapters:
        typer.echo(f"  {c.id}  {c.title:<28} {c.date_from}  {len(c.photos):3d} photos  opener: {c.divider.style}")


@app.command("export")
def export_cmd(work: Path = WorkOpt, profile: str = typer.Option(None, help="Override the plan's profile"),
               fresh: bool = typer.Option(False, help="Rebuild the Fotobuch project from scratch"),
               guides: bool = typer.Option(None, "--guides/--no-guides",
                                           help="PREVIEW watermark and bleed/margin lines in the preview PDF "
                                                "(default: off for new projects, unchanged on updates)")):
    """Create or update the Fotobuch project and its preview PDF."""
    from autobook.export.fotobuch import ExportConflict, export
    from autobook.plan.models import BookPlan

    w = Work(work)
    try:
        r = export(BookPlan.load(w.plan), w, profile, fresh=fresh, progress=_progress(), guides=guides)
    except ExportConflict as e:
        typer.echo(f"\n{e}", err=True)
        raise typer.Exit(2) from e
    sys.stderr.write("\n")
    typer.echo(f"{r.mode}: {r.pages} inner pages\npreview: {r.pdf}\nproject: {r.project_dir} "
               f"(open it in fotobuch-gui to fine-tune)")
    for wmsg in r.warnings:
        typer.echo(f"  ! {wmsg}")


@app.command("fix-dividers")
def fix_dividers_cmd(work: Path = WorkOpt):
    """Put chapter openers back in front of their chapters after `fotobuch rebuild --all`."""
    from autobook.export.fotobuch import fix_dividers

    for wmsg in fix_dividers(Work(work)):
        typer.echo(f"  ! {wmsg}")
    typer.echo("dividers re-anchored, preview rebuilt")


@app.command()
def release(work: Path = WorkOpt):
    """300 DPI PDF via `fotobuch build release`, then a preflight check (exit 1 on failure)."""
    from autobook.export.release import release as do_release

    rep = do_release(Work(work))
    typer.echo(rep.render())
    raise typer.Exit(0 if rep.ok else 1)


def _ui_ai_factory(max_cost: float, model: str):
    """For the browser: an AI client per book and effort, or None while no API key is set. The key
    is read each time, so one entered in the page counts at once (make.ready says whether there is one)."""
    from autobook.settings import get_api_key

    def make(work: Work, effort: str):
        from autobook.ai.client import AIClient
        from autobook.catalog import Catalog

        key = get_api_key()
        if not key:
            return None
        return AIClient(Catalog(work.catalog), api_key=key, max_cost=max_cost, model=model, effort=effort)
    make.ready = lambda: bool(get_api_key())
    return make


@app.command()
def ui(library: Path = typer.Option(None, help="Folder for your books (default ~/autobook)"),
       add: list[Path] = typer.Option(None, "--add", help="Also show this existing work directory (repeatable)"),
       port: int = typer.Option(0), no_browser: bool = typer.Option(False),
       max_cost: float = typer.Option(5.0, help="AI spending cap per run (USD)"), model: str = ModelOpt):
    """Browser app: your books, new book from a folder, settings, AI run, review, print file."""
    from autobook.settings import library_dir
    from autobook.web.server import register_book, serve

    lib = Path(library).expanduser() if library else library_dir()
    lib.mkdir(parents=True, exist_ok=True)
    for w in add or []:
        register_book(lib, Work(w).root)
    serve(lib, port=port, open_browser=not no_browser, ai_factory=_ui_ai_factory(max_cost, model))


@app.command()
def review(work: Path = WorkOpt, port: int = typer.Option(0), no_browser: bool = typer.Option(False),
           max_cost: float = typer.Option(5.0), model: str = ModelOpt):
    """Open the browser app on this book's review page."""
    from autobook.settings import library_dir
    from autobook.web.server import list_books, register_book, serve

    w = Work(work)
    lib = library_dir()
    if w.root.parent != lib:
        register_book(lib, w.root)
    name = next((n for n, b in list_books(lib).items() if b.root == w.root), w.root.name)
    serve(lib, port=port, open_browser=not no_browser, ai_factory=_ui_ai_factory(max_cost, model), open_book=name)


@app.command("all")
def all_cmd(roots: list[Path] = typer.Argument(..., exists=True), work: Path = WorkOpt,
            offline: bool = typer.Option(False), profile: str = typer.Option("square-21"),
            max_cost: float = typer.Option(5.0), yes: bool = typer.Option(False, "--yes", "-y"),
            model: str = ModelOpt, effort: str = EffortOpt):
    """scan → default brief → select → export, without questions."""
    scan(roots, work)
    brief(work, ai=False, defaults=True, lang="en", max_cost=max_cost, yes=yes, model=model, effort=effort,
          photos=None, per_page=None)
    select_cmd(work, offline=offline, profile=profile, batch=None, max_cost=max_cost, yes=yes,
               model=model, effort=effort, parallel=8)
    export_cmd(work, profile=profile, fresh=True, guides=None)


@app.command("eval")
def eval_cmd(work: Path = WorkOpt, gold: Path = typer.Option(None, help="Default: <work>/gold.json"),
             variants: str = typer.Option("random,local,ai"), max_cost: float = typer.Option(5.0),
             model: str = ModelOpt, effort: str = EffortOpt,
             favourites: bool = typer.Option(False, "--favourites",
                                             help="Use your own favourite marks (Favoriten folders, '+') as gold set")):
    """Compare selection variants against your gold set of favourites."""
    from autobook.eval.metrics import run_eval

    w = Work(work)
    typer.echo(run_eval(w, gold or (w.root / "gold.json"), [v.strip() for v in variants.split(",")],
                        ai_factory=_ai_factory(w, max_cost, model, effort), favourites=favourites))


@app.command()
def cost(work: Path = WorkOpt):
    """AI spend for this book, by model and task."""
    from autobook.catalog import Catalog

    cat = Catalog(Work(work).catalog)
    for r in cat.cost_summary():
        secs = (f"  {r['avg_seconds']:>6.1f} s avg, {r['max_seconds']:.1f} s max"
                if r["avg_seconds"] is not None else "  (batch: no per-call time)")
        typer.echo(f"  {r['model']:<16} {r['kind']:<10} {r['calls']:4d} calls  {r['input_tokens']:>9} in  "
                   f"{r['output_tokens']:>9} out (of which {r['reasoning_tokens']:>9} reasoning)  "
                   f"${r['usd']:.4f}{secs}")
    typer.echo(f"total ${cat.total_cost():.4f}")


@app.command("ai-calibrate")
def ai_calibrate(work: Path = WorkOpt, size: int = typer.Option(768), n: int = typer.Option(10),
                 model: str = ModelOpt):
    """Measure real input tokens per thumbnail with OpenAI's token-counting endpoint."""
    from autobook.ai.client import (
        AIClient,
        AIRequest,
        estimate_image_tokens,
        image_block,
        price,
    )
    from autobook.catalog import Catalog
    from autobook.settings import get_api_key
    from autobook.thumbs import resized_for_ai

    w = Work(work)
    key = get_api_key()
    if not key:
        typer.echo("No API key.", err=True)
        raise typer.Exit(1)
    cat = Catalog(w.catalog)
    ids = [p["id"] for p in cat.joined()][:n]
    client = AIClient(cat, api_key=key, model=model)
    base = client.count_tokens(AIRequest("c", "x", [{"type": "text", "text": "x"}], {}, "calibrate"))
    content = []
    for pid in ids:
        content.append(image_block(resized_for_ai(w.root / "thumbs", pid, size)))
    total = client.count_tokens(AIRequest("c", "x", [*content, {"type": "text", "text": "x"}], {}, "calibrate"))
    per = (total - base) / max(1, len(ids))
    typer.echo(f"{len(ids)} thumbnails at {size}px: {per:.0f} input tokens each "
               f"(autobook estimates {estimate_image_tokens(size, round(size * 0.75))} for a 4:3 thumbnail); "
               f"input ≈ ${price(model, round(per * 1000), 0):.3f} per 1000 photos with {model}, "
               "plus output and reasoning tokens (see `autobook cost` after a run)")


@app.command("install-shortcut")
def install_shortcut():
    """Add autobook to the desktop's app menu (Linux; Windows: Start menu, untested)."""
    import shutil
    from importlib import resources

    exe = shutil.which("autobook") or str(Path(sys.argv[0]).resolve())
    icon_src = resources.files("autobook.resources").joinpath("autobook.svg").read_bytes()
    if sys.platform == "win32":
        import os
        import subprocess

        start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        lnk = start / "autobook.lnk"
        ps = (f"$s=(New-Object -COM WScript.Shell).CreateShortcut('{lnk}');"
              f"$s.TargetPath='{exe}';$s.Arguments='ui';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        typer.echo(f"Start menu shortcut: {lnk}")
        return
    share = Path.home() / ".local" / "share"
    icon = share / "icons" / "hicolor" / "scalable" / "apps" / "autobook.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(icon_src)
    desktop = share / "applications" / "autobook.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("\n".join([
        "[Desktop Entry]", "Type=Application", "Name=autobook",
        "Comment=Photo books from your photo folders",
        f'Exec="{exe}" ui', "Icon=autobook",
        "Terminal=true",  # the window shows the address; closing it stops autobook
        "Categories=Graphics;Photography;", ""]), encoding="utf-8")
    desktop.chmod(0o755)
    typer.echo(f"App menu entry: {desktop}\n(starts `{exe} ui` in a terminal window; close it to stop autobook)")


@app.command("update-check")
def update_check():
    """Look for a new version of the installed Windows app; prints the result (an error included)."""
    import logging

    # Velopack reports through Python logging (which feed address, which channel): show it
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from autobook.update import Updater

    st = Updater().check()
    typer.echo(json.dumps(st, indent=2))
    if st.get("error"):
        raise typer.Exit(1)


@app.command()
def doctor():
    """Check the environment: Python, fotobuch, pdfimages (optional), API key."""
    import platform
    import shutil

    from autobook.export.fotobuch import Fotobuch, FotobuchError
    from autobook.settings import get_api_key

    typer.echo(f"python    {platform.python_version()} on {platform.system()}")
    try:
        v = Fotobuch().version()
        ok = v.startswith("0.3")
        typer.echo(f"fotobuch  {v}{'' if ok else '  (tested with 0.3.x; check the template still matches)'}")
    except FotobuchError as e:
        typer.echo(f"fotobuch  MISSING: {e}")
    typer.echo(f"pdfimages {'found' if shutil.which('pdfimages') else 'missing (optional, for the ppi check)'}")
    typer.echo(f"API key   {'found' if get_api_key() else 'missing (AI off; set OPENAI_API_KEY or run `autobook set-key`)'}")


@app.command("set-key")
def set_key():
    """Store the OpenAI API key in the OS keyring."""
    from autobook.settings import set_api_key

    set_api_key(typer.prompt("OpenAI API key", hide_input=True))
    typer.echo("stored in the OS keyring")


@app.command()
def profiles():
    """List built-in printer profiles."""
    from autobook.export.profile import available_profiles, load_profile

    for name in available_profiles():
        p = load_profile(name)
        typer.echo(f"  {name:<14} {p.description}")


@app.command("demo-photos")
def demo_photos(out: Path, scale: float = typer.Option(1.0), long_edge: int = typer.Option(3000)):
    """Generate a synthetic trip (EXIF time + GPS, bursts, blur) for trying things out."""
    from autobook.synth import generate_trip

    ps = generate_trip(out, scale=scale, long_edge=long_edge, heic_every=17)
    typer.echo(f"{len(ps)} photos in {out}")


@app.command()
def schemas(out: Path = typer.Argument(Path("docs/schemas"))):
    """Write JSON Schemas of book_plan.json and brief.json."""
    from autobook.plan.models import export_schemas

    export_schemas(out)
    typer.echo(f"written to {out}")


def main() -> None:
    """Entry point: known failures print one clear message instead of a traceback."""
    from autobook.ai.client import BudgetExceeded
    from autobook.export.fotobuch import ExportConflict, FotobuchError

    try:
        code = app(standalone_mode=False)  # click returns an Exit's code here instead of raising it
        if isinstance(code, int) and code:
            sys.exit(code)
    except (FotobuchError, ExportConflict, BudgetExceeded, FileNotFoundError) as e:
        typer.echo(f"error: {e}", err=True)
        sys.exit(2)
    except typer.Exit as e:
        sys.exit(e.exit_code)
    except typer.Abort:
        typer.echo("aborted", err=True)
        sys.exit(1)
    except Exception as e:  # usage errors (typer vendors its own click, so duck-type)
        if hasattr(e, "show") and hasattr(e, "exit_code"):
            e.show()
            sys.exit(e.exit_code)
        raise


if __name__ == "__main__":
    main()

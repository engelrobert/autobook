"""autobook as a desktop app (what the Windows installer starts): the server in the background, an
icon next to the clock (open, check for updates, quit), updates through Velopack.

A second start only opens the browser on the autobook that already runs. The address (port and
token) stays the same across restarts, so a tab left open picks up again after an update.
"""

from __future__ import annotations

import json
import locale
import logging
import os
import secrets
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

PORT = 8765  # the usual address; another free port when it is taken
TEXTS = {
    "de": {"open": "autobook öffnen", "check": "Nach Updates suchen", "quit": "Beenden",
           "current": "autobook ist auf dem neuesten Stand.", "update": "Eine neue Version ist da: {v}",
           "failed": "Die Suche nach Updates hat nicht geklappt: {e}"},
    "en": {"open": "Open autobook", "check": "Check for updates", "quit": "Quit",
           "current": "autobook is up to date.", "update": "A new version is available: {v}",
           "failed": "Checking for updates did not work: {e}"},
}
log = logging.getLogger("autobook.app")


def state_dir() -> Path:
    from platformdirs import user_data_dir

    d = Path(os.environ.get("AUTOBOOK_STATE") or user_data_dir("autobook", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def address(d: Path, preferred: int = PORT) -> tuple[int, str]:
    """The port and token of this user's autobook, kept in d so the address stays the same."""
    from autobook.web.server import free_port

    f = d / "address.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    token = data.get("token") or secrets.token_urlsafe(18)
    port = free_port(int(data.get("port") or preferred))
    f.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    return port, token


def running(d: Path) -> str | None:
    """The address of an autobook that already runs for this user, if one answers."""
    try:
        url = json.loads((d / "instance.json").read_text(encoding="utf-8"))["url"]
        with urllib.request.urlopen(url.split("/?")[0] + "/api/ping", timeout=2) as r:
            return url if json.load(r).get("app") == "autobook" else None
    except Exception:  # noqa: BLE001 - nothing there (or a stale file from a crash)
        return None


def lang() -> str:
    loc = (locale.getlocale()[0] or os.environ.get("LANG") or "").lower()
    return "de" if loc.startswith(("de", "german")) else "en"


def setup_logging() -> Path:
    """No console window on Windows: everything goes to a log file (errors point there)."""
    from platformdirs import user_log_dir

    d = Path(user_log_dir("autobook", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(d / "autobook.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    sys.excepthook = lambda *exc: log.error("uncaught", exc_info=exc)
    threading.excepthook = lambda a: log.error("uncaught in %s", a.thread, exc_info=(a.exc_type, a.exc_value,
                                                                                      a.exc_traceback))
    return d


def console_streams() -> None:
    """autobook.exe has no console window, so sys.stdout and sys.stderr are None, and libraries that
    write to them or ask isatty() fail (uvicorn did, on the first start). They get a place to go."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - for the whole run


def tray(url: str, server, updater, pystray=None) -> None:
    """The icon next to the clock; returns when "Quit" is chosen. Without pystray (e.g. a Linux
    desktop without tray support) it waits until the server stops (Ctrl+C)."""
    if pystray is None:
        try:
            import pystray
        except ImportError:
            while not server.should_exit:
                time.sleep(0.5)
            return
    from autobook.icon import draw_icon

    t = TEXTS[lang()]

    def open_app(icon=None, item=None):
        webbrowser.open(url)

    def check(icon, item):
        st = updater.check()
        if st["error"]:  # never "up to date" when the check did not work
            icon.notify(t["failed"].format(e=st["error"][:180]), "autobook")
        elif st["available"]:
            icon.notify(t["update"].format(v=st["version"]), "autobook")
            webbrowser.open(url)  # the page shows the update notice
        else:
            icon.notify(t["current"], "autobook")

    def quit_app(icon, item):
        server.should_exit = True
        icon.stop()

    items = [pystray.MenuItem(t["open"], open_app, default=True)]
    if updater.enabled or updater.status()["error"]:  # also when updates could not be set up: it says why
        items.append(pystray.MenuItem(t["check"], check))
    items.append(pystray.MenuItem(t["quit"], quit_app))
    pystray.Icon("autobook", draw_icon(64), "autobook", pystray.Menu(*items)).run()


def main(argv: list[str] | None = None) -> None:
    console_streams()
    try:  # Velopack's install/update hooks must run before anything else
        import velopack

        velopack.App().run()
    except ImportError:
        pass
    import multiprocessing

    multiprocessing.freeze_support()
    logs = setup_logging()
    d = state_dir()
    url = running(d)
    if url:  # already running: just show it
        webbrowser.open(url)
        return

    from autobook.ai.client import DEFAULT_MODEL
    from autobook.cli import _ui_ai_factory
    from autobook.settings import library_dir
    from autobook.update import Updater
    from autobook.web.server import make_server

    port, token = address(d)
    url = f"http://127.0.0.1:{port}/?t={token}"
    updater = Updater()
    server = make_server(library_dir(), token, port, ai_factory=_ui_ai_factory(5.0, DEFAULT_MODEL), updater=updater,
                         console=False)
    thread = threading.Thread(target=server.run, daemon=True, name="autobook-server")
    thread.start()
    for _ in range(200):  # up to 10 s
        if server.started:
            break
        time.sleep(0.05)
    log.info("autobook %s at %s (log: %s)", updater.status()["current"], url.split("/?")[0], logs)
    (d / "instance.json").write_text(json.dumps({"pid": os.getpid(), "url": url}), encoding="utf-8")
    try:
        webbrowser.open(url)
        tray(url, server, updater)
    finally:
        server.should_exit = True
        (d / "instance.json").unlink(missing_ok=True)
        thread.join(timeout=5)


if __name__ == "__main__":
    main()

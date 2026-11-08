"""Updates for the installed Windows app: Velopack, fed from the project's GitHub releases.

Only the app built by the release workflow knows where its updates come from (autobook/_build.py,
written during the build). A development checkout or a Linux install has no feed: everything here
then simply says "no update", and the page shows nothing about updates.
"""

from __future__ import annotations

import logging
import threading
import time
from importlib import metadata

log = logging.getLogger("autobook.update")


def current_version() -> str:
    try:
        return metadata.version("autobook")
    except metadata.PackageNotFoundError:
        return "0"


def feed_url() -> str | None:
    try:
        from autobook._build import UPDATE_FEED  # type: ignore[import-not-found]
    except ImportError:
        return None
    # with a trailing slash: Velopack joins the file name onto it like a URL, and without one
    # ".../latest/download" + "releases.win.json" becomes ".../latest/releases.win.json" (404)
    return UPDATE_FEED.rstrip("/") + "/" if UPDATE_FEED else None


def _get(obj, *names):
    """Velopack's Python objects: take the first attribute that exists (snake_case or PascalCase)."""
    for n in names:
        if obj is not None and hasattr(obj, n):
            return getattr(obj, n)
    return None


class Updater:
    """Check, download and apply updates. manager: a Velopack UpdateManager (or a stand-in in tests);
    None looks for one: only the installed app has it."""

    def __init__(self, manager=None, restart=None):
        self._manager = manager
        self._info = None
        # checked: a check has finished; error: why the last check (or the setup) failed
        self._status = {"enabled": False, "available": False, "version": None, "current": current_version(),
                        "checked": False, "checked_at": None, "error": None, "feed": feed_url()}
        self._lock = threading.Lock()
        self._restart = restart  # tests replace apply_updates_and_restart
        if manager is None and feed_url():
            try:
                import velopack

                # explicitly a plain web source: given a bare github.com address, Velopack takes it for a
                # repository and asks the GitHub API for ".../releases/latest/download" (404, 0.2.1–0.2.2)
                source = velopack.HttpSource(feed_url()) if hasattr(velopack, "HttpSource") else feed_url()
                self._manager = velopack.UpdateManager(source)
            except Exception as e:  # e.g. run from a build folder, not installed by Velopack
                log.warning("updates off: %s: %s", type(e).__name__, e, exc_info=True)
                self._status["error"] = f"{type(e).__name__}: {e}"
        self._status["enabled"] = self._manager is not None

    @property
    def enabled(self) -> bool:
        return self._manager is not None

    def status(self) -> dict:
        return dict(self._status)

    def check(self) -> dict:
        """Ask the feed for a newer version (network; a failure just means no update for now)."""
        if not self.enabled:
            return self.status()
        with self._lock:
            try:
                info = self._manager.check_for_updates()
            except Exception as e:  # offline, GitHub down, … : said as it is, never as "up to date"
                log.warning("update check failed (feed %s): %s: %s", feed_url(), type(e).__name__, e, exc_info=True)
                self._status.update(checked=True, checked_at=time.time(), error=f"{type(e).__name__}: {e}")
                return self.status()
            self._info = info
            target = _get(info, "target_full_release", "TargetFullRelease")
            version = _get(target, "version", "Version")
            self._status.update(available=info is not None, version=str(version) if version else None,
                                checked=True, checked_at=time.time(), error=None)
            log.info("update check: %s (running %s)", f"version {version} available" if info is not None
                     else "no newer version", self._status["current"])
        return self.status()

    def download(self, progress=lambda done, total: None) -> None:
        if self._info is None:
            raise RuntimeError("no update to download")

        def report(*args):  # Velopack reports percent (0..100); be lenient about the exact form
            try:
                progress(int(args[0]) if args else 0, 100)
            except (TypeError, ValueError):
                pass
        try:
            self._manager.download_updates(self._info, report)
        except TypeError:  # bindings without a progress callback
            self._manager.download_updates(self._info)
        progress(100, 100)

    def apply_and_restart(self) -> None:
        """Install the downloaded update and start the new version (this process ends)."""
        if self._restart is not None:
            self._restart(self._info)
            return
        self._manager.apply_updates_and_restart(self._info)

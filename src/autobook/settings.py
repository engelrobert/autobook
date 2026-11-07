"""Paths, API key lookup and the Fotobuch binary location."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir

APP = "autobook"
KEYRING_SERVICE = "autobook"
KEYRING_USER = "openai_api_key"


def cache_dir() -> Path:
    """Machine-wide cache (not per book)."""
    p = Path(os.environ.get("AUTOBOOK_CACHE", user_cache_dir(APP)))
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass(frozen=True)
class Work:
    """All files of one book live in one work directory."""

    root: Path

    def __post_init__(self) -> None:
        # absolute, because Fotobuch runs with other working directories
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def catalog(self) -> Path:
        return self.root / "catalog.sqlite"

    def thumbs(self, size: int) -> Path:
        return self.root / "thumbs" / str(size)

    @property
    def brief(self) -> Path:
        return self.root / "brief.json"

    @property
    def questions(self) -> Path:
        return self.root / "questions.json"

    @property
    def summary(self) -> Path:
        return self.root / "summary.json"

    @property
    def plan(self) -> Path:
        return self.root / "book_plan.json"

    @property
    def plan_history(self) -> Path:
        return self.root / "plan_history"

    @property
    def render(self) -> Path:
        return self.root / "render"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def project_parent(self) -> Path:
        return self.root / "fotobuch"

    @property
    def manifest(self) -> Path:
        return self.root / "export_manifest.json"

    def ensure(self) -> "Work":
        self.root.mkdir(parents=True, exist_ok=True)
        return self


def library_dir() -> Path:
    """Where `autobook ui` keeps books (one work directory each)."""
    p = Path(os.environ.get("AUTOBOOK_LIBRARY", Path.home() / "autobook")).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_api_key() -> str | None:
    """OPENAI_API_KEY wins; otherwise the OS keyring (set via `autobook set-key`)."""
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:  # no keyring backend (headless Linux) is fine
        return None


def set_api_key(key: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)


def delete_api_key() -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except PasswordDeleteError:  # there was none
        pass


def fotobuch_bin() -> str | None:
    env = os.environ.get("FOTOBUCH_BIN")
    if env:
        return env
    if getattr(sys, "frozen", False):  # the installed app brings its own (packaging/windows)
        bundled = Path(sys.executable).parent / "fotobuch" / ("fotobuch.exe" if os.name == "nt" else "fotobuch")
        if bundled.exists():
            return str(bundled)
    found = shutil.which("fotobuch")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / "fotobuch",
                 Path.home() / ".local" / "bin" / "fotobuch.exe"):
        if cand.exists():
            return str(cand)
    return None

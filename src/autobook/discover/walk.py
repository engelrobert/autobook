"""Recursive photo discovery. Folders only matter for finding files."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff"}
SKIP_DIRS = {"@eaDir", ".thumbnails", "__MACOSX", "$RECYCLE.BIN", "System Volume Information"}

# Windows file attributes for cloud-only placeholders (OneDrive, Dropbox, iCloud)
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ATTRS = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


@dataclass
class ScanResult:
    files: list[Path] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def long_path(p: Path) -> str:
    r"""Return a path string usable beyond 260 chars on Windows (\\?\ prefix)."""
    s = str(p)
    if sys.platform == "win32" and len(s) >= 240 and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + os.path.abspath(s)
    return s


def is_cloud_placeholder(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & CLOUD_ATTRS)


def scan(roots: list[Path], exclude: list[Path] | None = None) -> ScanResult:
    """Find image files under roots, skipping hidden/system dirs and cloud-only files.

    Never opens cloud placeholders: reading them would trigger a download.
    """
    result = ScanResult()
    excluded = {Path(e).resolve() for e in (exclude or [])}
    seen: set[str] = set()
    for root in roots:
        root = Path(root)
        if root.is_file():
            candidates = [(str(root.parent), [], [root.name])]
        else:
            candidates = os.walk(long_path(root.resolve()), followlinks=False)
        for dirpath, dirnames, filenames in candidates:
            dp = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames
                                 if not d.startswith(".") and d not in SKIP_DIRS
                                 and (dp / d).resolve() not in excluded)
            for name in sorted(filenames):
                if name.startswith("._"):  # macOS resource forks
                    continue
                p = dp / name
                if p.suffix.lower() not in IMAGE_EXTS:
                    continue
                try:
                    st = os.stat(long_path(p))
                except OSError as e:
                    result.skipped.append((p, f"stat failed: {e}"))
                    continue
                if is_cloud_placeholder(st):
                    result.skipped.append((p, "cloud-only placeholder (not downloaded)"))
                    continue
                if st.st_size < 1024:
                    result.skipped.append((p, "too small"))
                    continue
                key = os.path.normcase(str(p))
                if key in seen:
                    continue
                seen.add(key)
                result.files.append(p)
    return result

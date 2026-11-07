"""EXIF-rotated, metadata-free JPEG thumbnails cached by photo id."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from autobook.discover.walk import long_path

AI_SIZE = 1024
UI_SIZE = 320
SIZES = (AI_SIZE, UI_SIZE)


def thumb_path(thumbs_root: Path, pid: str, size: int) -> Path:
    return thumbs_root / str(size) / f"{pid}.jpg"


def open_upright(path: str | Path, draft: int | None = None) -> Image.Image:
    im = Image.open(long_path(Path(path)))
    if draft:
        im.draft("RGB", (draft, draft))  # fast JPEG downscale while decoding
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        im = im.convert("RGB")
    return im


def make_thumbs(src: str | Path, pid: str, thumbs_root: Path, sizes=SIZES) -> Image.Image:
    """Write all thumbnail sizes from one decode; returns the smallest for analysis."""
    targets = [(s, thumb_path(thumbs_root, pid, s)) for s in sorted(sizes, reverse=True)]
    smallest = None
    if all(p.exists() for _, p in targets):
        return Image.open(targets[-1][1]).convert("RGB")
    im = open_upright(src, draft=max(sizes))
    for size, p in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        im = im.copy()
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        im.save(p, "JPEG", quality=85, optimize=True)  # no exif= → EXIF/GPS stripped
        smallest = im
    return smallest


MID_SIZE = 640  # browser grid on high-density screens; made on first request from the AI size


def ensure_thumb(thumbs_root: Path, pid: str, size: int) -> Path | None:
    """The cached thumbnail at `size`; a missing in-between size is derived from the AI-size one."""
    p = thumb_path(thumbs_root, pid, size)
    if p.exists() or size in SIZES:
        return p if p.exists() else None
    big = thumb_path(thumbs_root, pid, AI_SIZE)
    if not big.exists():
        return None
    im = Image.open(big)
    im.thumbnail((size, size), Image.Resampling.LANCZOS)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    im.convert("RGB").save(tmp, "JPEG", quality=84, optimize=True)
    tmp.replace(p)  # two requests at once must not read a half-written file
    return p


def preview_of(src: Path, cache: Path, size: int = 360) -> Path:
    """A small upright JPEG of any photo (folder tiles before a scan), cached by path and mtime."""
    import hashlib

    st = src.stat()
    key = hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}|{size}".encode()).hexdigest()
    out = cache / f"{key}.jpg"
    if not out.exists():
        cache.mkdir(parents=True, exist_ok=True)
        im = open_upright(src, draft=size)
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        tmp = out.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=80)
        tmp.replace(out)
    return out


def resized_for_ai(thumbs_root: Path, pid: str, size: int) -> bytes:
    """JPEG bytes at the requested long edge (<= AI_SIZE), derived from the cached thumb."""
    p = thumb_path(thumbs_root, pid, AI_SIZE)
    if size >= AI_SIZE:
        return p.read_bytes()
    cache = thumb_path(thumbs_root, pid, size)
    if not cache.exists():
        im = Image.open(p).convert("RGB")
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        cache.parent.mkdir(parents=True, exist_ok=True)
        im.save(cache, "JPEG", quality=85)
    return cache.read_bytes()

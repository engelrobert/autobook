"""Cheap local quality signals computed on a thumbnail.

These feed the offline score, the blur/duplicate filters and the AI prompts.
They are relative signals: scoring.py normalises them per event.
"""

from __future__ import annotations

import math
import re

import imagehash
import numpy as np
from PIL import Image

SCREENSHOT_NAME = re.compile(r"screen ?shot|bildschirmfoto|screenshot|captura|scherm", re.I)


def sharpness(gray: np.ndarray, block: int = 32) -> float:
    """log of the 90th percentile of per-block Laplacian variance.

    Using the sharpest regions (not the whole frame) keeps photos with a sharp
    subject and a soft background from being marked as blurry.
    """
    g = gray.astype(np.float32)
    lap = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    h, w = lap.shape
    hb, wb = h // block, w // block
    if hb == 0 or wb == 0:
        return math.log1p(float(lap.var()))
    tiles = lap[: hb * block, : wb * block].reshape(hb, block, wb, block).var(axis=(1, 3))
    return math.log1p(float(np.percentile(tiles, 90)))


def colorfulness(rgb: np.ndarray) -> float:
    """Hasler & Süsstrunk colourfulness metric (0 = grey, ~100 = very colourful)."""
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    rg = r - g
    yb = 0.5 * (r + g) - b
    return float(math.hypot(rg.std(), yb.std()) + 0.3 * math.hypot(rg.mean(), yb.mean()))


def analyze_image(im: Image.Image, *, path: str = "", camera: str | None = None, fmt: str = "") -> dict:
    """Signals for one image. `im` should be an upright thumbnail (~320-1024 px)."""
    work = im.copy()
    work.thumbnail((512, 512), Image.Resampling.BILINEAR)
    rgb = np.asarray(work.convert("RGB"))
    gray = np.asarray(work.convert("L"))
    lum = gray.astype(np.float32) / 255.0
    w, h = work.size
    aspect = max(w, h) / max(1, min(w, h))
    screenshot = bool(SCREENSHOT_NAME.search(path)) or (
        not camera and fmt in ("PNG", "WEBP") and aspect >= 1.7)
    return {
        "sharpness": round(sharpness(gray), 4),
        "brightness": round(float(lum.mean()), 4),
        "clip_low": round(float((lum < 0.02).mean()), 4),
        "clip_high": round(float((lum > 0.98).mean()), 4),
        "colorfulness": round(colorfulness(rgb), 3),
        "phash": str(imagehash.phash(im)),
        "screenshot": int(screenshot),
    }


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")

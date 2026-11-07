"""Synthetic test photos: a fake trip with EXIF time, GPS, bursts, blur and edge cases.

Used by the test suite, the P0 spike and `autobook demo-photos`. The images are
simple drawn "landscapes" so perceptual hashes differ between scenes but stay
close within a burst.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ASPECTS = [(4, 3), (3, 4), (3, 2), (2, 3), (16, 9), (1, 1)]

# Stops of the fake trip: name, lat, lon, start offset in days, number of photos
DEFAULT_STOPS = [
    ("Lisboa", 38.7139, -9.1394, 0, 40),
    ("Sintra", 38.7975, -9.3881, 2, 25),
    ("Porto", 41.1496, -8.6110, 4, 45),
    ("Douro", 41.1621, -7.7869, 6, 30),
    ("Lagos", 37.1028, -8.6730, 9, 40),
]


@dataclass
class SynthPhoto:
    path: Path
    taken: datetime | None
    lat: float | None
    lon: float | None
    scene: int
    blurred: bool
    burst_of: Path | None


def _to_dms(value: float) -> tuple[float, float, float]:
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = round((value - d - m / 60) * 3600, 2)
    return (float(d), float(m), s)


def make_exif(taken: datetime | None, lat: float | None, lon: float | None,
              model: str = "SynthCam X1", orientation: int = 1) -> Image.Exif:
    exif = Image.Exif()
    exif[0x0110] = model  # Model
    exif[0x0112] = orientation  # Orientation
    if taken is not None:
        ifd = exif.get_ifd(0x8769)  # Exif IFD
        ifd[0x9003] = taken.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
        ifd[0x9011] = "+01:00"  # OffsetTimeOriginal
    if lat is not None and lon is not None:
        gps = exif.get_ifd(0x8825)
        gps[1] = "N" if lat >= 0 else "S"
        gps[2] = _to_dms(lat)
        gps[3] = "E" if lon >= 0 else "W"
        gps[4] = _to_dms(lon)
    return exif


def draw_scene(rng: random.Random, size: tuple[int, int], scene_seed: int,
               jitter: float = 0.0, label: str = "") -> Image.Image:
    """Draw a simple landscape. Same scene_seed + small jitter = near-duplicate."""
    srng = random.Random(scene_seed)
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    top = tuple(srng.randint(60, 200) for _ in range(3))
    bottom = tuple(srng.randint(20, 160) for _ in range(3))
    for y in range(0, h, 4):
        t = y / h
        c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        d.rectangle([0, y, w, y + 4], fill=c)
    dx = int(jitter * w * (rng.random() - 0.5))
    dy = int(jitter * h * (rng.random() - 0.5))
    horizon = int(h * srng.uniform(0.45, 0.7))
    pts = [(0, h)]
    for i in range(9):
        pts.append((int(w * i / 8) + dx, horizon + int(h * srng.uniform(-0.15, 0.1)) + dy))
    pts.append((w, h))
    d.polygon(pts, fill=tuple(srng.randint(20, 120) for _ in range(3)))
    for _ in range(srng.randint(2, 6)):
        r = int(min(w, h) * srng.uniform(0.04, 0.14))
        cx = int(w * srng.uniform(0.1, 0.9)) + dx
        cy = int(h * srng.uniform(0.1, 0.8)) + dy
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=tuple(srng.randint(0, 255) for _ in range(3)))
    if label:
        d.text((w // 20, h // 20), label, fill=(255, 255, 255))
    return img


def generate_trip(out_dir: Path, *, seed: int = 7, scale: float = 1.0, long_edge: int = 3000,
                  stops: list[tuple[str, float, float, int, int]] | None = None,
                  start: datetime = datetime(2026, 7, 1, 9, 0, 0),
                  heic_every: int = 0, no_exif_every: int = 23, blur_every: int = 11,
                  burst_every: int = 7, nested: bool = True) -> list[SynthPhoto]:
    """Create a fake trip under out_dir. Returns metadata for assertions in tests.

    scale multiplies the photo count per stop; long_edge sets resolution.
    Folder layout is deliberately messy (camera dumps, nested subfolders) because
    folders are only used for discovery.
    """
    rng = random.Random(seed)
    stops = stops or DEFAULT_STOPS
    photos: list[SynthPhoto] = []
    n = 0
    for si, (name, lat, lon, day, count) in enumerate(stops):
        count = max(1, round(count * scale))
        t = start + timedelta(days=day)
        sub = out_dir / (f"DCIM/{100 + si}CANON" if nested and si % 2 == 0 else f"phone/{name.lower()}")
        sub.mkdir(parents=True, exist_ok=True)
        prev: SynthPhoto | None = None
        for _ in range(count):
            n += 1
            is_burst = prev is not None and burst_every and n % burst_every == 0
            if is_burst:
                t = t + timedelta(seconds=1)
                scene = prev.scene
            else:
                t = t + timedelta(minutes=rng.randint(3, 50))
                if t.hour >= 21:  # night: jump to next morning
                    t = t.replace(hour=9, minute=rng.randint(0, 30)) + timedelta(days=1)
                scene = rng.randint(0, 10**9)
            a = rng.choice(ASPECTS)
            if a[0] >= a[1]:
                size = (long_edge, round(long_edge * a[1] / a[0]))
            else:
                size = (round(long_edge * a[0] / a[1]), long_edge)
            img = draw_scene(rng, size, scene, jitter=0.02 if is_burst else 0.0, label=f"{name} {n}")
            blurred = bool(blur_every) and n % blur_every == 0
            if blurred:
                img = img.filter(ImageFilter.GaussianBlur(radius=long_edge / 120))
            plat = lat + rng.uniform(-0.02, 0.02)
            plon = lon + rng.uniform(-0.02, 0.02)
            no_exif = bool(no_exif_every) and n % no_exif_every == 0
            heic = bool(heic_every) and n % heic_every == 0
            ext = ".heic" if heic else ".jpg"
            path = sub / f"IMG_{n:04d}{ext}"
            exif = None if no_exif else make_exif(t, plat, plon)
            if heic:
                import pillow_heif

                pillow_heif.register_heif_opener()
                img.save(path, format="HEIF", quality=80, **({"exif": exif.tobytes()} if exif else {}))
            else:
                img.save(path, format="JPEG", quality=88, **({"exif": exif} if exif else {}))
            if no_exif:
                ts = t.timestamp()
                import os

                os.utime(path, (ts, ts))
            p = SynthPhoto(path, None if no_exif else t, None if no_exif else plat,
                           None if no_exif else plon, scene, blurred, prev.path if is_burst else None)
            photos.append(p)
            prev = p
    return photos

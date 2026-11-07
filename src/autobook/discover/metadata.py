"""EXIF time, GPS, size and content hash for one photo."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import blake3
from PIL import Image, UnidentifiedImageError

from autobook.discover.walk import long_path

try:  # HEIC/HEIF support
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional at runtime
    pass

TAG_DATETIME = 0x0132
TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_ORIENTATION = 0x0112
IFD_EXIF = 0x8769
IFD_GPS = 0x8825
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_OFFSET_TIME_ORIGINAL = 0x9011


@dataclass
class PhotoMeta:
    id: str
    path: str
    size: int
    mtime: float
    width: int  # after EXIF orientation
    height: int
    taken: str  # ISO local time "YYYY-MM-DDTHH:MM:SS" (camera wall clock)
    tz: str | None  # "+01:00" when the camera recorded an offset
    ts_source: str  # "exif" | "mtime"
    lat: float | None
    lon: float | None
    camera: str | None
    orientation: int
    format: str

    def to_row(self) -> dict:
        return asdict(self)

    @property
    def taken_dt(self) -> datetime:
        return datetime.fromisoformat(self.taken)


def content_id(path: Path) -> str:
    """Stable photo id: BLAKE3 of the file content (survives moves and renames)."""
    h = blake3.blake3()
    with open(long_path(path), "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return "p" + h.hexdigest()[:15]


def _parse_exif_dt(value) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
        if dt.year < 1990:  # unset camera clocks report 1970/1980
            return None
        return dt
    return None


def _ratio(x) -> float:
    """EXIF rational (IFDRational, float or (num, den) tuple) -> float; ValueError if unusable."""
    try:
        return float(x)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return float(x[0]) / float(x[1])
        except (TypeError, ValueError, ZeroDivisionError, IndexError, KeyError) as e:
            raise ValueError(f"not a rational: {x!r}") from e


def _dms_to_deg(dms, ref) -> float | None:
    if isinstance(dms, (str, bytes)) or not hasattr(dms, "__len__") or len(dms) != 3:
        return None
    try:
        d, m, s = (_ratio(v) for v in dms)
    except ValueError:
        return None
    deg = d + m / 60 + s / 3600
    if str(ref).upper().startswith(("S", "W")):
        deg = -deg
    return deg


def parse_gps(gps: dict) -> tuple[float | None, float | None]:
    if not gps or 2 not in gps or 4 not in gps:
        return None, None
    lat = _dms_to_deg(gps.get(2), gps.get(1, "N"))
    lon = _dms_to_deg(gps.get(4), gps.get(3, "E"))
    if lat is None or lon is None:
        return None, None
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:  # (0,0) = no fix
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return round(lat, 6), round(lon, 6)


def read_metadata(path: Path, photo_id: str | None = None) -> PhotoMeta:
    """Read one file. Raises ValueError for files Pillow cannot open."""
    st = os.stat(long_path(path))
    try:
        with Image.open(long_path(path)) as im:
            w, h = im.size
            fmt = (im.format or path.suffix.lstrip(".")).upper()
            exif = im.getexif()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError(f"cannot open {path}: {e}") from e

    orientation = int(exif.get(TAG_ORIENTATION, 1) or 1)
    if orientation in (5, 6, 7, 8):
        w, h = h, w
    sub = exif.get_ifd(IFD_EXIF) if exif else {}
    dt = (_parse_exif_dt(sub.get(TAG_DATETIME_ORIGINAL))
          or _parse_exif_dt(sub.get(TAG_DATETIME_DIGITIZED))
          or _parse_exif_dt(exif.get(TAG_DATETIME)))
    tz = sub.get(TAG_OFFSET_TIME_ORIGINAL)
    tz = str(tz).strip() if tz else None
    if dt is not None:
        ts_source = "exif"
    else:
        dt = datetime.fromtimestamp(st.st_mtime).replace(microsecond=0)
        ts_source = "mtime"
        tz = None
    lat, lon = parse_gps(exif.get_ifd(IFD_GPS) if exif else {})
    make = str(exif.get(TAG_MAKE, "") or "").strip()
    model = str(exif.get(TAG_MODEL, "") or "").strip()
    camera = (model if make and model.lower().startswith(make.lower().split()[0])
              else f"{make} {model}".strip()) or None
    return PhotoMeta(
        id=photo_id or content_id(path),
        path=str(path),
        size=st.st_size,
        mtime=st.st_mtime,
        width=w,
        height=h,
        taken=dt.isoformat(timespec="seconds"),
        tz=tz,
        ts_source=ts_source,
        lat=lat,
        lon=lon,
        camera=camera,
        orientation=orientation,
        format=fmt,
    )

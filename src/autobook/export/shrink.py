"""A print PDF made to fit the printer's upload limit (epubli: 250 MB) by encoding its photos smaller.

Fotobuch stores the photos at nearly the highest JPEG quality with full colour detail (about 5 bits
per pixel). In print, quality 85 with the usual colour subsampling looks the same and takes less
than half. Only a file over the limit is touched, and then at the best quality that fits. The
photos keep their pixels and colour profile; only their JPEG encoding changes.
"""

from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor
from itertools import repeat
from pathlib import Path

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import StreamObject

# (JPEG quality, chroma subsampling: 0 = 4:4:4, 2 = 4:2:0), best first, and the share of the photo
# bytes each one left in a 68-page A4 book (262 photos, 217 MB)
STEPS = [(92, 0), (90, 2), (85, 2), (80, 2), (75, 2)]
EXPECTED = [0.82, 0.61, 0.46, 0.40, 0.35]
HEADROOM = 0.95  # stay a little under the limit (MB or MiB, and the printer's own counting)


class TooLarge(RuntimeError):
    def __init__(self, size: int, limit: int):
        super().__init__(f"{size / 1e6:.0f} MB even with the photos encoded smaller (limit {limit / 1e6:.0f} MB)")
        self.size, self.limit = size, limit


def _photos(writer: PdfWriter) -> list[StreamObject]:
    """The JPEG photos of all pages (each once), also inside form XObjects."""
    seen: set[int] = set()
    out: list[StreamObject] = []

    def walk(resources) -> None:
        for ref in ((resources or {}).get("/XObject") or {}).values():
            obj = ref.get_object()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            if obj.get("/Subtype") == "/Form":
                walk(obj.get("/Resources"))
            elif obj.get("/Subtype") == "/Image" and obj.get("/Filter") in ("/DCTDecode", ["/DCTDecode"]) \
                    and "/SMask" not in obj and "/Mask" not in obj:
                out.append(obj)

    for page in writer.pages:
        walk(page.get("/Resources"))
    return out


def _encode(raw: bytes, width: int, height: int, quality: int, subsampling: int) -> bytes | None:
    try:
        im = Image.open(io.BytesIO(raw))
        if im.mode not in ("RGB", "L") or im.size != (width, height):
            return None  # CMYK and odd ones stay as they are
        b = io.BytesIO()
        im.save(b, "JPEG", quality=quality, subsampling=subsampling, optimize=True)
        return b.getvalue()
    except (OSError, ValueError):
        return None


def fit(pdf: Path, limit_mb: float, progress=lambda done, total: None) -> int | None:
    """Make pdf at most limit_mb (with headroom). Returns the JPEG quality used, None when it was
    small enough already. Raises TooLarge when even the smallest step does not fit."""
    limit = int(limit_mb * 1e6 * HEADROOM)
    size = pdf.stat().st_size
    if size <= limit:
        return None
    writer = PdfWriter(clone_from=str(pdf))
    photos = _photos(writer)
    raws = [obj.get_data() for obj in photos]  # a DCT stream's data is the JPEG file itself
    widths, heights = [int(o["/Width"]) for o in photos], [int(o["/Height"]) for o in photos]
    photo_bytes = sum(len(r) for r in raws) or 1
    need = (limit - (size - photo_bytes)) / photo_bytes
    start = next((i for i, share in enumerate(EXPECTED) if share <= need * 0.97), len(STEPS) - 1)
    tmp = pdf.with_name(pdf.stem + ".smaller.pdf")
    smallest = size
    try:
        for n, (quality, sub) in enumerate(STEPS[start:], 1):
            progress(n, len(STEPS) - start)
            with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
                new = list(ex.map(_encode, raws, widths, heights, repeat(quality), repeat(sub)))
            for obj, raw, enc in zip(photos, raws, new):
                # StreamObject's own set_data: pypdf's encoded streams only take FlateDecode data,
                # but the JPEG bytes are exactly what a DCTDecode stream holds
                StreamObject.set_data(obj, enc if enc is not None and len(enc) < len(raw) else raw)
            with open(tmp, "wb") as f:
                writer.write(f)
            smallest = tmp.stat().st_size
            if smallest <= limit:
                os.replace(tmp, pdf)
                return quality
    finally:
        tmp.unlink(missing_ok=True)
    raise TooLarge(smallest, int(limit_mb * 1e6))

"""Release build (300 DPI via Fotobuch) and a preflight check against the printer profile."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from autobook.export.fotobuch import PROJECT, Fotobuch
from autobook.export.profile import Profile, load_profile
from autobook.settings import Work

MM = 72 / 25.4
DPI_LINE = re.compile(r"Page (\d+): (\S+) — (\d+) DPI")


RELEASE_FILE = "release.json"  # in the work dir: the last check of the print file


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    severity: str = "error"  # error | warning | info
    id: str = ""  # stable key for the browser app, which words the result in the reader's language
    values: dict = field(default_factory=dict)


@dataclass
class Preflight:
    pdf: Path
    checks: list[Check] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")

    def add(self, name: str, ok: bool, detail: str, severity: str = "error", id: str = "", **values) -> None:
        self.checks.append(Check(name, ok, detail, severity, id, values))

    def as_dict(self) -> dict:
        return {"ok": self.ok, "files": [str(f) for f in self.files], "report": self.render(),
                "checks": [{"id": c.id, "ok": c.ok, "severity": c.severity, "values": c.values}
                           for c in self.checks]}

    def render(self) -> str:
        lines = [f"Preflight for {self.pdf.name}: {'PASS' if self.ok else 'FAIL'}"]
        for c in self.checks:
            mark = "ok " if c.ok else ("!! " if c.severity == "error" else "?  ")
            lines.append(f"  {mark}{c.name}: {c.detail}")
        for f in self.files:
            lines.append(f"  -> {f}")
        return "\n".join(lines)


def _box(page, name: str) -> tuple[float, float, float, float]:
    b = page.get(f"/{name}") or page.mediabox
    return tuple(float(x) for x in b)  # type: ignore[return-value]


def _fonts_embedded(reader: PdfReader) -> tuple[bool, list[str]]:
    missing: set[str] = set()
    for page in reader.pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        for ref in fonts.values():
            f = ref.get_object()
            desc_fonts = f.get("/DescendantFonts")
            targets = [d.get_object() for d in desc_fonts] if desc_fonts else [f]
            for t in targets:
                fd = t.get("/FontDescriptor")
                fd = fd.get_object() if fd else None
                if fd is None or not any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                    missing.add(str(f.get("/BaseFont")))
    return not missing, sorted(missing)


def _min_ppi(pdf: Path) -> tuple[int | None, str]:
    exe = shutil.which("pdfimages")
    if not exe:
        return None, "pdfimages (poppler) not installed; relying on Fotobuch's DPI warnings"
    out = subprocess.run([exe, "-list", str(pdf)], capture_output=True, text=True).stdout
    ppis = []
    for line in out.splitlines()[2:]:
        parts = line.split()
        if len(parts) > 13 and parts[2] == "image":
            try:
                ppis.append(min(int(parts[12]), int(parts[13])))
            except ValueError:
                pass
    return (min(ppis) if ppis else None), f"{len(ppis)} images checked"


def preflight(pdf: Path, profile: Profile, *, has_cover: bool, dpi_warnings: list[str] | None = None) -> Preflight:
    rep = Preflight(pdf)
    reader = PdfReader(str(pdf))
    pages = reader.pages
    inner = pages[1:] if has_cover else pages
    n = len(inner)
    pmin = int(profile.print.get("page_count_min", 0) or 0)
    pmax = int(profile.print.get("page_count_max", 10_000) or 10_000)
    step = int(profile.print.get("page_count_step", 1) or 1)
    rep.add("inner page count", pmin <= n <= pmax, f"{n} pages (allowed {pmin}–{pmax})",
            id="pages", n=n, min=pmin, max=pmax)
    rep.add("page count step", n % step == 0, f"{n} is {'' if n % step == 0 else 'not '}a multiple of {step}",
            id="page_step", n=n, step=step)
    b = profile.bleed_mm
    want_w, want_h = (profile.page_width_mm + 2 * b) * MM, (profile.page_height_mm + 2 * b) * MM
    bad_size = [i for i, p in enumerate(inner, 1)
                if abs(_box(p, "MediaBox")[2] - _box(p, "MediaBox")[0] - want_w) > 0.6
                or abs(_box(p, "MediaBox")[3] - _box(p, "MediaBox")[1] - want_h) > 0.6]
    rep.add("page size incl. bleed", not bad_size,
            f"{profile.page_width_mm:g}×{profile.page_height_mm:g} mm + {b:g} mm bleed"
            + (f"; wrong on pages {bad_size[:5]}" if bad_size else ""),
            id="page_size", w=profile.page_width_mm, h=profile.page_height_mm, bleed=b, bad=bad_size[:5])
    bad_trim = []
    for i, p in enumerate(inner, 1):
        mb, tb = _box(p, "MediaBox"), _box(p, "TrimBox")
        if abs((tb[0] - mb[0]) - b * MM) > 0.6 or abs((mb[2] - tb[2]) - b * MM) > 0.6:
            bad_trim.append(i)
    rep.add("TrimBox = page minus bleed", not bad_trim, "set on all pages" if not bad_trim else f"pages {bad_trim[:5]}",
            id="trim", bad=bad_trim[:5])
    if has_cover:
        mb = _box(pages[0], "MediaBox")
        rep.add("cover size", True, f"{(mb[2] - mb[0]) / MM:.1f} × {(mb[3] - mb[1]) / MM:.1f} mm incl. bleed "
                "(check spine width against your printer's calculator)", "info",
                id="cover", w=round((mb[2] - mb[0]) / MM, 1), h=round((mb[3] - mb[1]) / MM, 1))
    ok, missing = _fonts_embedded(reader)
    rep.add("fonts embedded", ok, "all fonts embedded" if ok else f"not embedded: {', '.join(missing)}",
            id="fonts", missing=missing)
    ppi, note = _min_ppi(pdf)
    if ppi is not None:
        rep.add("image resolution", ppi >= 250, f"lowest {ppi} ppi ({note})", "warning" if ppi >= 150 else "error",
                id="resolution", ppi=ppi)
    if dpi_warnings:
        rep.add("photos below target DPI", False, f"{len(dpi_warnings)}: " + "; ".join(dpi_warnings[:4]), "warning",
                id="low_dpi", n=len(dpi_warnings))
    size_mb = pdf.stat().st_size / 1e6
    maxmb = float(profile.print.get("max_file_mb", 1e9) or 1e9)
    rep.add("file size", size_mb <= maxmb, f"{size_mb:.1f} MB (limit {maxmb:g} MB)",
            id="file_size", mb=round(size_mb, 1), max=maxmb)
    rep.add("colour", True, "sRGB images with embedded ICC profile (no CMYK/PDF-X conversion)", "info", id="colour")
    return rep


def split_cover(pdf: Path) -> tuple[Path, Path]:
    reader = PdfReader(str(pdf))
    cover, inner = PdfWriter(), PdfWriter()
    cover.add_page(reader.pages[0])
    for p in reader.pages[1:]:
        inner.add_page(p)
    c_path = pdf.with_name(pdf.stem + "_cover.pdf")
    i_path = pdf.with_name(pdf.stem + "_inner.pdf")
    with open(c_path, "wb") as f:
        cover.write(f)
    with open(i_path, "wb") as f:
        inner.write(f)
    return c_path, i_path


def release(work: Work, profile: Profile | str | None = None, fb: Fotobuch | None = None) -> Preflight:
    manifest = json.loads(work.manifest.read_text(encoding="utf-8"))
    profile = profile if isinstance(profile, Profile) else load_profile(profile or manifest["profile"])
    project_dir = Path(manifest["project_dir"])
    fb = fb or Fotobuch()
    out = fb.run(["build", "release"], project_dir)
    dpi_warnings = [f"page {m.group(1)}: {m.group(2)} at {m.group(3)} DPI" for m in DPI_LINE.finditer(out)]
    pdf = project_dir / f"{PROJECT}_final.pdf"
    has_cover = bool(manifest.get("cover"))
    rep = preflight(pdf, profile, has_cover=has_cover, dpi_warnings=dpi_warnings)
    rep.files.append(pdf)
    if has_cover and profile.print.get("cover_separate_pdf"):
        rep.files.extend(split_cover(pdf))
    # the browser app's Print step shows this result until the book changes again
    (work.root / RELEASE_FILE).write_text(json.dumps(rep.as_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
    return rep

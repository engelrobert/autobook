"""Printer profiles (page size, bleed, cover, page-count rules) as TOML files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path


@dataclass
class Profile:
    name: str
    description: str
    page_width_mm: float
    page_height_mm: float
    bleed_mm: float = 3.0
    margin_mm: float = 0.0
    gap_mm: float = 4.0
    dpi: float = 300.0
    photos_per_page_max: int = 5
    printer: str = ""  # a named printer (epubli) whose rules these are; "" = generic defaults
    cover: dict = field(default_factory=dict)
    print: dict = field(default_factory=dict)

    @property
    def cover_enabled(self) -> bool:
        return bool(self.cover.get("enabled", False))

    @property
    def cover_typed(self) -> bool:
        """The cover's size comes from the printer's cover calculator, typed in for each book."""
        return self.cover.get("sizes") == "typed"

    @property
    def separate_files(self) -> bool:
        """Inner pages and cover as files of their own (epubli: one page size per file)."""
        return bool(self.print.get("separate_files") or self.print.get("cover_separate_pdf"))

    def same_pages(self, other: Profile) -> bool:
        """Same page size and bleed: a book can move between the two without a new layout."""
        return (self.page_width_mm, self.page_height_mm, self.bleed_mm, self.margin_mm) == \
            (other.page_width_mm, other.page_height_mm, other.bleed_mm, other.margin_mm)


def available_profiles() -> list[str]:
    root = resources.files("autobook.resources").joinpath("profiles")
    return sorted(p.name.removesuffix(".toml") for p in root.iterdir() if p.name.endswith(".toml"))


def load_profile(name_or_path: str) -> Profile:
    """A built-in profile name (e.g. 'square-21') or a path to your own TOML file."""
    p = Path(name_or_path)
    if p.suffix == ".toml" and p.exists():
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        name = p.stem
    else:
        res = resources.files("autobook.resources").joinpath("profiles", f"{name_or_path}.toml")
        if not res.is_file():
            raise ValueError(f"unknown profile {name_or_path!r}; built-in: {', '.join(available_profiles())}")
        data = tomllib.loads(res.read_text(encoding="utf-8"))
        name = name_or_path
    return Profile(name=name, **data)

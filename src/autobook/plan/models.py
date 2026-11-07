"""The book plan: the neutral contract between selection/review and layout.

Nothing in here is Fotobuch-specific, so another layout engine could consume it.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Scores(BaseModel):
    local: float | None = None
    tech: float | None = None
    aesthetic: float | None = None
    interest: float | None = None
    final: float = 0.0


class PlanPhoto(BaseModel):
    id: str
    path: str
    ts: str
    ts_source: Literal["exif", "mtime"] = "exif"
    gps: tuple[float, float] | None = None
    width: int = 0
    height: int = 0
    weight: float = 1.0  # Fotobuch area_weight
    strong: bool = False
    scores: Scores = Scores()
    reason: str = ""
    dup_group: str | None = None
    by_user: bool = False  # put in by hand: the photo-count slider never takes it out
    layout: Literal["normal", "page", "spread"] = "normal"  # page: a page to itself; spread: across two pages


class Divider(BaseModel):
    style: Literal["hero", "plain"] = "hero"
    photo: str | None = None  # hero photo id (for style hero)

    @field_validator("style", mode="before")
    @classmethod
    def _no_maps(cls, v):  # plans saved before maps were removed (2026-10-30)
        return "hero" if v == "map" else v


class Chapter(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    date_from: str
    date_to: str
    place: str | None = None
    country: str | None = None
    event_ids: list[str] = []
    divider: Divider = Divider()
    photos: list[PlanPhoto] = []
    alternatives: list[str] = []  # next-best photo ids for the review tray
    excluded: list[str] = []  # taken out by hand: the photo-count slider never puts them back
    pages: list[list[str]] | None = None  # your own page split (photo ids per page) once you moved a photo


class Rejected(BaseModel):
    id: str
    reason: str  # blur | near_dup:<id> | not_selected | excluded_event | screenshot


class BookMeta(BaseModel):
    title: str
    subtitle: str = ""
    date_from: str
    date_to: str
    language: str = "en"
    cover_photo: str | None = None
    cover_candidates: list[str] = []  # suggestions for the review page (favourites first)
    back_photo: str | None = None
    profile: str = "square-21"
    photos_per_page: float = 4.5  # average on photo pages (from the brief)
    # page design: the same frame around the photos on every page, background colours as swatch keys
    frame: Literal["none", "narrow", "wide"] = "narrow"
    background: str = "white"
    page_colors: dict[str, str] = {}  # first photo id of a page (or chapter:<id> for a plain opener) -> swatch
    # where a trimmed photo is cut: photo id -> (x, y), 0 keeps the left/top edge, 1 the right/bottom one
    crops: dict[str, tuple[float, float]] = {}
    auto_fit: bool = True  # a book too short for the printer gets more photos instead of blank pages

    @field_validator("crops")
    @classmethod
    def _crops_inside(cls, v):
        bad = sorted(k for k, (x, y) in v.items() if not (0 <= x <= 1 and 0 <= y <= 1))
        if bad:
            raise ValueError(f"crop positions must be between 0 and 1: {bad}")
        return v

    @field_validator("background")
    @classmethod
    def _known_background(cls, v):
        from autobook.export.style import SWATCHES

        if v not in SWATCHES:
            raise ValueError(f"unknown colour {v!r}")
        return v

    @field_validator("page_colors")
    @classmethod
    def _known_page_colors(cls, v):
        from autobook.export.style import SWATCHES

        bad = sorted(set(v.values()) - set(SWATCHES))
        if bad:
            raise ValueError(f"unknown colours {bad}")
        return v


class BookPlan(BaseModel):
    schema_: Literal["autobook.plan/1"] = Field("autobook.plan/1", alias="schema")
    created: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    selection: Literal["local", "ai"] = "local"
    book: BookMeta
    chapters: list[Chapter]
    rejected: list[Rejected] = []
    warnings: list[str] = []  # e.g. AI calls that failed and what was used instead

    model_config = {"populate_by_name": True}

    # helpers -----------------------------------------------------------
    def all_photos(self) -> list[PlanPhoto]:
        return [p for c in self.chapters for p in c.photos]

    def photo_count(self) -> int:
        return sum(len(c.photos) for c in self.chapters)

    def find(self, pid: str) -> tuple[Chapter, PlanPhoto] | None:
        for c in self.chapters:
            for p in c.photos:
                if p.id == pid:
                    return c, p
        return None

    def save(self, path: Path, history: Path | None = None) -> None:
        """Write the plan; keep the previous version in history/ (for undo and the swap metric)."""
        if history is not None and path.exists():
            history.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(path, history / f"plan-{stamp}.json")
        path.write_text(self.model_dump_json(by_alias=True, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> BookPlan:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


def export_schemas(out: Path) -> None:
    """Write JSON Schemas for the plan and the brief (docs/schemas)."""
    from autobook.brief.models import Brief

    out.mkdir(parents=True, exist_ok=True)
    (out / "book_plan.schema.json").write_text(
        json.dumps(BookPlan.model_json_schema(by_alias=True), indent=2), encoding="utf-8")
    (out / "brief.schema.json").write_text(
        json.dumps(Brief.model_json_schema(by_alias=True), indent=2), encoding="utf-8")

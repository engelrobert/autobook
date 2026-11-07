"""The question brief: a small JSON document that steers selection, chapters and titles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Chaptering = Literal["auto", "place", "day", "event", "theme", "folder"]
Scope = Literal["highlights", "complete"]
Tone = Literal["plain", "warm", "playful", "poetic"]


class QA(BaseModel):
    id: str
    question: str
    answer: str | list[str] | bool | int | None = None


class Brief(BaseModel):
    schema_: Literal["autobook.brief/1"] = Field("autobook.brief/1", alias="schema")
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    qa: list[QA] = []
    main_event_ids: list[str] = []  # empty = all events weigh the same
    excluded_event_ids: list[str] = []
    scope: Scope = "highlights"
    target_from_user: bool = False  # target_photos was set by the person (not a suggestion) in the browser app
    target_photos: int = 200
    target_pages: int = 70
    photos_per_page: float = 4.5  # average on photo pages; sets the page count Fotobuch aims for
    chaptering: Chaptering = "auto"
    max_chapters: int = 10
    title_tone: Tone = "warm"
    language: str = "en"
    book_title: str | None = None  # None = let the AI (or the place names) decide
    priorities: list[str] = []  # AI tags that get a bonus, e.g. ["people", "landscape"]
    # share of photos with people as the subject (rest: landscapes, sights, details); None = no target.
    # Needs AI tags; 0.8 matches "family first" (the mix of the user's own favourites, 2026-10-30).
    people_share: float | None = 0.8
    subtitles: bool = False  # one line under each chapter title and the book title
    # confirmed favourite marks: [{kind: folder|prefix|suffix, pattern, level}]; see discover/favourites.py
    favourite_markers: list[dict] = []
    # fill: all favourites + the AI picks more up to the target; only: exactly the favourites;
    # best: the AI picks the target number from the favourites (for many favourites); ignore
    favourites_mode: Literal["fill", "only", "best", "ignore"] = "fill"
    # run settings (chosen in the browser; the CLI has its own options)
    profile: str = "square-21"
    use_ai: bool = True
    effort: str = "high"
    notes: str = ""

    model_config = {"populate_by_name": True}

    def dump(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)


class Option(BaseModel):
    value: str
    label: str


class Question(BaseModel):
    id: str
    text: str
    kind: Literal["single", "multi", "text", "number", "bool"]
    options: list[Option] = []
    default: str | list[str] | int | bool | None = None
    maps_to: str  # Brief field name, or "main_event_ids" etc.
    why: str = ""

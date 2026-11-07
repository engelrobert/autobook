"""Offline reverse geocoding (GeoNames cities1000 via reverse_geocoder, CC-BY 4.0)."""

from __future__ import annotations

import contextlib
import io

COUNTRY_NAMES = {
    "AT": "Austria", "BE": "Belgium", "CH": "Switzerland", "CZ": "Czechia", "DE": "Germany",
    "DK": "Denmark", "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom",
    "GR": "Greece", "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IS": "Iceland",
    "IT": "Italy", "NL": "Netherlands", "NO": "Norway", "PL": "Poland", "PT": "Portugal",
    "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "TR": "Turkey", "US": "United States",
    "CA": "Canada", "MX": "Mexico", "JP": "Japan", "TH": "Thailand", "VN": "Vietnam",
    "AU": "Australia", "NZ": "New Zealand", "ZA": "South Africa", "MA": "Morocco", "EG": "Egypt",
}


def reverse_geocode(coords: list[tuple[float, float]]) -> list[dict]:
    if not coords:
        return []
    import reverse_geocoder as rg

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        res = rg.search(coords, mode=1)
    return [{"name": r["name"], "admin1": r["admin1"], "cc": r["cc"]} for r in res]


def name_events(events: list[dict]) -> list[dict]:
    """Set place/country on events; events without GPS borrow from the nearest neighbour."""
    with_gps = [e for e in events if e.get("lat") is not None]
    for e, g in zip(with_gps, reverse_geocode([(e["lat"], e["lon"]) for e in with_gps])):
        e["place"] = g["name"]
        e["country"] = g["cc"]
        e.setdefault("data", {})["admin1"] = g["admin1"]
    for i, e in enumerate(events):
        if e.get("place"):
            continue
        for d in range(1, len(events)):
            for j in (i - d, i + d):
                if 0 <= j < len(events) and events[j].get("lat") is not None and events[j].get("place"):
                    e["place"] = events[j]["place"]
                    e["country"] = events[j].get("country")
                    e.setdefault("data", {})["place_inferred"] = True
                    break
            if e.get("place"):
                break
    return events


def country_name(cc: str | None) -> str:
    return COUNTRY_NAMES.get(cc or "", cc or "")

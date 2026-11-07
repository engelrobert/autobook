"""Date formatting for chapter openers and the cover in a handful of languages."""

from __future__ import annotations

from datetime import date

MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
           "Oktober", "November", "Dezember"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
           "octobre", "novembre", "décembre"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
           "octubre", "noviembre", "diciembre"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
           "settembre", "ottobre", "novembre", "dicembre"],
    "nl": ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
           "oktober", "november", "december"],
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
           "outubro", "novembro", "dezembro"],
}

def lang_of(language: str) -> str:
    code = (language or "en").lower()[:2]
    return code if code in MONTHS else "en"


def format_date(d: date, language: str = "en") -> str:
    lang = lang_of(language)
    m = MONTHS[lang][d.month - 1]
    if lang == "en":
        return f"{d.day} {m} {d.year}"
    if lang == "de":
        return f"{d.day}. {m} {d.year}"
    return f"{d.day} {m} {d.year}"


def format_range(a: date, b: date, language: str = "en") -> str:
    """'1–14 July 2026', '1.–14. Juli 2026', '30 June – 2 July 2026'."""
    lang = lang_of(language)
    if a == b:
        return format_date(a, lang)
    ma, mb = MONTHS[lang][a.month - 1], MONTHS[lang][b.month - 1]
    dot = "." if lang == "de" else ""
    if a.year != b.year:
        return f"{format_date(a, lang)} – {format_date(b, lang)}"
    if a.month == b.month:
        return f"{a.day}{dot}–{b.day}{dot} {mb} {b.year}"
    return f"{a.day}{dot} {ma} – {b.day}{dot} {mb} {b.year}"

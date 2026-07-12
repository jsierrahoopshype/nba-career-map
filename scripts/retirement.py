"""Explicit-retirement-language detection from a player's Wikipedia wikitext.

Bug: the only retirement signal used to be "no current stint for 2+ years"
(see player_status.classify_status), so a player who explicitly announced
their retirement stays classified overseas_active/nba_active for up to two
years after the announcement (confirmed case: Alex Abrines retired 22 July
2025; Wikipedia's own article states this in prose, but he has no dated
career-history stint after that, so the time-based rule alone would not catch
him until mid-2027).

This module adds retirement-announcement PROSE detection as a second,
independent signal, layered on top of (not replacing) the 2-year fallback:
classify_status() treats either signal as sufficient. The fallback stays in
place for players who fade out with no explicit announcement.

Detection runs on the full page wikitext already fetched for career-history
parsing (no extra request): reference tags are stripped first (citation
titles can contain "retirement" incidentally), then a fixed set of retirement
phrases is searched for, skipping any hit immediately preceded by a negation
("not"/"never"). If found, a nearby date (looked for in the text just before
the phrase, matching how Wikipedia prose reads: "On <date>, <Name>
announced...") is parsed into ISO form when possible.
"""
from __future__ import annotations

import re

_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL | re.IGNORECASE)

_RETIRE_PHRASES = [
    r"announced (?:his|her|their) retirement",
    r"retirement from professional basketball",
    r"retired from professional basketball",
    r"retired from playing (?:professional )?basketball",
    r"retired from basketball",
    r"retired from playing",
    r"officially retired",
    r"announced (?:his|her|their) retirement from basketball",
]
_RETIRE_RE = re.compile("(?:" + "|".join(_RETIRE_PHRASES) + ")", re.IGNORECASE)
_NEGATION_RE = re.compile(r"\b(?:not|never|n't)\s+\S*\s*$", re.IGNORECASE)

_MONTHS = {m: i + 1 for i, m in enumerate([
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"])}
_MONTH_ALT = "|".join(_MONTHS)
_DATE_MDY_RE = re.compile(rf"({_MONTH_ALT})\s+(\d{{1,2}}),?\s+(\d{{4}})", re.IGNORECASE)
_DATE_DMY_RE = re.compile(rf"(\d{{1,2}})\s+({_MONTH_ALT})\s+(\d{{4}})", re.IGNORECASE)
_BARE_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

_WINDOW = 220  # chars of context scanned before a retirement-phrase match for a date


def _strip_markup(s: str) -> str:
    """Light cleanup so date regexes see plain text: drop wikilink brackets,
    bold/italic markup, and stray templates without touching the wording."""
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", s)
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"\{\{[^{}]*\}\}", " ", s)
    return s


def _extract_date(window: str) -> str:
    """Best-effort ISO (or partial) date from a text window, or ''."""
    window = _strip_markup(window)
    m = _DATE_MDY_RE.search(window)
    if m:
        month, day, year = _MONTHS[m.group(1).title()], int(m.group(2)), int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    m = _DATE_DMY_RE.search(window)
    if m:
        day, month, year = int(m.group(1)), _MONTHS[m.group(2).title()], int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    m = _BARE_YEAR_RE.search(window)
    if m:
        return m.group(0)
    return ""


def detect_retirement(wikitext: str) -> dict:
    """Scan wikitext for an explicit retirement announcement.

    Returns {} if none found, else {"retirement_announced": True,
    "retirement_date": "<iso or partial date>"} (retirement_date omitted if no
    date could be parsed nearby).
    """
    if not wikitext:
        return {}
    text = _REF_RE.sub(" ", wikitext)
    m = _RETIRE_RE.search(text)
    if not m:
        return {}
    preceding = text[max(0, m.start() - 12):m.start()]
    if _NEGATION_RE.search(preceding):
        # e.g. "he has not officially retired" — skip and keep looking
        return detect_retirement(text[m.end():])
    window = text[max(0, m.start() - _WINDOW):m.start()]
    out = {"retirement_announced": True}
    date = _extract_date(window)
    if date:
        out["retirement_date"] = date
    return out

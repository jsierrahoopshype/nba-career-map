"""Curated Wikipedia articles for players the scraper resolves to a namesake.

WHY THIS EXISTS. The scraper asks Wikipedia for a name and takes the article it
gets back. For "David Duke", "Jack White", "Ace Bailey" or "Michael Phelps" the
article it gets back is the Klansman, the guitarist, the ice hockey player and
the swimmer -- and the club history parsed off it became the NBA player's. The
identity gate in fetch_bio_wikidata.py detects it (the article's Wikidata item
is not a basketball player) and reports it in
data/players/bio_needs_review.json under `wikipedia_url_wrong_person`, but
detecting it does not fix it: the next daily run asks the same question and
gets the same wrong article.

This file is the answer that sticks. data/players/player_url_overrides.json
maps a player key in the career database to the article that IS about him, and
the pipeline consults it before it asks Wikipedia anything -- so a daily run
cannot walk the fix back.

TWO TIERS, AND ONLY ONE OF THEM IS LIVE.

  overrides   verified. Each entry was accepted by scripts/resolve_player_urls.py
              against Wikidata: the article's item carries P106 = Q3665646
              (basketball player) and a birth year within a year of
              Basketball-Reference's for this player. These are the ones the
              pipeline uses.
  candidates  guesses from Wikipedia's naming conventions ("<name>
              (basketball)", "<name> Jr."), pre-filled so the resolver tries
              the likely title first. NOTHING here reaches the pipeline. A
              candidate only goes live by being verified, at which point the
              resolver moves it into `overrides`.

A hand-written entry whose value is a bare URL string counts as verified: a
human typing an article into this file IS the verification. Every machine-
written entry is a dict and has to say `"verified": <date or true>` for the
loader to hand it over.

The file is also tolerated in its simplest possible shape -- a flat
{player: url} mapping with no wrapper -- so it can be edited by hand without
knowing any of the above.
"""
from __future__ import annotations

import json
from pathlib import Path

from names import normkey, title_from_url

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "data" / "players" / "player_url_overrides.json"

_CACHE: dict | None = None
_CACHE_PATH: Path | None = None


def _entry(value) -> dict | None:
    """One raw entry as a record, or None when it is not usable.

    A bare string is a human's hand-written article and is live. A dict has to
    carry both a URL and a truthy `verified` flag; an unverified dict is a
    candidate that happens to be stored in the wrong section, and is ignored
    rather than trusted.
    """
    if isinstance(value, str):
        url = value.strip()
        return {"wikipedia_url": url, "verified": "hand-written"} if url else None
    if not isinstance(value, dict):
        return None
    url = str(value.get("wikipedia_url") or "").strip()
    if not url or not value.get("verified"):
        return None
    rec = dict(value)
    rec["wikipedia_url"] = url
    return rec


def load(path: Path | None = None, *, refresh: bool = False) -> dict[str, dict]:
    """{player key: entry} for the VERIFIED overrides, read once.

    Keyed by the name as written in the file and by its normkey, so a lookup
    works whether the caller holds the career database's key, the display name
    or a roster spelling of it.
    """
    global _CACHE, _CACHE_PATH
    path = Path(path) if path else OVERRIDES
    if _CACHE is not None and not refresh and _CACHE_PATH == path:
        return _CACHE

    doc: dict = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            doc = raw
    except (OSError, ValueError):
        doc = {}

    if "overrides" in doc or "candidates" in doc:
        section = doc.get("overrides") or {}
    else:
        # the bare {player: url} shape -- everything that is not metadata
        section = {k: v for k, v in doc.items()
                   if k not in ("generated", "note", "verified_by")}
    if not isinstance(section, dict):
        section = {}

    out: dict[str, dict] = {}
    for name, value in section.items():
        rec = _entry(value)
        if not rec or not str(name).strip():
            continue
        rec["player"] = name
        out[name] = rec
        key = normkey(name)
        if key:
            out.setdefault(key, rec)
    _CACHE, _CACHE_PATH = out, path
    return out


def reset_cache() -> None:
    """Forget the loaded file (tests, and anything that rewrites it mid-run)."""
    global _CACHE, _CACHE_PATH
    _CACHE, _CACHE_PATH = None, None


def override_for(name: str, path: Path | None = None) -> dict | None:
    """The verified entry for this player, or None."""
    if not name:
        return None
    idx = load(path)
    return idx.get(name) or idx.get(normkey(name))


def override_url(name: str, path: Path | None = None) -> str:
    """The article URL to use for this player, or "" when he has no override."""
    rec = override_for(name, path)
    return rec["wikipedia_url"] if rec else ""


def override_title(name: str, path: Path | None = None) -> str:
    """The article TITLE to ask Wikipedia for, or "" when there is no override."""
    return title_from_url(override_url(name, path))


def overridden_players(path: Path | None = None) -> list[str]:
    """The player keys with a verified override, as the file spells them."""
    idx = load(path)
    return sorted({rec["player"] for rec in idx.values()})


def candidates(path: Path | None = None) -> dict[str, dict]:
    """{player: candidate record} -- the unverified guesses, for the resolver.

    Read straight off disk: these never take part in a pipeline run, so there
    is nothing to cache and nothing to keep consistent with `load`.
    """
    path = Path(path) if path else OVERRIDES
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    section = doc.get("candidates") if isinstance(doc, dict) else None
    if not isinstance(section, dict):
        return {}
    out = {}
    for name, value in section.items():
        if isinstance(value, str):
            value = {"wikipedia_url": value}
        if isinstance(value, dict) and str(value.get("wikipedia_url") or "").strip():
            out[name] = value
    return out

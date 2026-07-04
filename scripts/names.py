"""Name normalization and canonical Wikipedia URL/title helpers.

Used for duplicate detection: two records refer to the same player when their
canonical Wikipedia article matches, or (cheaper, pre-fetch) when their
normalized name keys match. Normalization folds the differences that caused
duplicates — diacritics (Şengün/Sengun), transliteration (Schröder/Schroeder),
suffixes (Jr./II), disambiguators ((basketball)), and initial spacing
(A. J./AJ).
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote, unquote

WIKI_PREFIX = "https://en.wikipedia.org/wiki/"


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def normkey(name: str) -> str:
    """A normalized match key for a player name.

    Folds diacritics, removes parenthetical disambiguators and Jr./Sr./II/III/IV
    suffixes, drops punctuation (so "A. J." == "AJ"), lowercases and collapses
    whitespace. Two names with the same normkey are treated as the same person.
    """
    if not name:
        return ""
    s = strip_accents(name)
    s = re.sub(r"\(.*?\)", " ", s)                       # (basketball), (1984)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", " ", s, flags=re.I)  # suffixes
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())            # drop punctuation
    # collapse runs of single-letter tokens so "a j green" == "aj green"
    tokens, buf = [], ""
    for tok in s.split():
        if len(tok) == 1:
            buf += tok
        else:
            if buf:
                tokens.append(buf); buf = ""
            tokens.append(tok)
    if buf:
        tokens.append(buf)
    return " ".join(tokens)


def title_from_url(url: str) -> str:
    """Article title from a Wikipedia URL ('.../Alperen_Sengun' -> 'Alperen Sengun')."""
    if not url:
        return ""
    return unquote(url.rstrip("/").split("/")[-1]).replace("_", " ")


def url_key(url: str) -> str:
    """normkey of the article a URL points to (for URL-based dedupe)."""
    return normkey(title_from_url(url))


def canonical_url(title: str) -> str:
    """Build a canonical en.wikipedia URL from an article title."""
    if not title:
        return ""
    return WIKI_PREFIX + quote(title.replace(" ", "_"), safe="_(),.'-")

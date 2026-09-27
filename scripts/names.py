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


_PAREN = re.compile(r"\s*\([^)]*\)")


def strip_disambiguator(name: str) -> str:
    """'Anthony Edwards (basketball)' -> 'Anthony Edwards'.

    Removes every parenthetical, and nothing else: a Jr./II/III that belongs
    to the name stays.
    """
    return re.sub(r"\s+", " ", _PAREN.sub("", name or "")).strip()


def spelling_key(name: str) -> str:
    """Compare two spellings of ONE name: accents, case and punctuation fold
    away, a parenthetical is dropped, and -- unlike normkey -- a suffix is
    kept. "Jorge Gutiérrez" matches "Jorge Gutierrez"; "Harry Giles III" does
    not match "Harry Giles", and "Cat Barber" does not match "Anthony Barber".
    """
    return re.sub(r"[^a-z0-9]", "",
                  strip_accents(strip_disambiguator(name)).casefold())


def display_name_for(key: str, current: str = "", *spellings: str) -> str:
    """The name a record is shown under.

    It is the record's own name. The article title is only the link: it may
    carry a disambiguator ("(basketball, born 1990)"), a suffix the record does
    not ("Harry Giles III"), or a different name altogether ("Cat Barber"),
    and none of that belongs on the page.

    The first of `current`, then `spellings` (e.g. the article title), that is
    the same name as the key -- up to accents, case and punctuation, so
    "Dennis Schröder" and "Mamadou N'Diaye" survive -- is kept, with any
    parenthetical removed. Otherwise the key itself, minus a parenthetical.
    """
    want = spelling_key(key)
    for cand in (current, *spellings):
        cand = strip_disambiguator(cand)
        if cand and spelling_key(cand) == want:
            return cand
    return strip_disambiguator(key) or key


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


def exact_article_key(title: str) -> str:
    """An article's identity, keeping what normkey folds away.

    normkey answers "same person?" and so drops Jr., Sr. and disambiguators --
    which makes it useless for "is this the same ARTICLE?". Kenyon Martin and
    Kenyon Martin Jr. share a normkey, and reading that as one article told the
    scraper the son's page already belonged to his father.
    """
    t = (title or "").replace("_", " ")
    t = re.sub(r"\s+", " ", t.replace(".", "")).strip().casefold()
    return t

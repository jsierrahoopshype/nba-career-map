"""Is the article we landed on about the person we asked for?

The scraper asks Wikipedia for a name and takes whatever article the API hands
back after following redirects. That is right for the cases it was built for --
"Bub Carrington" -> "Carlton Carrington", "Jakob Poeltl" -> "Jakob Pöltl" -- and
catastrophic for one case it was not: "Scotty Pippen Jr" redirects to his
father, so the son's record was filled with a career that ended before he was
born.

The rule that separates the two: a redirect may change how a name is spelled,
but it may not change WHO it names. Two signals say the person changed:

  * the generational suffix moved (Jr and Sr are different people, always)
  * the surname changed (Michael Wilson is not Michael Gibson)

A birth year in a disambiguator is a third, when both sides carry one.

Everything else -- diacritics, transliteration, nicknames, an added
"(basketball)" -- is the same person under another spelling, and is allowed.
"""
from __future__ import annotations

import re
import unicodedata

SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")

# Transliterations that a redirect legitimately crosses. Folding to the bare
# vowel is not enough: "Poeltl" and "Pöltl" only meet if ö can become "oe".
_TRANSLIT = ((r"ö", "oe"), (r"ü", "ue"), (r"ä", "ae"), (r"ß", "ss"),
             (r"å", "aa"), (r"ø", "oe"), (r"æ", "ae"), (r"œ", "oe"))


class Person:
    """A Wikipedia title split into the parts that identify a person."""

    __slots__ = ("raw", "given", "surname", "suffix", "year", "qualifier")

    def __init__(self, title: str):
        self.raw = title or ""
        inner = " ".join(re.findall(r"\((.*?)\)", self.raw))
        self.qualifier = inner.strip()
        year = re.search(r"\b(1[89]\d\d|20\d\d)\b", inner)
        self.year = int(year.group()) if year else None

        bare = re.sub(r"\(.*?\)", " ", self.raw)
        bare = bare.replace("_", " ")
        toks = [t for t in re.split(r"[\s,]+", bare) if t]
        self.suffix = ""
        while toks and _suffix_of(toks[-1]):
            self.suffix = _suffix_of(toks[-1])
            toks.pop()
        self.surname = _fold(toks[-1]) if toks else frozenset()
        self.given = [_fold(t) for t in toks[:-1]]


def _suffix_of(token: str) -> str:
    t = re.sub(r"[^a-z]", "", token.lower())
    return t if t in SUFFIXES else ""


def _fold(token: str) -> frozenset:
    """Every spelling a name token could legitimately be written as.

    One folded form is not enough. Şengün reaches ASCII two ways -- "sengun" by
    dropping the diacritics, "senguen" by transliterating ü -- and the roster
    picks one while Wikipedia titles the other. Return both and let the caller
    ask whether the two sets meet.
    """
    def plain(s):
        s = "".join(c for c in unicodedata.normalize("NFKD", s)
                    if not unicodedata.combining(c))
        return re.sub(r"[^a-z0-9]", "", s)

    low = token.lower()
    forms = {plain(low)}
    trans = low
    for ch, rep in _TRANSLIT:
        trans = trans.replace(ch, rep)
    forms.add(plain(trans))
    return frozenset(f for f in forms if f)


def _surnames_meet(a: frozenset, b: frozenset) -> bool:
    """Surnames match outright, or one is part of the other's compound.

    "Jackson" and "Jackson-Davis" are the same family name lengthened; the fold
    has already dropped the hyphen, so test containment at a token boundary
    rather than equality alone.
    """
    if not a or not b:
        return False
    if a & b:
        return True
    for x in a:
        for y in b:
            lo, hi = sorted((x, y), key=len)
            if len(lo) >= 4 and len(hi) > len(lo) and (
                    hi.startswith(lo) or hi.endswith(lo)):
                return True
    return False


def _show(forms: frozenset) -> str:
    return "/".join(sorted(forms)) or "?"


def same_person(requested: str, resolved: str, *,
                strict_suffix: bool = False) -> tuple[bool, str]:
    """Does `resolved` name the same person as `requested`?

    Returns (verdict, reason). A False verdict always carries a reason naming
    the signal that fired. A True verdict carries "" when the two titles agree
    on everything but spelling, or "suffix-added"/"given-name-changed" when the
    caller should look twice -- those are allowed, but they are also where a
    wrong article would hide, so they are worth logging.

    strict_suffix compares two article titles rather than a request and its
    answer: there, a suffix on one side alone is a second article about a
    second person, not a key catching up with its own title.
    """
    if not resolved:
        return False, "no article"
    a, b = Person(requested), Person(resolved)

    if a.year and b.year and a.year != b.year:
        return False, f"born {a.year} vs {b.year}"
    if not _surnames_meet(a.surname, b.surname):
        return False, (f"surname {_show(a.surname)} vs {_show(b.surname)}")
    if a.suffix and a.suffix != b.suffix:
        # Jr asked for, someone else answered. Always the father, never a
        # spelling: this is the Scotty Pippen Jr case.
        return False, f"suffix {a.suffix} vs {b.suffix or 'none'}"
    if b.suffix and not a.suffix:
        # Our key may simply predate the suffix ("Craig Porter" ->
        # "Craig Porter Jr."), or it may be the father being handed his son.
        # Titles alone cannot tell; the caller checks the database. When both
        # sides are real article titles there is nothing to weigh: two articles
        # that differ by a suffix are two people.
        if strict_suffix:
            return False, f"suffix none vs {b.suffix}"
        return True, "suffix-added"
    if a.given and b.given and not (a.given[0] & b.given[0]):
        return True, "given-name-changed"
    return True, ""


def candidate_titles(name: str, birth_year: int | None = None) -> list[str]:
    """Titles to try, most specific first, when a plain fetch lands wrong.

    A suffix written without its period is the common miss: Wikipedia titles
    the son "Scotty Pippen Jr." and leaves "Scotty Pippen" pointing at his
    father, so the period is the whole difference between the two careers.
    """
    p = Person(name)
    bare = re.sub(r"\s*\(.*?\)", "", name).strip()
    out = []
    if p.suffix and not bare.endswith("."):
        bare = re.sub(r"\b([A-Za-z]+)\s*$", r"\1.", bare)
        out.append(bare)
    if birth_year:
        out.append(f"{bare} (basketball, born {birth_year})")
    out.append(f"{bare} (basketball)")
    seen, uniq = set(), []
    for t in out:
        if t and t != name and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq

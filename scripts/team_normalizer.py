"""Team-name normalization.

Loads data/teams/team_aliases.json and maps any historical / sponsored team
name to its most-recent canonical name. Also handles light cleanup that is
common in Wikipedia wikitext (stripping wikilink markup, sponsor suffixes,
whitespace) before consulting the alias table.

Also exposes a SPELLING-VARIANT test (spelling_key / is_spelling_variant) for
names the alias table has never been told about -- the case that produced the
"Ironi Nes Ziona -> Ironi Ness Ziona" phantom transfer.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ALIASES_PATH = DATA_DIR / "teams" / "team_aliases.json"


def strip_diacritics(name: str) -> str:
    """'Beşiktaş' -> 'Besiktas'. Decompose, then drop the combining marks."""
    return "".join(c for c in unicodedata.normalize("NFKD", name or "")
                   if not unicodedata.combining(c))


def spelling_key(name: str) -> str:
    """Reduce a club name to the part that survives spelling noise.

    Folds away exactly the four things that distinguish a typo from a transfer:
    letter case, diacritics, punctuation and spacing, and doubled letters. So
    'Ironi Nes Ziona' and 'Ironi Ness Ziona' both key to 'ironinesziona', as do
    'Besiktas'/'Beşiktaş' and 'PAOK'/'P.A.O.K.'.

    Deliberately NOT a general fuzzy match. Two names one edit apart routinely
    belong to different clubs -- Palencia/Valencia, Palma/Parma,
    Iraklio/Iraklis, Chicago Rockers/Rockets are all real, distinct clubs in
    this dataset -- so anything looser than "same letters, differently
    written" would suppress genuine transfers.

    Transpositions are handled separately by is_spelling_variant, because they
    are the one edit that keeps the letters identical. See the note on
    TRANSPOSITION_MIN_KEY_LEN.
    """
    key = strip_diacritics(name).casefold()
    key = re.sub(r"[^a-z0-9]+", "", key)
    return re.sub(r"(.)\1+", r"\1", key)


# Pairs that look like one name written two ways but really are different
# clubs. Keyed on the pair of canonical names, order-insensitive.
KNOWN_DISTINCT: set[frozenset[str]] = {
    # Al Nasr plays in Dubai, Al Nassr in Riyadh.
    frozenset({"al nasr", "al nassr"}),
    # One adjacent transposition apart, and two different clubs in two
    # different countries: Khimik is in Pivdenne, Ukraine, BC Khimki in Khimki,
    # Russia. Below the length floor below anyway; pinned so that lowering the
    # floor could never merge them.
    frozenset({"khimik", "khimki"}),
}

# A transposition ("Watson"/"Waston") is a typo, not a different club -- but
# only once the name is long enough to carry the information. Swapping two
# characters in a SHORT name lands on another real name often enough to matter:
# the one false positive in this dataset, Khimik/Khimki, is a 6-character key,
# while all seven true positives are 11 characters or more. Short names are
# also where the space of real club names is densest, so this floor is not
# merely fitted to the one case.
TRANSPOSITION_MIN_KEY_LEN = 10


def _one_adjacent_transposition(a: str, b: str) -> bool:
    """True when b is a with exactly one neighbouring pair of chars swapped."""
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(diff) != 2:
        return False
    i, j = diff
    return j == i + 1 and a[i] == b[j] and a[j] == b[i]


def is_spelling_variant(a: str, b: str) -> bool:
    """True when two club names are the same name spelled differently.

    Two ways that can be true: the spelling keys match outright (case,
    diacritics, punctuation, doubled letters), or they differ by a single
    adjacent transposition in a key long enough to trust -- "Guruyu Watson" vs
    "Guruyú Waston", the pair that slipped past the key-equality rule.
    """
    if not a or not b:
        return False
    ka, kb = spelling_key(a), spelling_key(b)
    if not ka or not kb:
        return False
    if ka != kb:
        if len(ka) < TRANSPOSITION_MIN_KEY_LEN or len(kb) < TRANSPOSITION_MIN_KEY_LEN:
            return False
        if not _one_adjacent_transposition(ka, kb):
            return False
    pair = frozenset({strip_diacritics(a).casefold().strip(),
                      strip_diacritics(b).casefold().strip()})
    return pair not in KNOWN_DISTINCT


def edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, abandoned once it is certain to exceed `cap`.

    Used only to FLAG a pair for human review, never to suppress a transfer --
    see the note in spelling_key about how often one edit separates two real
    clubs.
    """
    a, b = a or "", b or ""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur, best = [i], i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            best = min(best, v)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


class TeamNormalizer:
    def __init__(self, aliases_path: Path = ALIASES_PATH):
        self.aliases_path = Path(aliases_path)
        raw = json.loads(self.aliases_path.read_text(encoding="utf-8"))
        self.aliases: dict[str, str] = raw.get("aliases", {})
        # Case-insensitive lookup index built once.
        self._ci = {self._key(k): v for k, v in self.aliases.items()}

    @staticmethod
    def _key(name: str) -> str:
        return re.sub(r"\s+", " ", name).strip().casefold()

    @staticmethod
    def clean(name: str) -> str:
        """Strip wikitext markup and normalize whitespace from a raw name."""
        if not name:
            return ""
        n = name
        # [[Target|Display]] -> Display ; [[Target]] -> Target
        n = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", n)
        n = n.replace("'''", "").replace("''", "")
        n = re.sub(r"<ref[^>]*>.*?</ref>", "", n, flags=re.DOTALL)
        n = re.sub(r"<ref[^>]*/>", "", n)
        n = re.sub(r"<[^>]+>", "", n)  # any remaining html tags
        n = re.sub(r"\{\{[^}]*\}\}", "", n)  # leftover templates
        n = re.sub(r"\s+", " ", n).strip()
        return n

    def normalize(self, name: str) -> str:
        """Return the canonical current name for a (possibly historical) team."""
        cleaned = self.clean(name)
        if not cleaned:
            return cleaned
        hit = self._ci.get(self._key(cleaned))
        return hit if hit else cleaned

    def is_known_alias(self, name: str) -> bool:
        return self._key(self.clean(name)) in self._ci

    def add_alias(self, historical: str, canonical: str) -> None:
        self.aliases[historical] = canonical
        self._ci[self._key(historical)] = canonical

    def save(self) -> None:
        raw = json.loads(self.aliases_path.read_text(encoding="utf-8"))
        raw["aliases"] = dict(sorted(self.aliases.items()))
        self.aliases_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    tn = TeamNormalizer()
    for t in ["Tau Cerámica", "New Jersey Nets", "Seattle SuperSonics",
              "[[Charlotte Bobcats]]", "Fenerbahçe Beko", "Los Angeles Lakers"]:
        print(f"{t!r:35} -> {tn.normalize(t)!r}")


# --- club renames: the same club, described at more length ------------------
#
# "Al Riyadi" -> "Al Riyadi Club Beirut" is not a transfer. One Wikipedia
# editor expanded the club's name to its formal form and every stint scraped
# after that carries the longer string, so the diff between two scrapes looks
# like a move. The spelling guard cannot see it: the letters are not the same,
# the name GAINED tokens.
#
# The test is containment of the TOKEN sets, never of the collapsed key. On the
# collapsed key "aris" sits inside "marisa" and "parma" nearly inside "palma";
# on tokens, gaining a word is gaining a word.

def spelling_tokens(name: str) -> tuple:
    """A club name as normalized words.

    Each token is folded the way spelling_key folds a whole name -- diacritics,
    case, punctuation, doubled letters -- but the word boundaries survive, so
    "P.A.O.K." stays one token rather than becoming four.
    """
    out = []
    for part in re.split(r"[\s\-/_]+", strip_diacritics(name or "").casefold()):
        tok = re.sub(r"[^a-z0-9]+", "", part)
        # Doubled letters are folded the way spelling_key folds them, but NOT
        # in a token short enough to be an acronym: collapsing "KK" to "k"
        # turns a club-type abbreviation into a stray letter that no longer
        # matches anything.
        if len(tok) > 3:
            tok = re.sub(r"(.)\1+", r"\1", tok)
        if tok:
            out.append(tok)
    return tuple(out)


# Words that describe a club rather than name it. An allowlist, not a
# denylist: an extra token that is not here (or a place, see below) means the
# two names are not the same club as far as this guard is concerned.
# Already doubled-letter-folded, as spelling_tokens produces them.
GENERIC_TOKENS = frozenset({
    # club-type words and their abbreviations
    "club", "clube", "cf", "fc", "bc", "bbc", "kk", "sc", "sk", "cd", "cb",
    "ca", "ac", "as", "cs", "ub", "bk", "bcm", "tj", "sd", "ce", "ad",
    "basket", "basketbal", "baloncesto", "basquete", "basquet", "basquetbol",
    "palacanestro", "kosarka", "kosarkaski", "korfbal", "sport", "sports",
    "sportif", "sportiva", "sportive", "deportivo", "deportiva", "atletico",
    "athletic", "atletica", "team", "bbal", "bal",
    # articles and particles
    "al", "el", "la", "le", "les", "lo", "los", "las", "de", "del", "della",
    "di", "da", "do", "dos", "das", "der", "die", "das", "den", "the", "of",
    "und", "et", "y", "e",
})

# Words that mark a SECOND team, not a longer name for the first one. Real
# Madrid and Real Madrid Castilla are a first team and its reserve side; so are
# Barcelona and Barcelona B. These veto a containment match outright, ahead of
# every other rule, because the alternative is quietly merging a farm team into
# its parent and losing every transfer between them.
RESERVE_TOKENS = frozenset({
    "b", "c", "ii", "iii", "2", "3", "two", "three",
    "castilla", "reserve", "reserves", "reserva", "filial", "cantera",
    "academy", "academia", "youth", "junior", "juniors", "jr", "jnr",
    "development", "dev", "farm", "affiliate", "feeder", "segunda",
    "u14", "u15", "u16", "u17", "u18", "u19", "u20", "u21", "u22", "u23",
})


def _place_tokens(place) -> set:
    """City and country of a stint, as normalized tokens."""
    out = set()
    for part in place or ():
        out.update(spelling_tokens(part))
    return out


def rename_containment(a: str, b: str, *, place_a=(), place_b=(),
                       known_cities=frozenset()) -> tuple:
    """Is `b` just `a` with descriptive words added (or the other way round)?

    Returns (verdict, reason) where verdict is True (same club), False (not
    this rule's business) or None (looks like a rename but is not safe to call
    automatically -- send it to review).

    A place token counts as a descriptor only when it is where one of these
    two clubs actually plays. Accepting any city attested in the country was
    tried and is far too loose: it merged Al Ahly into Al Ahly Cairo and Isuzu
    Motors into Akita Isuzu Motors purely because Cairo and Akita are cities
    somewhere in the same country. known_cities widens it back out for a caller
    that has a reason to; the default is the strict reading.
    """
    ta, tb = set(spelling_tokens(a)), set(spelling_tokens(b))
    if not ta or not tb or ta == tb:
        return False, ""
    if ta < tb:
        extra = tb - ta
    elif tb < ta:
        extra = ta - tb
    else:
        return False, ""

    if extra & RESERVE_TOKENS:
        marker = sorted(extra & RESERVE_TOKENS)[0]
        return False, f"reserve-side marker ({marker})"

    pa, pb = _place_tokens(place_a), _place_tokens(place_b)
    places = pa | pb | set(known_cities)
    unknown = sorted(t for t in extra
                     if t not in GENERIC_TOKENS and t not in places)
    if unknown:
        return False, f"distinguishing word ({', '.join(unknown)})"

    # Different countries is the strongest evidence of two clubs that merely
    # share a name -- Al-Nasr Benghazi and Al Nasr Dubai are not one club.
    ca = {t for t in spelling_tokens(place_a[-1] if place_a else "")}
    cb = {t for t in spelling_tokens(place_b[-1] if place_b else "")}
    if ca and cb and ca != cb:
        return None, "same name, different countries"
    # Two clubs in the same country whose cities disagree are two clubs, even
    # when the only extra token is an article: Palma is in Mallorca and La
    # Palma is in the Canaries.
    ta_city = set(spelling_tokens(place_a[0] if place_a else ""))
    tb_city = set(spelling_tokens(place_b[0] if place_b else ""))
    if ta_city and tb_city and ta_city != tb_city:
        return None, "same name, different cities"
    return True, "rename"

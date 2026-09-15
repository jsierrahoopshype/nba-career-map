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

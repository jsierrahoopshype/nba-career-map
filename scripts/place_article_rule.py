"""Recover a location from a place article, but only when the name agrees.

The sports-club guard in `_discover_location` refuses any article that says
nothing about a sport. That is what stops "Libertas" becoming an Irish
political party's registered office, and it is worth keeping. But it also
refuses 67 clubs whose article is simply the town they play in -- Ajaccio,
Ravenna, Plasencia, Keflavík -- where the location was there to be read.

This is the second, narrower rule for exactly that case, and nothing else:

    accept a place article ONLY when the club's name contains the place's name

"Ajaccio" contains "Ajaccio", so the club plays in Ajaccio. "Chêne" does not
contain "Montreux", so the Montreux article is refused even though it is a
perfectly good article about a perfectly real town. The containment test is
the whole safety margin: it is what separates reading a club's own name off
the map from accepting whatever Wikipedia offered when it had no article.

Names are compared with diacritics, punctuation and case folded away, so
"Al-Jahra" matches "Al Jahra" and "Kuşadası" matches itself.

Read-only by default; --apply writes the locations it is confident about.
Run:  python3 scripts/place_article_rule.py            # from the audit log
      python3 scripts/place_article_rule.py --live     # ask Wikipedia
      python3 scripts/place_article_rule.py --live --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geo import resolve_location  # noqa: E402
from wiki_person import _TRANSLIT  # noqa: E402  the letters NFKD leaves whole

ROOT = Path(__file__).resolve().parent.parent
AUDIT = ROOT / "logs" / "queued_place_audit.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
OUT = ROOT / "logs" / "place_article_rule.json"

# The words that make an article an article about a place. Deliberately a
# closed list: "Vasco da Gama" was a man who went to places, not one.
# A club sits in a settlement, so only settlement words count. "Region",
# "province", "district" and "island" are deliberately absent: South China is
# "a geographical and cultural region that covers the southernmost part of
# China", and the club of that name plays in Hong Kong. An area is not an
# address.
PLACE_WORDS = (r"cit(?:y|ies)|town|municipality|comm?une|village|borough|"  # comune: the
               # Italian spelling, and this dataset is full of Italian clubs
               r"settlement|urban area|metropolis|suburb")

_IS_PLACE = re.compile(
    rf"^(?P<subject>.{{1,60}}?)\s+(?:is|was)\s+(?:the|a|an)\s[^.]{{0,120}}?"
    rf"\b(?:{PLACE_WORDS})\b(?P<rest>[^.]*)", re.I)

# The proper nouns in the rest of the sentence -- the chain of places the town
# sits inside. Splitting on commas alone is not enough: "a city in the region
# of Emilia-Romagna in northern Italy" and "a university city on the river
# Clain in west-central France" put the country behind a preposition, not a
# comma, and a comma-only parse reads neither.
_PROPER = re.compile(r"[A-Z\u00C0-\u00DD][\w.'\u2019-]*(?:[ -][A-Z\u00C0-\u00DD][\w.'\u2019-]*)*")

# Innermost bracket pair, removed repeatedly. A single pass cannot do it:
# "Kuşadası (Turkish: [ˈkuʃadasɯ])" nests square brackets inside round ones,
# and a non-nesting pattern stops at the wrong closer and leaves a stray ")"
# glued to the name -- which then fails the containment test for no reason.
_INNERMOST = re.compile(r"\s*\([^()\[\]]*\)|\s*\[[^()\[\]]*\]")
_DANGLING = re.compile(r"\s*[\(\[\)\]]")
_SPACED_PUNCT = re.compile(r"\s+([;,])")
_NOISE = re.compile(r"^(?:the|northern|southern|eastern|western|central|north|"
                    r"south|east|west|north-?eastern|north-?western|"
                    r"south-?eastern|south-?western|greater|upper|lower)\s+", re.I)


def fold(s: str) -> str:
    """Case, diacritics and punctuation folded away; the comparison key.

    Transliterates before decomposing, because NFKD leaves ı, đ, ð and ł whole
    -- they are letters, not letters with marks. Without that pass "Kuşadası"
    folds to "kusadas", the article title folds to "kusadasi", and a club that
    is named after its own town fails the containment test.
    """
    s = s or ""
    for ch, rep in _TRANSLIT:
        s = s.replace(ch, rep)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.casefold())


def strip_brackets(text: str) -> str:
    """Every bracketed aside removed, however they nest."""
    prev = None
    out = text or ""
    while prev != out:
        prev, out = out, _INNERMOST.sub("", out)
    # A truncated extract can cut a bracket in half; drop the orphan rather
    # than carry it into a name.
    out = _DANGLING.sub("", out)
    return _SPACED_PUNCT.sub(r"\1", out)


def subject_of(extract: str) -> str:
    """The name the article is about: what stands before its first verb."""
    head = strip_brackets(extract or "").strip()
    m = _IS_PLACE.match(head)
    if not m:
        return ""
    subject = m.group("subject").strip(" ,;:-")
    # "Peja or Peć", "Mogi das Cruzes or" -- an article that opens with two
    # names for the same place. The first is the one to test.
    subject = re.split(r"\s+or\s+", subject)[0].strip(" ,;:-")
    # "Afyonkarahisar (Turkish: ...) is a major city" leaves one word; a
    # subject that ran away into a clause is not a name.
    return subject if 0 < len(subject.split()) <= 5 else ""


def place_chain(extract: str) -> tuple[str, str]:
    """(state, country) read off the chain of places the article names."""
    head = strip_brackets(extract or "")
    m = _IS_PLACE.match(head.strip())
    rest = m.group("rest") if m else ""
    parts = [_NOISE.sub("", p).strip(" ,;:") for p in _PROPER.findall(rest)]
    parts = [p for p in parts if p]
    # Walk from the outermost place inwards: the last name that resolves to a
    # country is the country, and the innermost name that resolves to a region
    # of that same country is the region.
    for i in range(len(parts) - 1, -1, -1):
        state, country = resolve_location(parts[i])
        if not country:
            continue
        if not state:
            for j in range(i - 1, -1, -1):
                inner_state, inner_country = resolve_location(parts[j])
                if inner_country == country and inner_state:
                    state = inner_state
                    break
        return state, country
    return "", ""


def judge(team: str, extract: str) -> dict:
    """What this rule makes of one refused club."""
    subject = subject_of(extract)
    if not subject:
        return {"team": team, "verdict": "not a place article"}
    if fold(subject) not in fold(team):
        return {"team": team, "verdict": "name does not contain the place",
                "place": subject}
    state, country = place_chain(extract)
    if not country:
        return {"team": team, "verdict": "place matched, country unreadable",
                "place": subject}
    return {"team": team, "verdict": "accept", "place": subject,
            "city": subject, "state": state, "country": country}


def extracts_from_audit() -> dict:
    doc = json.loads(AUDIT.read_text(encoding="utf-8"))
    return {r["team"]: r["extract"] for r in doc["refused"]}


def extracts_live(teams: list, delay: float, budget: int) -> dict:
    from wikipedia_api import WikipediaClient
    return WikipediaClient(delay=delay, max_requests=budget).get_extracts(teams)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="ask Wikipedia for full extracts instead of the "
                         "truncated ones in the audit log")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--max-requests", type=int, default=300)
    args = ap.parse_args()

    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    refused = [r["team"] for r in audit["refused"]]
    extracts = (extracts_live(refused, args.delay, args.max_requests)
                if args.live else extracts_from_audit())

    results = [judge(t, extracts.get(t) or "") for t in refused]
    by = {}
    for r in results:
        by.setdefault(r["verdict"], []).append(r)

    print(f"{len(refused)} club(s) the sports-club guard refused"
          f"{' (live extracts)' if args.live else ' (audit log, 200-char extracts)'}\n")
    for verdict in ("accept", "place matched, country unreadable",
                    "name does not contain the place", "not a place article"):
        print(f"   {verdict:34} {len(by.get(verdict, [])):4}")

    print("\naccepted, with what each would be set to:")
    for r in by.get("accept", []):
        print(f"   {r['team']!r:28} -> {r['city']}, {r['state'] or '-'}, {r['country']}")

    print("\ndeclined because the name does not contain the place:")
    for r in by.get("name does not contain the place", []):
        print(f"   {r['team']!r:28} article: {r['place']!r}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(ROOT)}")

    if args.apply:
        loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
        for r in by.get("accept", []):
            entry = loc.get(r["team"]) or {"team": r["team"], "league": ""}
            entry.update({"team": r["team"], "city": r["city"],
                          "state": r["state"], "country": r["country"]})
            loc[r["team"]] = entry
        LOCATIONS.write_text(json.dumps(dict(sorted(loc.items())),
                                        ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print(f"applied {len(by.get('accept', []))} location(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

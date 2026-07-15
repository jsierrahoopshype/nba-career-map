"""One-time ingest: nationality (and, if enabled, bonus fields) from a
basketball-reference-style CSV export into nba_players_careers.json.

The CSV's NATIONALITY column is already a country name (not a demonym, unlike
the live-fetch parser's |nationality= extraction) and correctly distinguishes
nationality from birthplace (e.g. Al-Farouq Aminu: born Georgia, USA;
nationality Nigeria). Both representations are stored as given by their
source — the frontend's nationalityFlag() bridges demonym and country-name
forms — rather than force-normalizing one into the other.

Matching: exact PLAYER-name match against the DB's primary key first (this
alone is expected to cover the vast majority). For the remainder, reuse
names.normkey (diacritics, suffixes, initials — the same normalization built
for the earlier duplicate-detection work) against every DB player's key,
display_name, and aliases. A normkey that resolves to MORE THAN ONE distinct
DB player is a genuine ambiguity (e.g. two "Mike James" entries disambiguated
only by a birth year the CSV doesn't carry) and is intentionally left
unmatched rather than guessed.

Conflict handling: a player who already has a nationality value from a live
Wikipedia fetch (a demonym, e.g. "French") is compared against the CSV value
(a country name, e.g. "France") by resolving the demonym to its country and
normalizing the CSV spelling to the same canonical form the resolved country
uses. Genuine disagreements are logged to logs/nationality_conflicts.json and
left untouched (not overwritten); agreements and players with no existing
value are set/overwritten from the CSV, since it has far more coverage.

DOES NOT touch all_star, career_history, status, or team fields. Bonus
fields (POS/HEIGHT/WEIGHT/DRAFT year/PICK/COLLEGE) only ingest when
INGEST_BONUS_FIELDS = True below (gated on explicit confirmation).

Idempotent: re-running with the same CSV changes nothing further (matched
players already carrying the same CSV-sourced value are left as-is; the
conflict log is rewritten fresh each run, not appended).

Run:  python3 scripts/ingest_nationalities.py <path-to-csv>
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from names import normkey

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
CONFLICTS_OUT = ROOT / "logs" / "nationality_conflicts.json"
UNMATCHED_OUT = ROOT / "logs" / "nationality_unmatched.json"

# Gate for the bonus fields (POS/HEIGHT/WEIGHT/DRAFT year/PICK/COLLEGE) —
# only ingested when explicitly confirmed. Nationality ingestion (this
# script's primary purpose) always runs regardless of this flag.
INGEST_BONUS_FIELDS = False

# Demonym -> country, mirroring flags.js's DEMONYM_TO_COUNTRY (used here only
# to compare an existing live-fetched demonym against the CSV's country name
# for conflict detection — not the frontend's rendering path).
DEMONYM_TO_COUNTRY = {
    "Angolan": "Angola", "Argentine": "Argentina", "Argentinian": "Argentina",
    "Australian": "Australia", "Austrian": "Austria", "Azerbaijani": "Azerbaijan",
    "Bahraini": "Bahrain", "Belarusian": "Belarus", "Belarusan": "Belarus",
    "Belgian": "Belgium", "Bolivian": "Bolivia", "Bosnian": "Bosnia",
    "Brazilian": "Brazil", "Bulgarian": "Bulgaria", "Burundian": "Burundi",
    "Canadian": "Canada", "Chilean": "Chile", "Chinese": "China",
    "Colombian": "Colombia", "Croatian": "Croatia", "Cypriot": "Cyprus",
    "Czech": "Czech Republic", "Danish": "Denmark", "Dominican": "Dominican Republic",
    "Ecuadorian": "Ecuador", "Ecuadorean": "Ecuador", "Egyptian": "Egypt",
    "Estonian": "Estonia", "Finnish": "Finland", "French": "France",
    "Georgian": "Georgia", "German": "Germany", "Greek": "Greece",
    "Honduran": "Honduras", "Hongkonger": "Hong Kong", "Hungarian": "Hungary",
    "Icelandic": "Iceland", "Indian": "India", "Indonesian": "Indonesia",
    "Iranian": "Iran", "Iraqi": "Iraq", "Irish": "Ireland", "Israeli": "Israel",
    "Italian": "Italy", "Ivorian": "Ivory Coast", "Japanese": "Japan",
    "Jordanian": "Jordan", "Kazakh": "Kazakhstan", "Kazakhstani": "Kazakhstan",
    "Kosovar": "Kosovo", "Kosovan": "Kosovo", "Kuwaiti": "Kuwait",
    "Latvian": "Latvia", "Lebanese": "Lebanon", "Libyan": "Libya",
    "Lithuanian": "Lithuania", "Luxembourgish": "Luxembourg", "Luxembourger": "Luxembourg",
    "Malaysian": "Malaysia", "Mexican": "Mexico", "Monegasque": "Monaco",
    "Monacan": "Monaco", "Mongolian": "Mongolia", "Montenegrin": "Montenegro",
    "Moroccan": "Morocco", "Dutch": "Netherlands", "New Zealander": "New Zealand",
    "Nicaraguan": "Nicaragua", "Nigerian": "Nigeria", "Macedonian": "North Macedonia",
    "Filipino": "Philippines", "Filipina": "Philippines", "Philippine": "Philippines",
    "Polish": "Poland", "Portuguese": "Portugal", "Puerto Rican": "Puerto Rico",
    "Qatari": "Qatar", "Romanian": "Romania", "Russian": "Russia",
    "Saudi": "Saudi Arabia", "Saudi Arabian": "Saudi Arabia", "Serbian": "Serbia",
    "Singaporean": "Singapore", "Slovak": "Slovakia", "Slovenian": "Slovenia",
    "Slovene": "Slovenia", "South African": "South Africa", "South Korean": "South Korea",
    "Korean": "South Korea", "Spanish": "Spain", "Swedish": "Sweden",
    "Swiss": "Switzerland", "Syrian": "Syria", "Taiwanese": "Taiwan",
    "Tanzanian": "Tanzania", "Tunisian": "Tunisia", "Turkish": "Turkey",
    "Emirati": "UAE", "American": "USA", "Ukrainian": "Ukraine",
    "British": "United Kingdom", "Scottish": "United Kingdom", "Welsh": "United Kingdom",
    "Northern Irish": "United Kingdom", "Uruguayan": "Uruguay", "Venezuelan": "Venezuela",
    "Vietnamese": "Vietnam", "English": "England", "Senegalese": "Senegal",
    "Thai": "Thailand", "Andorran": "Andorra", "South Sudanese": "South Sudan",
    "Sudanese": "Sudan",
    # revealed by real dual-nationality strings in the live-fetched data
    # (e.g. "Congolese / American") that don't otherwise appear in flags.js's
    # demonym table — added here for fair comparison even where the target
    # country has no flags.js flag entry yet (a comparison concern, separate
    # from flag rendering).
    "Congolese": "DR Congo", "Ugandan": "Uganda", "Cameroonian": "Cameroon",
    "Guinean": "Guinea", "Beninese": "Benin", "Rwandan": "Rwanda",
    "Salvadoran": "El Salvador", "Antiguan": "Antigua and Barbuda",
    "U.S. Virgin Islander": "US Virgin Islands",
    "Trinidadian": "Trinidad and Tobago", "Bahamian": "Bahamas",
}

# CSV country-name spellings that refer to a country already covered above
# under a different canonical spelling — used only to compare fairly against
# a resolved demonym; the CSV's original spelling is still what gets stored.
COUNTRY_ALIASES = {
    "United States": "USA",
    "Great Britain": "United Kingdom",
    "Republic of Georgia": "Georgia",
}


def _resolve_one(v: str) -> str:
    v = v.strip()
    return DEMONYM_TO_COUNTRY.get(v) or COUNTRY_ALIASES.get(v) or v


def _resolve_countries(value: str) -> set[str]:
    """Canonicalize a nationality value to the set of countries it names.

    A live-fetched value is often compound dual-nationality prose
    ("American / Nigerian" — the Wikipedia infobox field itself lists both),
    NOT a single opaque string; splitting on " / " and resolving each part is
    required so a CSV value matching ANY one of the parts registers as
    agreement rather than a false conflict."""
    parts = re.split(r"\s*/\s*", value or "")
    return {_resolve_one(p) for p in parts if p.strip()}


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            name = (row.get("PLAYER") or "").strip()
            if not name:
                continue
            rows.append({
                "player": name,
                "nationality": (row.get("NATIONALITY") or "").strip(),
                "pos": (row.get("POS") or "").strip(),
                "height": (row.get("HEIGHT") or "").strip(),
                "weight": (row.get("WEIGHT") or "").strip(),
                "draft_year": (row.get("DRAFT") or "").strip(),
                "pick": (row.get("PICK") or "").strip(),
                "college": (row.get("COLLEGE / TEAM") or "").strip(),
            })
    return rows


def build_norm_groups(players: list[dict]) -> dict[str, set[str]]:
    """normkey -> set of DISTINCT player keys sharing it (across key,
    display_name, and aliases) — used to detect genuine ambiguity, since a
    plain dict (first-wins) would silently hide a collision."""
    groups: dict[str, set[str]] = {}
    for p in players:
        key = p["player"]
        names = {key, p.get("display_name", "")} | set(p.get("aliases", []))
        for n in names:
            if not n:
                continue
            groups.setdefault(normkey(n), set()).add(key)
    return groups


def match_players(csv_rows: list[dict], players: list[dict]):
    by_key = {p["player"]: p for p in players}
    norm_groups = build_norm_groups(players)

    exact, via_normkey, ambiguous, unmatched = [], [], [], []
    for row in csv_rows:
        name = row["player"]
        if name in by_key:
            exact.append((row, by_key[name]))
            continue
        candidates = norm_groups.get(normkey(name), set())
        if len(candidates) == 1:
            via_normkey.append((row, by_key[next(iter(candidates))]))
        elif len(candidates) > 1:
            ambiguous.append({"csv_player": name, "candidates": sorted(candidates)})
        else:
            unmatched.append(name)
    return exact, via_normkey, ambiguous, unmatched


def apply_nationality(rec: dict, csv_nat: str, conflicts: list[dict]) -> str:
    """Returns 'set' | 'agreed_overwrite' | 'agreed_kept_existing' | 'conflict' | 'skipped'.

    A live-fetched value is sometimes compound dual-nationality prose
    ("American / Nigerian"). If the CSV's single value is one of several
    countries in an existing compound value, that's agreement, not a
    conflict — but the existing (richer, multi-nationality) value is KEPT
    rather than overwritten with the CSV's single-country value, so no
    previously-captured nuance is lost. A single-valued existing entry that
    agrees IS overwritten with the CSV's value (a straight equivalent-value
    swap, no information lost). Only a genuine no-overlap disagreement is a
    conflict."""
    if not csv_nat:
        return "skipped"
    existing = rec.get("nationality")
    if not existing:
        rec["nationality"] = csv_nat
        return "set"
    existing_countries = _resolve_countries(existing)
    csv_country = _resolve_one(csv_nat)
    if csv_country.lower() in {c.lower() for c in existing_countries}:
        if len(existing_countries) > 1:
            return "agreed_kept_existing"
        rec["nationality"] = csv_nat
        return "agreed_overwrite"
    conflicts.append({
        "player": rec["player"],
        "existing_nationality": existing,
        "existing_resolved_countries": sorted(existing_countries),
        "csv_nationality": csv_nat,
        "csv_resolved_country": csv_country,
    })
    return "conflict"


BONUS_FIELD_MAP = {
    "pos": "position", "height": "height", "weight": "weight",
    "draft_year": "draft_year_csv", "pick": "draft_pick_csv", "college": "college",
}


def apply_bonus_fields(rec: dict, row: dict, conflicts: list[dict]) -> None:
    for csv_key, field in BONUS_FIELD_MAP.items():
        val = row.get(csv_key, "")
        if not val:
            continue
        existing = rec.get(field)
        if existing and str(existing).strip() != val:
            conflicts.append({"player": rec["player"], "field": field,
                              "existing": existing, "csv_value": val})
            continue
        rec[field] = val


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python3 scripts/ingest_nationalities.py <path-to-csv>")
        sys.exit(1)
    csv_path = Path(sys.argv[1])
    csv_rows = load_csv(csv_path)
    players = json.loads(CAREERS.read_text(encoding="utf-8"))

    exact, via_normkey, ambiguous, unmatched = match_players(csv_rows, players)
    total_matched = len(exact) + len(via_normkey)

    conflicts: list[dict] = []
    field_conflicts: list[dict] = []
    counts = {"set": 0, "agreed_overwrite": 0, "agreed_kept_existing": 0,
              "conflict": 0, "skipped": 0}
    for row, rec in exact + via_normkey:
        outcome = apply_nationality(rec, row["nationality"], conflicts)
        counts[outcome] += 1
        if INGEST_BONUS_FIELDS:
            apply_bonus_fields(rec, row, field_conflicts)

    CAREERS.write_text(json.dumps(players, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    CONFLICTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"nationality_conflicts": conflicts}
    if INGEST_BONUS_FIELDS:
        payload["field_conflicts"] = field_conflicts
    CONFLICTS_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    UNMATCHED_OUT.write_text(json.dumps(
        {"unmatched": unmatched, "ambiguous": ambiguous}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print(f"CSV rows: {len(csv_rows)}")
    print(f"Exact-name matches: {len(exact)}")
    print(f"normkey/alias matches: {len(via_normkey)}")
    print(f"Total matched: {total_matched} ({total_matched/len(csv_rows)*100:.2f}%)")
    print(f"Ambiguous (left unmatched): {len(ambiguous)}")
    print(f"Unmatched (no candidate at all): {len(unmatched)}")
    print(f"Nationality set (no prior value): {counts['set']}")
    print(f"Nationality overwritten (agreed with a single existing value): {counts['agreed_overwrite']}")
    print(f"Nationality kept as-is (agreed with one part of a richer compound "
          f"existing value): {counts['agreed_kept_existing']}")
    print(f"Nationality conflicts (genuine disagreement, left untouched, logged): {counts['conflict']}")
    if INGEST_BONUS_FIELDS:
        print(f"Bonus-field conflicts (left untouched, logged): {len(field_conflicts)}")
    print(f"Conflicts written to {CONFLICTS_OUT.relative_to(ROOT)}")
    print(f"Unmatched/ambiguous written to {UNMATCHED_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

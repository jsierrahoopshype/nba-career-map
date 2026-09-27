"""One-time merge of the "Herb Jones" / "Herbert Jones" duplicate pair.

Both records are the Pelicans forward (drafted 2021, #35). "Herb Jones" came in
from the roster under the name nba.com prints and was scraped off the
"Herbert Jones" disambiguation page, so the identity gate flagged it as a
wrong-person record. "Herbert Jones" is the same man built from his own article
("Herbert Jones (basketball)", a verified override), so it is the survivor.

Merge rule (same as dedupe_claxton_migration.py and dedupe_alias_records.py):
keep "Herbert Jones", drop "Herb Jones", and keep the dropped spelling as an
alias -- the dedupe index in update_careers.py reads `aliases`, so a roster
row that says "Herb Jones" resolves to the survivor instead of re-creating the
duplicate, and search (data/player_aliases.json) still finds him by it.

Stints are NOT blindly unioned: the dropped record's "2021-2026" and the
survivor's "2021–present" are the same Pelicans tenure, and a union keyed on
the years string would print it twice. A stint is carried over only when the
survivor has nothing with the same team and start year. Scalars the survivor
lacks (nationality) are taken; the survivor's own values are kept.

Also:
  * nba_players_careers_READY.json gets the same merge
  * player_bio.json drops the "Herb Jones" record (the survivor has its own,
    with the right Wikidata item)
  * bio_needs_review.json drops his rows, which takes him off
    `wikipedia_url_wrong_person`; the counts are recomputed

The old /player/herb-jones.html URL is kept alive as a redirect stub by
prerender.REDIRECTS, not here. Derived files are rebuilt by
scripts/build_dashboard_data.py.

Idempotent. Run:  python3 scripts/merge_herb_jones.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_bio_wikidata import _write_json as _write_sorted  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
BIO = ROOT / "data" / "players" / "player_bio.json"
REVIEW = ROOT / "data" / "players" / "bio_needs_review.json"

PRIMARY = "Herbert Jones"
DUPLICATE = "Herb Jones"

# never copied from the dropped record: identity, and what the survivor's own
# article decides
_OWN = {"player", "display_name", "aliases", "career_history", "wikipedia_url",
        "current_team", "status", "parse_status", "last_updated"}


def _start(years: str) -> str:
    m = re.match(r"\d{4}", years or "")
    return m.group(0) if m else ""


def _merge_records(path: Path) -> bool:
    records = json.loads(path.read_text(encoding="utf-8"))
    by_name = {r["player"]: r for r in records}
    if DUPLICATE not in by_name:
        return False  # already merged
    assert PRIMARY in by_name, f"{PRIMARY!r} missing from {path}"
    keep, other = by_name[PRIMARY], by_name[DUPLICATE]

    have = {(_start(s.get("years")), s.get("team")) for s in
            keep.get("career_history") or []}
    for s in other.get("career_history") or []:
        if (_start(s.get("years")), s.get("team")) not in have:
            keep.setdefault("career_history", []).append(s)

    for field, value in other.items():
        if field not in _OWN and value and not keep.get(field):
            keep[field] = value

    # READY carries no aliases field on most rows; only add one where the
    # file already uses it, so its shape stays what the frontend expects.
    if "aliases" in keep or "aliases" in other:
        aliases = set(keep.get("aliases") or []) | set(other.get("aliases") or [])
        aliases.add(DUPLICATE)
        if other.get("display_name"):
            aliases.add(other["display_name"])
        aliases.discard(PRIMARY)
        keep["aliases"] = sorted(a for a in aliases if a)

    out = [r for r in records if r["player"] != DUPLICATE]
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return True


def _drop_bio() -> bool:
    bio = json.loads(BIO.read_text(encoding="utf-8"))
    if DUPLICATE not in bio:
        return False
    assert PRIMARY in bio, f"{PRIMARY!r} missing from {BIO}"
    del bio[DUPLICATE]
    _write_sorted(BIO, bio)
    return True


def _drop_review() -> bool:
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    changed = False
    for key, rows in list(review.items()):
        if not isinstance(rows, list):
            continue
        kept = [r for r in rows
                if not (isinstance(r, dict) and r.get("player") == DUPLICATE)]
        if len(kept) != len(rows):
            review[key] = kept
            changed = True
    if not changed:
        return False
    counts = review.get("counts")
    if isinstance(counts, dict):
        for key in ("wrong_entity", "date_disagreement", "still_missing",
                    "wikipedia_url_wrong_person"):
            if isinstance(review.get(key), list):
                counts[key] = len(review[key])
        if isinstance(review.get("wrong_entity"), list):
            counts["wrong_entity_replaced"] = sum(
                1 for r in review["wrong_entity"] if r.get("replacement_found"))
    _write_sorted(REVIEW, review)
    return True


def main() -> None:
    for label, done in (
            ("data/players/nba_players_careers.json", _merge_records(CAREERS)),
            ("nba_players_careers_READY.json",
             _merge_records(READY) if READY.exists() else False),
            ("data/players/player_bio.json", _drop_bio()),
            ("data/players/bio_needs_review.json", _drop_review())):
        print(f"{label}: {'merged' if done else 'already merged'}")

    careers = {r["player"]: r for r in
               json.loads(CAREERS.read_text(encoding="utf-8"))}
    assert DUPLICATE not in careers and PRIMARY in careers
    assert DUPLICATE in careers[PRIMARY].get("aliases", [])
    pelicans = [s for s in careers[PRIMARY]["career_history"]
                if s.get("team") == "New Orleans Pelicans"]
    assert len(pelicans) == 1, pelicans
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert DUPLICATE not in {r.get("player") for r in
                             review.get("wikipedia_url_wrong_person") or []}
    print("sanity checks PASS")


if __name__ == "__main__":
    main()

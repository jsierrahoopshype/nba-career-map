"""One-time merge of the 8 duplicate pairs created by run d395807, plus
wikipedia_url/display_name backfill and {{nbay}} year cleanup.

Each pair is the same player stored twice — an existing (seed) record and a
new record the pre-dedupe run inserted under a different spelling. Merge rules:

  * primary key `player` = the EXISTING (ASCII) spelling — it is load-bearing
    in the map/quiz (index.html keys on it, PLAYERS_400_GAMES etc. are ASCII),
    so it must not change. The canonical spelling goes in `display_name`.
  * career_history / status / current_team come from the record that reflects
    the current Wikipedia page: the freshly-fetched new record when it parsed
    (richer or equal history), else the existing record — so the empty
    "A. J. Green" disambiguation parse never overwrites the real AJ Green.
  * the dropped spelling is kept as an `alias` (so lookups by it still resolve;
    Database indexes aliases, which also prevents the dup reappearing).

Also: backfill wikipedia_url + display_name on the other new records from that
run, and expand any leftover {{nbay|YYYY|...}} year strings.

Idempotent. Run:  python3 scripts/merge_migration.py
"""
from __future__ import annotations

import re

import update_careers as uc
from names import canonical_url
from player_status import NBA_ACTIVE, OVERSEAS_ACTIVE, RETIRED

# (new_spelling_to_remove, existing_primary_key, canonical_display_name)
# Display names follow the canonical Wikipedia title: diacritics/suffixes where
# Wikipedia uses them; "AJ Green"/"AJ Lawson" (not "A. J. …") and "Carlton
# Carrington" (Wikipedia titles him Carlton, "Bub" is the alias).
PAIRS = [
    ("Alperen Şengün",    "Alperen Sengun",     "Alperen Şengün"),
    ("Dennis Schröder",   "Dennis Schroeder",   "Dennis Schröder"),
    ("Craig Porter Jr.",  "Craig Porter",       "Craig Porter Jr."),
    ("Dereck Lively II",  "Dereck Lively",      "Dereck Lively II"),
    ("Derrick Jones Jr.", "Derrick Jones",      "Derrick Jones Jr."),
    ("A. J. Green",       "AJ Green",           "AJ Green"),
    ("A. J. Lawson",      "AJ Lawson",          "AJ Lawson"),
    ("Bub Carrington",    "Carlton Carrington", "Carlton Carrington"),
]

_NBAY = re.compile(r"\{\{\s*nbay\s*\|\s*(\d{4})[^{}]*\}\}", re.IGNORECASE)


def _clean_year(val: str) -> str:
    v = _NBAY.sub(r"\1", str(val or ""))
    v = re.sub(r"\{\{[^{}]*\}\}", "", v)
    return re.sub(r"\s+", " ", v).strip()


def _clean_history(hist: list) -> list:
    out = []
    for s in hist or []:
        s = dict(s)
        s["years"] = _clean_year(s.get("years", ""))
        out.append(s)
    return out


def main() -> None:
    db = uc.Database()
    merged, missing = [], []

    for new_name, primary, display in PAIRS:
        old = db.by_name.get(primary)
        new = db.by_name.get(new_name)
        if old is None:
            missing.append(primary)
            continue
        if new is None:
            # already merged on a prior run — just ensure display/alias present
            old["display_name"] = display
            _add_alias(old, new_name)
            old["career_history"] = _clean_history(old.get("career_history", []))
            continue

        new_valid = bool(new.get("career_history"))
        use_new = new_valid and len(new.get("career_history", [])) >= \
            len(old.get("career_history", []))
        src = new if use_new else old

        rec = dict(old)  # keep existing fields (wikipedia_url, etc.) as base
        rec["player"] = primary
        rec["display_name"] = display
        rec["career_history"] = _clean_history(src.get("career_history", []))
        rec["current_team"] = src.get("current_team", "")
        rec["status"] = src.get("status", old.get("status"))  # current-page truth
        for f in ("position", "number", "birth_date", "birth_place", "death_date",
                  "death_place", "high_school", "college", "draft", "parse_status"):
            val = src.get(f) or new.get(f) or old.get(f)
            if val:
                rec[f] = val
        if not rec.get("wikipedia_url"):
            rec["wikipedia_url"] = canonical_url(primary)
        # aliases: the removed spelling + the new record's display, minus primary
        for alt in (new_name, new.get("display_name")):
            _add_alias(rec, alt, primary)

        db.by_name[primary] = rec
        db.by_name.pop(new_name, None)
        merged.append((new_name, primary, rec["status"]))

    db.order = [n for n in db.order if n in db.by_name]

    # backfill wikipedia_url + display_name on the other new records from the run
    backfilled = _backfill_new_records(db)
    # expand any remaining {{nbay}} year strings across the DB
    cleaned = 0
    for rec in db.by_name.values():
        before = [s.get("years") for s in rec.get("career_history", [])]
        rec["career_history"] = _clean_history(rec.get("career_history", []))
        if [s.get("years") for s in rec["career_history"]] != before:
            cleaned += 1

    _persist(db, merged, backfilled, cleaned)

    print(f"pairs merged     : {len(merged)}")
    for n, p, st in merged:
        print(f"  '{n}' -> '{p}'  (status={st})")
    if missing:
        print(f"missing primaries (skipped): {missing}")
    print(f"url/display backfilled: {backfilled}")
    print(f"records with {{{{nbay}}}} cleaned: {cleaned}")
    print(f"players total    : {len(db.by_name)}")
    # sanity
    assert "Bub Carrington" not in db.by_name and "Carlton Carrington" in db.by_name
    assert db.by_name["Carlton Carrington"]["display_name"] == "Carlton Carrington"
    assert "Bub Carrington" in db.by_name["Carlton Carrington"].get("aliases", [])
    print("sanity checks PASS")


def _add_alias(rec: dict, alt: str, primary: str | None = None) -> None:
    primary = primary or rec.get("player")
    if not alt or alt == primary:
        return
    al = set(rec.get("aliases", []))
    al.add(alt)
    rec["aliases"] = sorted(al)


def _backfill_new_records(db: uc.Database) -> int:
    """wikipedia_url + display_name for records still missing a URL (the run's
    56 genuine-new records). Their name is already the article title."""
    n = 0
    for rec in db.by_name.values():
        if not rec.get("wikipedia_url"):
            url = canonical_url(rec["player"])
            if not url:  # empty/blank player name — nothing to backfill
                continue
            rec["wikipedia_url"] = url
            rec.setdefault("display_name", rec["player"])
            n += 1
    return n


def _persist(db: uc.Database, merged, backfilled, cleaned) -> None:
    players = [db.by_name[n] for n in db.order]
    uc.write_json(uc.CAREERS, players)
    uc.write_json(uc.LOCATIONS, dict(sorted(db.locations.items())))
    uc.write_json(uc.REVIEW, dict(sorted(db.review.items())))

    nba = sorted(p["player"] for p in players if p.get("status") == NBA_ACTIVE)
    over = sorted(p["player"] for p in players if p.get("status") == OVERSEAS_ACTIVE)
    ret = sorted(p["player"] for p in players if p.get("status") == RETIRED)
    uc.write_json(uc.ACTIVE, {"count": len(nba) + len(over),
                              "nba_active": nba, "overseas_active": over})
    uc.write_json(uc.RETIRED, {"count": len(ret), "players": ret})

    map_players = []
    for p in players:
        mp = {"player": p["player"], "status": p.get("status", ""),
              "career_history": [{"years": s.get("years", ""), "team": s["team"],
                                  "city": s.get("city", ""), "state": s.get("state", ""),
                                  "country": s.get("country", "")}
                                 for s in p.get("career_history", [])]}
        if p.get("display_name") and p["display_name"] != p["player"]:
            mp["display_name"] = p["display_name"]
        if p.get("wikipedia_url"):
            mp["wikipedia_url"] = p["wikipedia_url"]
        map_players.append(mp)
    uc.write_json(uc.ROOT_MAP_FILE, map_players)

    logobj = uc.load_json(uc.UPDATE_LOG, {"runs": []})
    logobj["runs"].append({"date": uc.today(), "mode": "merge",
                           "merged_pairs": [{"from": n, "into": p} for n, p, _ in merged],
                           "url_backfilled": backfilled, "requests": 0})
    uc.write_json(uc.UPDATE_LOG, logobj)

    lines = [f"## {uc.today()} — dedupe merge migration", "",
             f"- Merged **{len(merged)}** duplicate player pairs (kept ASCII primary "
             f"key + canonical `display_name` + old spelling as alias)",
             f"- Backfilled wikipedia_url/display_name on **{backfilled}** records",
             f"- Expanded leftover `{{{{nbay}}}}` year strings on **{cleaned}** records",
             ""]
    for n, p, _ in merged:
        lines.append(f"  - `{n}` → `{p}`")
    lines.append("")
    existing = uc.CHANGELOG.read_text(encoding="utf-8") if uc.CHANGELOG.exists() else ""
    uc.CHANGELOG.write_text("\n".join(lines) + "\n" + existing, encoding="utf-8")


if __name__ == "__main__":
    main()

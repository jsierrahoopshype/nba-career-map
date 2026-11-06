"""Fill the `college` field for records that have none, from Basketball-Reference.

WHY. `college` comes from the Wikipedia infobox, and only records the pipeline
has re-parsed carry it. Most retired players were imported once and never
re-read, so Allen Iverson (Georgetown) had no college, and neither did ~3,900
other records. Their meta descriptions lose the "from <college> to <last team>"
close (see build_meta_description in scripts/prerender.py).

SOURCE. The public Basketball-Reference career-info export the bio fetcher
already uses (sumitrodatta/bball-reference-datasets, "Player Career Info.csv"):
one free request, no key. Its `colleges` column is BBRef's College field; "NA"
means none (straight from high school, or international), and such records
are LEFT EMPTY on purpose.

MATCHING. A record is matched to exactly one BBRef row or not at all:
  1. same normalized name (player / display_name / aliases) AND same birth date;
  2. else the name matches ONE row only, and the birth dates agree to within a
     year or BBRef has none (data-entry slips like 1930-09-19 vs 1930-09-09);
  3. else no name match at all (Hedo Turkoglu stored as "Hidayet Turkoglu",
     "JR Smith"): same birth date AND the same first or last name token.
Anything ambiguous is skipped and listed. Birth dates come from
data/players/player_bio.json (falling back to the record's own birth_date).

A record that already has a college is never touched, and the nightly keeps a
stored college when a fresh Wikipedia parse has none (merge_player in
update_careers.py), so this does not get undone.

Run:  python3 scripts/backfill_colleges.py [--csv PATH] [--apply]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names import normkey, strip_accents  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
BIO = ROOT / "data" / "players" / "player_bio.json"
LOG = ROOT / "logs" / "college_backfill.json"
BREF_CSV = ("https://raw.githubusercontent.com/sumitrodatta/"
            "bball-reference-datasets/master/Data/Player%20Career%20Info.csv")
UA = "nba-career-map/1.0 (college backfill; github.com/jsierrahoopshype/nba-career-map)"


def _tokens(name: str) -> list[str]:
    s = strip_accents(re.sub(r"\(.*?\)", " ", name or "")).lower()
    return [t for t in re.sub(r"[^a-z0-9]+", " ", s).split()
            if t not in ("jr", "sr", "ii", "iii", "iv")]


# Schools BBRef writes with a comma before the campus name.
_CAMPUS_PARENTS = {"University of California", "California State University",
                   "Montana State University", "State College of Florida"}


def format_colleges(raw: str) -> str:
    """BBRef lists schools comma-separated, in order attended. The stored
    format for several schools is the infobox's "* A * B" list, which
    prerender._college() splits on '*'; a single school is stored bare."""
    raw = (raw or "").strip()
    if not raw or raw.upper() == "NA":
        return ""
    parts: list[str] = []
    for piece in (x.strip() for x in raw.split(",")):
        if not piece:
            continue
        # A comma inside one school's name ("University of California,
        # Irvine"): glue the campus back onto the school it belongs to.
        if parts and (parts[-1] in _CAMPUS_PARENTS or piece.endswith(" Campus")):
            parts[-1] = f"{parts[-1]}, {piece}"
        else:
            parts.append(piece)
    return parts[0] if len(parts) == 1 else " ".join(f"* {p}" for p in parts)


def has_college(p: dict) -> bool:
    return bool((p.get("college") or "").strip())


def load_rows(path: str | None) -> list[dict]:
    if path:
        text = Path(path).read_text(encoding="utf-8")
    else:
        req = urllib.request.Request(BREF_CSV, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def match(p: dict, birth: str, by_key: dict, by_birth: dict):
    """(row, how) or (None, why)."""
    names = {p.get("player"), p.get("display_name"), *(p.get("aliases") or [])}
    cands = {}
    for n in names:
        for r in by_key.get(normkey(n or ""), []) if n else []:
            cands[r["player_id"]] = r
    if cands:
        if birth:
            same = [r for r in cands.values() if r.get("birth_date") == birth]
            if len(same) == 1:
                return same[0], "name+birth"
            if len(same) > 1:
                return None, "several same-name rows share the birth date"
        if len(cands) == 1:
            r = next(iter(cands.values()))
            theirs = r.get("birth_date") or ""
            if not birth or not re.match(r"\d{4}", theirs):
                return r, "unique name (a birth date missing)"
            if abs(int(theirs[:4]) - int(birth[:4])) <= 1:
                return r, f"unique name, birth {birth} vs BBRef {theirs}"
            return None, f"unique name but birth {birth} vs BBRef {theirs}"
        # "Charles Smith (1975)": the article's disambiguator is a birth year.
        years = {m for n in names if n for m in re.findall(r"\((\d{4})\)", n)}
        by_year = [r for r in cands.values() if (r.get("birth_date") or "")[:4] in years]
        if len(by_year) == 1:
            return by_year[0], "name + birth year in the article title"
        return None, f"{len(cands)} same-name rows, birth date does not pick one"
    if not birth:
        return None, "no name match and no birth date"
    toks = [_tokens(n or "") for n in names if n]
    firsts = {t[0] for t in toks if t}
    lasts = {t[-1] for t in toks if t}
    hits = [r for r in by_birth.get(birth, [])
            if (rt := _tokens(r["player"])) and (rt[0] in firsts or rt[-1] in lasts)]
    if len(hits) == 1:
        return hits[0], f"birth date + name token ({hits[0]['player']})"
    return None, ("no name match" if not hits
                  else f"{len(hits)} rows share birth date and a name token")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", help="local copy of the BBRef CSV (default: download)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rows = load_rows(args.csv)
    by_key: dict[str, list] = {}
    by_birth: dict[str, list] = {}
    for r in rows:
        by_key.setdefault(normkey(r.get("player", "")), []).append(r)
        if r.get("birth_date"):
            by_birth.setdefault(r["birth_date"], []).append(r)

    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    bio = json.loads(BIO.read_text(encoding="utf-8")) if BIO.exists() else {}
    before = sum(1 for p in players if not has_college(p))

    filled, none_at_bbref, unmatched = [], [], []
    for p in players:
        if has_college(p):
            continue
        birth = ((bio.get(p["player"]) or {}).get("birth_date")
                 or p.get("birth_date") or "")
        row, how = match(p, birth, by_key, by_birth)
        if row is None:
            unmatched.append({"player": p["player"], "why": how})
            continue
        college = format_colleges(row.get("colleges", ""))
        if not college and (row.get("pos") or "NA") == "NA":
            # A fresh debut BBRef has not filled in yet (every bio column NA):
            # unknown, not "no college".
            unmatched.append({"player": p["player"],
                              "why": f"BBRef row {row['player_id']} not filled in yet"})
            continue
        if not college:
            none_at_bbref.append({"player": p["player"], "bbref_id": row["player_id"]})
            continue
        filled.append({"player": p["player"], "college": college,
                       "bbref_id": row["player_id"], "how": how})
        if args.apply:
            p["college"] = college

    after = sum(1 for p in players if not has_college(p))
    print(f"records: {len(players)}")
    print(f"no college BEFORE: {before}")
    print(f"  filled from BBRef:                 {len(filled)}")
    print(f"  BBRef says none (HS/international): {len(none_at_bbref)}")
    print(f"  not matched (left empty):           {len(unmatched)}")
    print(f"no college AFTER{'' if args.apply else ' (dry run)'}: "
          f"{after if args.apply else before - len(filled)}")
    for f in filled:
        if f["how"] != "name+birth":
            print(f"  loose match: {f['player']} -> {f['college']} ({f['how']})")

    if args.apply:
        CAREERS.write_text(json.dumps(players, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
        LOG.write_text(json.dumps({
            "source": BREF_CSV, "before": before, "after": after,
            "filled": filled, "none_at_bbref": none_at_bbref,
            "unmatched": unmatched}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"written: {CAREERS.relative_to(ROOT)}, {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

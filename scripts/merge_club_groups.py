"""Fold 26 groups of club-name variants into one canonical club each.

Every group below is one club the database was carrying under two or more
names: a sponsor era ("Knorr Bologna"), a reserve side filed as its own club
("Real Madrid B"), a formal name next to its short form ("Joventut" /
"Joventut Badalona"), or a transliteration ("Atletico Madrid").

WHY THE ALIAS TABLE IS THE POINT. Rewriting the stored stints alone would
last exactly one pipeline run: the daily Wikipedia pass re-parses each page,
and whatever spelling the article carries comes back. So every variant is
written into data/teams/team_aliases.json, which is what
team_normalizer.TeamNormalizer consults on the way in -- the merge is applied
to the data AND taught to the parser, and the two can't drift apart. It also
keeps the move detector quiet: classify_move() normalizes both sides before
comparing, so a re-parse that reads "Herbalife Gran Canaria" resolves to
"Gran Canaria" and logs nothing.

Direction of each merge: the name the user named, where they named one;
otherwise the higher-usage spelling (stint count), with the count recorded in
the table below so the choice is auditable.

Consecutive stints: merging can leave a player with two adjacent stints at
what is now the same club (Pesaro's Scavolini era running into its VL era).
Those are joined into one span. Only stints this merge touched are eligible,
so a genuine two-spell career (a loan out and back) is left alone.

Idempotent. Run:  python3 scripts/merge_club_groups.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stint_order  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"
REVIEW = ROOT / "data" / "teams" / "teams_needing_review.json"

# canonical -> (variants, why). The canonical is the surviving club name.
GROUPS: dict[str, tuple[tuple[str, ...], str]] = {
    "Baskonia": (
        ("Taugrés/TAU Cerámica",),
        "sponsor eras of the Vitoria club; 'Tau Cerámica' was already aliased here",
    ),
    "Dafni": (
        ("Dafni Athens", "AO Dafni", "Dafni BC", "Dafnis B.C."),
        "one Athens club under five spellings; Dafni is a suburb of Athens, so "
        "the three variants filed under city 'Dafni' are the same club",
    ),
    "Maccabi Tel Aviv": (
        ("Maccabi Darom Tel Aviv",),
        "named by the user; Maccabi Tel Aviv carries 107 stints against 1",
    ),
    "Olympiacos": (("Olympiacos B",), "reserve side folded into the first team"),
    "FC Barcelona": (("FC Barcelona B",), "reserve side folded into the first team"),
    "Real Madrid": (("Real Madrid B",), "reserve side folded into the first team"),
    # REVERSED since the first pass, on request: the club now displays under
    # its full name and every short/sponsored form is an alias of it. The
    # alias table is rewritten accordingly -- see the flip handling in main().
    "İstanbul Teknik Üniversitesi": (
        ("İTÜ", "İTÜ BB", "Sigortam.net İTÜ BB"),
        "the club's full name is the display name; İTÜ, İTÜ BB and the "
        "sponsored Sigortam.net form are aliases of it",
    ),
    "Ülkerspor": (
        ("Ülker",),
        "same Istanbul club; 'Ülkerspor' is the higher-usage spelling (23 vs 2)",
    ),
    "İstanbul BB": (
        ("İstanbul BŞB",),
        "same club (Büyükşehir Belediyesi); 'İstanbul BB' is higher-usage (5 vs 2)",
    ),
    "Trotamundos de Carabobo": (
        ("Trotamundos B.B.C.",),
        "same Valencia (Venezuela) club; 42 stints against 2",
    ),
    "Valencia": (
        ("Valencia B",),
        "reserve side of the Spanish club folded into the first team",
    ),
    "Hapoel Jerusalem": (
        ("Hapoel Migdal Jerusalem",),
        "sponsor era (Migdal) of the same club; 78 stints against 1",
    ),
    "Sporting Club Gira": (
        ("Fernet Tonic Bologna",),
        "consecutive names of the same Bologna club; 'Sporting Club Gira' is "
        "higher-usage (2 vs 1)",
    ),
    "Virtus Bologna": (
        ("Knorr Bologna",),
        "named by the user: sponsor era folded into Virtus Bologna",
    ),
    "Mega Basket": (
        ("Mega Leks", "Mega Vizura", "Mega"),
        "sponsor eras and the bare short form of the Belgrade club",
    ),
    "Galatasaray": (
        ("Galatasaray Doğa Sigorta", "Galatasaray Liv Hospital", "Galatasaray Nef"),
        "sponsor eras folded into Galatasaray",
    ),
    "Žalgiris": (
        ("Žalgiris -Arvydas Sabonis School", "Žalgiris-2 Kaunas"),
        "academy and second team folded into Žalgiris",
    ),
    "Dynamo Moscow": (
        ("Dynamo Moscow Region",),
        "same club written with its oblast suffix",
    ),
    "Joventut Badalona": (
        ("Joventut",),
        "same Badalona club; the full name is higher-usage (64 stints vs 19)",
    ),
    "Capitanes de Arecibo": (
        ("Arecibo Captains",),
        "English rendering of the same Puerto Rican club; 64 stints against 1",
    ),
    "Cholet Basket": (
        ("Cholet Cedex Basket",),
        "same French club; 'Cholet Basket' is higher-usage (53 vs 1)",
    ),
    "Victoria Libertas Pesaro": (
        ("Scavolini Pesaro", "VL Pesaro"),
        "named by the user: sponsor era and abbreviation folded into the club name",
    ),
    "Benetton Treviso": (
        ("Benetton Basket", "Liberti / Benetton Treviso"),
        "named by the user: earlier names of the Treviso club",
    ),
    "Gran Canaria": (
        ("Gran Canaria B", "Herbalife Gran Canaria", "Telecom Gran Canaria"),
        "named by the user: sponsor eras and the reserve side",
    ),
    "Atlético Madrid": (
        ("Atlético Madrid Villalba", "Atletico Madrid"),
        "one club, two spellings and a venue suffix; the user asked for the "
        "accented spelling, so the club's own name is the survivor",
    ),
    "Pau-Orthez": (
        ("Élan Béarnais", "Élan Béarnais Pau-Lacq-Orthez", "Élan Béarnais Pau-Orthez"),
        "named by the user: every Élan Béarnais form folded into Pau-Orthez",
    ),

    # --- second pass ------------------------------------------------------
    "Beşiktaş": (
        ("Beşiktaş Sompo Japan",),
        "sponsor era, alongside the Gain and Icrypex forms already aliased",
    ),
    "Anyang KGC": (
        ("Anyang SBS", "Anyang SBS Stars", "Anyang KT&G Kites",
         "Anyang KGC Pro Basketball Club", "Anyang Jung Kwan Jang Red Boosters",
         # The last two are not names anyone wrote: they are two club names
         # that the infobox parser ran together. scripts/wiki_parser.py no
         # longer produces either shape; these entries clear the ones already
         # stored, and keep an old URL resolving.
         "Anyang SBS Stars·KT&G Kites",
         "Anyang KGCAnyang Jung Kwan Jang Red Boosters"),
        "every name of the one Anyang club, through its SBS, KT&G and KGC eras",
    ),
    "Limoges CSP": (
        ("CSP Limoges",),
        "the club's name written both ways round",
    ),
    "Khimki": (
        ("Khimki Moscow",),
        "same club, written with the metropolitan area instead of the town",
    ),
    "Lietuvos rytas": (
        ("Rytas Vilnius", "Rytas"),
        "short forms of the Vilnius club",
    ),
    "Union Olimpija": (
        ("Olimpija", "Olimpija Ljubljana", "Petrol Olimpija", "Smelt Olimpija"),
        "sponsor eras and short forms of the Ljubljana club. Cedevita Olimpija "
        "is deliberately NOT here: it is the 2019 merger with Cedevita Zagreb "
        "and a different club record",
    ),
    "Juvecaserta Basket": (
        ("Phonola Caserta", "Onyx Caserta", "Snaidero Caserta", "Pepsi Caserta",
         "Otto Caserta"),
        "five sponsor eras of the Caserta club",
    ),
    "Split": (
        ("Jugoplastika", "Jugoplastika / Pop 84 / Slobodna Dalmacija"),
        "the Yugoslav-era names of KK Split",
    ),
    "Brose Bamberg": (
        ("Bamberg Baskets", "TSK/GHP Bamberg"),
        "earlier names of the Bamberg club",
    ),
    "Pallacanestro Cantù": (
        ("Acqua S.Bernardo Cantù",),
        "sponsor era of the Cantù club",
    ),
    "Olympia Larissa": (
        ("Olimpia Larissa",),
        "one club, two transliterations",
    ),
    "Gymnastikos S. Larissas": (
        ("Gymnastikos Larissa",),
        "one club, two renderings of the same name",
    ),
    "Pınar Karşıyaka": (
        ("Karşıyaka Basket",),
        "same İzmir club; Pınar is the long-running sponsor",
    ),
    "Darüşşafaka": (
        ("Darüşşafaka Tekfen",),
        "sponsor era of the Istanbul club",
    ),
    "Peristeri": (
        ("Nikas Peristeri",),
        "sponsor era of the Athens club",
    ),
    "Estudiantes": (
        ("Estudiantes Mudespa",),
        "sponsor era of the MADRID club. The Argentine Estudiantes clubs "
        "(Bahía Blanca, Olavarría, Concordia) are separate and untouched",
    ),
}

# variant -> canonical, flattened.
VARIANT_TO_CANON: dict[str, str] = {
    v: canon for canon, (variants, _why) in GROUPS.items() for v in variants
}


def _key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


_CI = {_key(v): c for v, c in VARIANT_TO_CANON.items()}


def canon_of(team: str) -> str:
    return _CI.get(_key(team), team)


def merge_place(keep: dict, drop: dict) -> dict:
    """Canonical location wins; a variant only supplies what it left empty."""
    out = dict(keep)
    for field in ("city", "state", "country", "league"):
        if not (out.get(field) or "").strip():
            out[field] = drop.get(field, "")
    return out


def _span(start: int, end: int) -> str:
    if end >= stint_order.OPEN_END:
        return f"{start}–present"
    return str(start) if end == start else f"{start}–{end}"


def join_consecutive(career: list, touched: set) -> tuple[list, int]:
    """Collapse adjacent stints at the same club into one span.

    Only runs where at least one side was re-pointed by this merge (`touched`
    holds the id() of every stint dict this script rewrote), so a career that
    genuinely records two separate spells at one club keeps both.
    """
    if not career:
        return career, 0
    ordered = stint_order.sort_career(career)
    out: list = []
    joined = 0
    for stint in ordered:
        prev = out[-1] if out else None
        same_club = prev is not None and (prev.get("team") or "") == (stint.get("team") or "")
        eligible = same_club and (id(prev) in touched or id(stint) in touched)
        if not eligible:
            out.append(stint)
            continue
        ps, pe = stint_order.bounds(prev.get("years"))
        cs, ce = stint_order.bounds(stint.get("years"))
        prev["years"] = _span(min(ps, cs), max(pe, ce))
        # A loan is only a loan if BOTH halves were; otherwise the joined span
        # covers a real, non-loan spell as well.
        if not stint.get("loan"):
            prev.pop("loan", None)
        joined += 1
    return out, joined


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    alias_doc = json.loads(ALIASES.read_text(encoding="utf-8"))

    dbs = [(CAREERS, json.loads(CAREERS.read_text(encoding="utf-8")))]
    if READY.exists():
        dbs.append((READY, json.loads(READY.read_text(encoding="utf-8"))))

    # Home of each surviving club: its own entry, topped up from the variants.
    homes: dict[str, dict] = {}
    for canon, (variants, _why) in GROUPS.items():
        home = dict(loc.get(canon) or {})
        for v in variants:
            home = merge_place(home, loc.get(v) or {})
        home["team"] = canon
        homes[canon] = home

    per_group: dict[str, int] = {}
    joins = 0
    for path, db in dbs:
        for p in db:
            touched: set = set()
            for s in p.get("career_history") or []:
                old = (s.get("team") or "").strip()
                new = canon_of(old)
                if new == old:
                    continue
                home = homes[new]
                s["team"] = new
                s["city"] = home.get("city", "")
                s["state"] = home.get("state", "")
                s["country"] = home.get("country", "")
                touched.add(id(s))
                if path == CAREERS:
                    per_group[new] = per_group.get(new, 0) + 1
            if touched:
                p["career_history"], n = join_consecutive(p["career_history"], touched)
                if path == CAREERS:
                    joins += n
            # current_team is stored, not derived, so it needs the same pass.
            ct = (p.get("current_team") or "").strip()
            if canon_of(ct) != ct:
                p["current_team"] = canon_of(ct)

    for canon, (variants, why) in GROUPS.items():
        print(f"{canon!r}  <- {', '.join(repr(v) for v in variants)}")
        print(f"    {why}")
        print(f"    {per_group.get(canon, 0)} stint(s) re-pointed; place: "
              f"{homes[canon].get('city') or '-'}, {homes[canon].get('country') or '-'}")
    print(f"\n{sum(per_group.values())} stint(s) re-pointed across {len(GROUPS)} "
          f"group(s); {joins} consecutive stint pair(s) joined")

    if not args.apply:
        return 0

    aliases = alias_doc["aliases"]
    for variant, canon in VARIANT_TO_CANON.items():
        aliases[variant] = canon
    # An existing alias pointing at a name this run merged away would otherwise
    # become a two-hop lookup the normalizer does not follow ('7Up Joventut' ->
    # 'Joventut' -> 'Joventut Badalona'), so collapse those to the survivor.
    rechained = 0
    for k, v in list(aliases.items()):
        target = canon_of(v)
        if target != v and k != target:
            aliases[k] = target
            rechained += 1
    # Two shapes of dead alias, dropped after the re-chain so it can fix what
    # it can first:
    #   - a key that is now a SURVIVING canonical name. Flipping a merge's
    #     direction leaves exactly this ("İstanbul Teknik Üniversitesi" ->
    #     "İTÜ" after the pair was reversed), and it maps a club off its own
    #     name -- the worst kind of alias, because normalize() applies it.
    #   - a self-alias (k == v), which does nothing at all.
    dead = 0
    for k, v in list(aliases.items()):
        if k == v or k in GROUPS:
            del aliases[k]
            dead += 1
    alias_doc["aliases"] = dict(sorted(aliases.items()))
    ALIASES.write_text(json.dumps(alias_doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    for canon in GROUPS:
        loc[canon] = homes[canon]
    for variant in VARIANT_TO_CANON:
        loc.pop(variant, None)
    LOCATIONS.write_text(
        json.dumps(dict(sorted(loc.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    if REVIEW.exists():
        review = json.loads(REVIEW.read_text(encoding="utf-8"))
        dropped = [k for k in review if k in VARIANT_TO_CANON]
        for k in dropped:
            review.pop(k, None)
        REVIEW.write_text(
            json.dumps(dict(sorted(review.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"teams_needing_review: {len(dropped)} merged-away name(s) dropped")

    for path, db in dbs:
        path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"aliases: {len(VARIANT_TO_CANON)} written, {rechained} re-chained, "
          f"{dead} dead entr(ies) dropped")
    print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

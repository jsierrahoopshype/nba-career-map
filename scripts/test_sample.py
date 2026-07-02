"""Smoke test: fetch + parse a small sample of players from Wikipedia.

Run this first to verify the Wikipedia API path and parser end-to-end before
trusting a full update run:

    python3 scripts/test_sample.py

It fetches 10 well-known players (real network calls, ~10s with the 1s delay),
parses each, and prints a compact summary. It does NOT modify the database.
"""
from __future__ import annotations

import json

from wikipedia_api import WikipediaClient
from team_normalizer import TeamNormalizer
from wiki_parser import parse_player

SAMPLE = [
    "LeBron James", "Stephen Curry", "Nikola Jokić", "Luka Dončić",
    "Giannis Antetokounmpo", "Victor Wembanyama", "Kevin Durant",
    "Pau Gasol", "Manu Ginóbili", "Kareem Abdul-Jabbar",
]


def main() -> None:
    client = WikipediaClient(delay=1.0, max_requests=100)
    tn = TeamNormalizer()
    ok = 0
    for name in SAMPLE:
        wt = client.get_wikitext(name)
        if not wt:
            print(f"✗ {name}: page not found")
            continue
        rec = parse_player(wt, name, tn)
        rec.pop("_raw_teams", None)
        stints = rec["career_history"]
        if rec["status"] == "success":
            ok += 1
        teams = " → ".join(f"{s['team']} ({s['years']})" for s in stints[:6])
        print(f"{'✓' if rec['status']=='success' else '✗'} {name}: "
              f"pos={rec['position']!r} #={rec['number']} "
              f"born={rec['birth_date']} teams={len(stints)}")
        print(f"    {teams}")
    print(f"\n{ok}/{len(SAMPLE)} parsed successfully; "
          f"{client.requests_made} requests used")


if __name__ == "__main__":
    main()

"""Smoke test: fetch + parse a small sample of players from Wikipedia.

Run this first to verify the Wikipedia API path and parser end-to-end before
trusting a full update run:

    python3 scripts/test_sample.py

It fetches 10 well-known players (real network calls, ~10s with the 1s delay),
parses each, and prints a compact summary. It does NOT modify the database.

WHEN WIKIPEDIA WON'T ANSWER. A 429 (rate limited) or a 5xx is Wikipedia's
server saying no, not a bug in this code. Each request is retried with
backoff (honouring Retry-After); a player whose page still cannot be fetched
is reported as NOT_TESTED_SERVER instead of failing -- the same rule the
nba-matchups verify step uses for the NBA's server errors. Nothing else is
excused: a missing page, a parse that is not "success", a stint with no team
or years, any other HTTP status (403, 404, ...), a timeout or a crash still
fails (exit 1). The run passes only if every player that WAS fetched is
right; if none could be fetched it says NOT TESTED and exits 0.
"""
from __future__ import annotations

import sys
import time
import urllib.error

from wikipedia_api import WikipediaClient
from team_normalizer import TeamNormalizer
from wiki_parser import parse_player

SAMPLE = [
    "LeBron James", "Stephen Curry", "Nikola Jokić", "Luka Dončić",
    "Giannis Antetokounmpo", "Victor Wembanyama", "Kevin Durant",
    "Pau Gasol", "Manu Ginóbili", "Kareem Abdul-Jabbar",
]

NOT_TESTED_SERVER = "NOT TESTED (Wikipedia server error, retried)"
# Waits between attempts, in seconds: 4 attempts in all. A longer Retry-After
# from the server wins, capped so one player can't stall the run for minutes.
BACKOFF = (5, 15, 45)
MAX_RETRY_AFTER = 120


class ServerUnavailable(Exception):
    """Wikipedia answered 429 or 5xx on every attempt."""


def is_server_refusal(exc: BaseException) -> bool:
    """429 or 5xx: the server declining to serve. Nothing else qualifies."""
    return isinstance(exc, urllib.error.HTTPError) and (
        exc.code == 429 or 500 <= exc.code <= 599)


def _retry_after(exc: urllib.error.HTTPError) -> float:
    try:
        return min(float((exc.headers or {}).get("Retry-After", 0)), MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        return 0.0


def fetch_with_backoff(fetch, name: str, backoff=BACKOFF, sleep=time.sleep):
    """fetch(name), retried on 429/5xx. Raises ServerUnavailable when every
    attempt was refused; any other error propagates untouched."""
    last = None
    for attempt in range(len(backoff) + 1):
        try:
            return fetch(name)
        except urllib.error.HTTPError as exc:
            if not is_server_refusal(exc):
                raise
            last = exc
            if attempt < len(backoff):
                sleep(max(backoff[attempt], _retry_after(exc)))
    raise ServerUnavailable(f"HTTP {last.code} after {len(backoff) + 1} attempts")


def check_record(rec: dict) -> list[str]:
    """What is wrong with a parsed record (empty list = nothing)."""
    problems = []
    if rec.get("status") != "success":
        problems.append(f"parse status {rec.get('status')!r}")
    stints = rec.get("career_history") or []
    if not stints:
        problems.append("no career stints")
    for s in stints:
        if not (s.get("team") or "").strip() or not (s.get("years") or "").strip():
            problems.append(f"stint missing team/years: {s}")
            break
    return problems


def run(fetch, parse, names=SAMPLE, backoff=BACKOFF, sleep=time.sleep) -> int:
    """Exit code: 0 = every fetched player parsed right (or none could be
    fetched: NOT TESTED), 1 = something is actually wrong."""
    ok, failed, untested = [], [], []
    for name in names:
        try:
            wt = fetch_with_backoff(fetch, name, backoff, sleep)
        except ServerUnavailable as exc:
            untested.append(name)
            print(f"- {name}: {NOT_TESTED_SERVER}: {exc}")
            continue
        if not wt:
            failed.append(name)
            print(f"✗ {name}: page not found")
            continue
        rec = parse(wt, name)
        rec.pop("_raw_teams", None)
        problems = check_record(rec)
        stints = rec.get("career_history") or []
        print(f"{'✗' if problems else '✓'} {name}: "
              f"pos={rec.get('position')!r} #={rec.get('number')} "
              f"born={rec.get('birth_date')} teams={len(stints)}")
        teams = " → ".join(f"{s.get('team')} ({s.get('years')})" for s in stints[:6])
        print(f"    {teams}")
        if problems:
            failed.append(name)
            print(f"    FAIL: {'; '.join(problems)}")
        else:
            ok.append(name)

    print(f"\n{len(ok)}/{len(names)} parsed successfully, {len(failed)} failed, "
          f"{len(untested)} not tested")
    if failed:
        print(f"RESULT: FAIL ({', '.join(failed)})")
        return 1
    if not ok:
        print(f"RESULT: {NOT_TESTED_SERVER} -- every request was refused")
        return 0
    if untested:
        print(f"RESULT: PASS, {len(untested)} {NOT_TESTED_SERVER}: {', '.join(untested)}")
        return 0
    print("RESULT: PASS")
    return 0


def main() -> int:
    client = WikipediaClient(delay=1.0, max_requests=100)
    tn = TeamNormalizer()
    code = run(client.get_wikitext, lambda wt, name: parse_player(wt, name, tn))
    print(f"{client.requests_made} requests used")
    return code


if __name__ == "__main__":
    sys.exit(main())

"""Is a newly-DETECTED stint a newly-STARTED one?

THE BUG THIS EXISTS FOR. The transaction ledger logged a move whenever a
player's stored ``current_team`` changed between two runs. That is a diff of
what the pipeline had SEEN, not of what had HAPPENED: the rotation re-reads
each player every few days, and any Wikipedia edit that adds, reorders or
renames an old stint moves ``current_team`` and books a "signing" dated today.
So Lonnie Walker's 2025-26 season at Partizan surfaced as a September 2026
signing, and Jalen Crutcher's December 2025 move to the Iowa Wolves as a
September 2026 one.

THE RULE. A detected move counts as a new signing only if the destination
stint STARTED on or after the day the ledger opened (2026-07-11). Anything
older is the pipeline catching up with history, not news.

WHAT THE SOURCE CAN AND CANNOT TELL US. Wikipedia career stints are
year-granular ("2026-present"), so most of the time the only available signal
is the start year, and the comparison runs at season granularity: a stint
starting in the ledger's own year counts (a 2026-27 season begins after
2026-07-11 and there is nothing finer to go on), one starting earlier does
not. Where a real signing date IS known it is carried on the stint as
``start_date`` (see data/teams/stint_start_dates.json) and compared exactly.

UNDETERMINED IS NOT OLD. When the destination has no dated stint at all --
a club renamed between runs, a current_team the infobox lists without a
matching career row -- the guard says so rather than guessing. The live
pipeline lets those through (suppressing them would silently drop real
signings, which is the failure mode that actually costs a reader something),
and the historical cleanup leaves them in place.
"""
from __future__ import annotations

import datetime as dt
import re

# The day the ledger opened: its oldest entry. Nothing before it was ever
# recorded, so nothing before it can be news.
LEDGER_START = dt.date(2026, 7, 11)

_YEAR = re.compile(r"\d{4}")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_iso(value) -> dt.date | None:
    m = _ISO.match(str(value or "").strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def stint_start_year(stint: dict) -> int | None:
    """The year a stint began: its exact start_date's year, else the first
    four-digit year in its `years` span."""
    exact = parse_iso((stint or {}).get("start_date"))
    if exact:
        return exact.year
    m = _YEAR.search(str((stint or {}).get("years") or ""))
    return int(m.group()) if m else None


def latest_stint_at(record: dict, team: str) -> dict | None:
    """The player's most recent stint at `team`, or None.

    Most recent, not first: a player who returns to a club he left years ago
    must be judged on the spell he just started, not the one he finished.
    """
    team = (team or "").strip()
    if not team:
        return None
    found = None
    for stint in (record or {}).get("career_history") or []:
        if (stint.get("team") or "").strip() == team:
            found = stint
    return found


def is_new_signing(record: dict, to_team: str,
                   ledger_start: dt.date = LEDGER_START) -> tuple[bool | None, str]:
    """(verdict, reason) for a detected move to `to_team`.

    verdict is True (started on/after the ledger opened -- a real signing),
    False (started before it -- the pipeline catching up with history), or
    None (no dated stint to judge by; see the module docstring).
    """
    stint = latest_stint_at(record, to_team)
    if stint is None:
        return None, f"no stint at {to_team!r} to date"
    exact = parse_iso(stint.get("start_date"))
    if exact:
        if exact >= ledger_start:
            return True, f"signed {exact.isoformat()}"
        return False, f"signed {exact.isoformat()}, before the ledger opened"
    year = stint_start_year(stint)
    if year is None:
        return None, f"stint {stint.get('years')!r} carries no year"
    if year >= ledger_start.year:
        return True, f"stint starts {year}"
    return False, (f"stint starts {year} ({stint.get('years')!r}), "
                   f"before the ledger opened in {ledger_start.year}")


def counts_as_signing(record: dict, to_team: str,
                      ledger_start: dt.date = LEDGER_START) -> bool:
    """Live-pipeline policy: log it unless the guard is SURE it is old."""
    return is_new_signing(record, to_team, ledger_start)[0] is not False


# --- curated exact start dates ----------------------------------------------
# Wikipedia gives years, not dates. Where the real signing date is known it
# lives in data/teams/stint_start_dates.json and is stamped back onto the
# stint after every parse, so the daily run -- which rebuilds career_history
# from the article each time -- cannot quietly drop it. Matching is on player
# + team + start YEAR rather than the exact `years` string, so a span that
# later closes ("2025-present" -> "2025-2026") still matches.
from pathlib import Path  # noqa: E402
import json  # noqa: E402

START_DATES = (Path(__file__).resolve().parent.parent / "data" / "teams"
               / "stint_start_dates.json")


def load_start_dates(path: Path = START_DATES) -> list[dict]:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("overrides", []) if isinstance(doc, dict) else doc
    return [r for r in rows if r.get("player") and r.get("team")
            and parse_iso(r.get("start_date"))]


def apply_start_dates(players, path: Path = START_DATES) -> int:
    """Stamp the curated start dates onto matching stints. Returns the count."""
    rows = load_start_dates(path)
    if not rows:
        return 0
    by_player: dict[str, list] = {}
    for r in rows:
        by_player.setdefault(r["player"], []).append(r)
    stamped = 0
    for p in players:
        for name in {p.get("player"), p.get("display_name")}:
            for r in by_player.get(name or "", []):
                for stint in p.get("career_history") or []:
                    if (stint.get("team") or "").strip() != r["team"]:
                        continue
                    year = r.get("start_year")
                    if year is not None and stint_start_year(stint) != year:
                        continue
                    if stint.get("start_date") != r["start_date"]:
                        stint["start_date"] = r["start_date"]
                        stamped += 1
                    break
    return stamped

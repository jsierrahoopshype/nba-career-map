"""Birth dates from NBA data: nba-player-data first, NBA.com for the gaps.

This is the birth date that decides `birth_date` in data/players/player_bio.json
and the one every Wikidata / Wikipedia identity check measures a candidate
against (scripts/fetch_bio_wikidata.py, scripts/resolve_player_urls.py,
scripts/backfill_colleges.py).

WHERE IT COMES FROM, in order:

  1. nba-player-data `bio.json` (BIRTHDAY, M/D/YYYY) -- the bio data the site
     already keeps. Read from the nba-player-data repo on GitHub, or from a
     local copy (--npd-bio). Source label: "nba-player-data".
  2. NBA.com (stats.nba.com commonplayerinfo, BIRTHDATE), only for players
     bio.json has no date for, and only for players with a known NBA person ID
     (nba-player-data `player-headshots.json`, and PLAYER_ID in its season
     files). stats.nba.com often refuses cloud runners, so a refusal or a
     timeout is a warning, never a failure: the player simply goes on to the
     Wikidata fallback. Every answer is kept in data/players/nba_com_birthdates.json
     so a date fetched once is never asked for again. Source label: "nba.com".

Wikidata is the fallback after both, and it lives in fetch_bio_wikidata.py.

MATCHING. A career record is matched to a bio.json row by its exact player key
first ("Charles Smith (1975)" is a bio.json key too), then by the normalized
name of its key, display name or aliases (names.normkey) -- but only when that
normalized name belongs to ONE bio.json row and ONE career record. Two rows
or two records under one name is a coin toss, and is left unmatched.

A name is not a person, though: bio.json's "Kevin Porter" is the one born in
2000, the career database's is the one whose first stint was in 1972. So a
matched row is only taken when its birth date fits the record's own career --
born 14 to 40 years before his first stint (CAREER_AGE) -- and, when his
Wikipedia article's title names a birth year ("Roger Brown (basketball, born
1942)"), within a year of it. A row that does not fit is set aside as a
suspect. A career that lists a youth club can start
before 14, so the bio fetcher still takes a suspect date when the player's own
gated Wikidata item puts his birth within a year of it (two sources agreeing
on the man); otherwise the row is treated as a namesake's, listed
(`nba_row_rejected` in bio_needs_review.json) and not used, and Wikidata
decides that player's date.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names import normkey  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NPD_RAW = "https://raw.githubusercontent.com/jsierrahoopshype/nba-player-data/main/"
NPD_BIO_URL = NPD_RAW + "bio.json"
NPD_IDS_URL = NPD_RAW + "player-headshots.json"
NPD_SEASON_URL = NPD_RAW + "nba-current-season-data.json"
NBA_COM_URL = "https://stats.nba.com/stats/commonplayerinfo"
NBA_COM_CACHE = ROOT / "data" / "players" / "nba_com_birthdates.json"

SRC_NPD = "nba-player-data"
SRC_NBA_COM = "nba.com"
NBA_SOURCES = (SRC_NPD, SRC_NBA_COM)

# stats.nba.com answers a browser, not a script; and it is slow to refuse.
NBA_COM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json",
}
NBA_COM_TIMEOUT = 20
NBA_COM_LIMIT = 40          # live NBA.com requests per run
NBA_COM_GIVE_UP = 2         # consecutive failures before the run stops asking

# A row's birth date must put the player this many years old (youngest,
# oldest) at the start of his first stint in the career database.
CAREER_AGE = (14, 40)

_MDY = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$")
_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})")


def parse_birthday(value) -> str:
    """bio.json's "10/4/1963" (or an ISO date) -> "1963-10-04"; "" when unreadable."""
    s = str(value or "").strip()
    m = _MDY.match(s)
    if m:
        mo, d, y = (int(x) for x in m.groups())
    else:
        m = _ISO.match(s)
        if not m:
            return ""
        y, mo, d = (int(x) for x in m.groups())
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return ""


def _names(player: dict) -> list[str]:
    out = [player.get("player"), player.get("display_name"),
           *(player.get("aliases") or [])]
    return [n for n in dict.fromkeys(out) if n]


def first_stint_year(player: dict) -> int | None:
    years = []
    for s in player.get("career_history") or []:
        m = re.match(r"^\s*(\d{4})", str((s or {}).get("years") or "")) \
            if isinstance(s, dict) else None
        if m:
            years.append(int(m.group(1)))
    return min(years) if years else None


_TITLE_BORN = re.compile(r"born[_ ](\d{4})\)")


def title_birth_year(player: dict) -> int | None:
    """The birth year his article's title gives, if it gives one."""
    import player_urls  # noqa: PLC0415 -- only needed here
    url = (player_urls.override_url(player.get("player") or "")
           or player.get("wikipedia_url") or "")
    m = _TITLE_BORN.search(urllib.parse.unquote(url))
    return int(m.group(1)) if m else None


def fits_career(player: dict, date: str) -> bool:
    """Could a man born on `date` have started this record's career (and be
    the man his article's title says)?"""
    if not date:
        return True
    titled = title_birth_year(player)
    if titled is not None and abs(titled - int(date[:4])) > 1:
        return False
    start = first_stint_year(player)
    if start is None:
        return True
    age = start - int(date[:4])
    return CAREER_AGE[0] <= age <= CAREER_AGE[1]


def _why_not(player: dict, date: str) -> str:
    titled = title_birth_year(player)
    if titled is not None and abs(titled - int(date[:4])) > 1:
        return f"born {date}, but his article's title says born {titled}"
    start = first_stint_year(player)
    return f"born {date}, {start - int(date[:4])} years before his first listed stint"


def match_npd(players: list[dict], rows: list[dict],
              rejected: list | None = None) -> dict[str, str]:
    """{career player key: ISO birth date} from bio.json rows.

    A row whose date does not fit the record's career is left out and, when
    `rejected` is given, appended to it."""
    exact: dict[str, str] = {}
    by_norm: dict[str, set[str]] = {}
    for r in rows:
        name = str(r.get("PLAYER") or "").strip()
        if not name:
            continue
        exact[name] = parse_birthday(r.get("BIRTHDAY"))
        by_norm.setdefault(normkey(name), set()).add(name)

    career_norm: dict[str, set[str]] = {}
    for p in players:
        for n in _names(p):
            career_norm.setdefault(normkey(n), set()).add(p["player"])

    out: dict[str, str] = {}
    for p in players:
        key = p.get("player") or ""
        row = key if key in exact else ""
        if not row:
            rows_hit: set[str] = set()
            for n in _names(p):
                k = normkey(n)
                if len(career_norm.get(k, ())) == 1:
                    rows_hit |= by_norm.get(k, set())
            if len(rows_hit) == 1:
                row = next(iter(rows_hit))
        date = exact.get(row, "") if row else ""
        if not date:
            continue
        if not fits_career(p, date):
            if rejected is not None:
                rejected.append({"player": key, "nba_player_data_row": row,
                                 "nba_birth_date": date,
                                 "first_stint": first_stint_year(p),
                                 "why": _why_not(p, date)})
            continue
        out[key] = date
    return out


def person_ids(headshots: dict | None, season: dict | None) -> dict[str, str]:
    """{normalized name: NBA person ID} from nba-player-data's own files."""
    out: dict[str, str] = {}
    for name, val in (headshots or {}).items():
        m = re.match(r"^(\d+)", str(val or ""))
        if m:
            out.setdefault(normkey(name), m.group(1))
    for rows in (season or {}).values():
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            pid = next((str(v) for k, v in r.items()
                        if k.endswith("PLAYER_ID") and str(v).isdigit()), "")
            name = r.get("PLAYER_NAME") or ""
            if pid and name:
                out.setdefault(normkey(name), pid)
    return out


def _nba_com_date(payload: dict) -> str:
    for rs in payload.get("resultSets") or []:
        if rs.get("name") != "CommonPlayerInfo":
            continue
        headers = rs.get("headers") or []
        for row in rs.get("rowSet") or []:
            rec = dict(zip(headers, row))
            return parse_birthday(str(rec.get("BIRTHDATE") or "")[:10])
    return ""


def fetch_nba_com(person_id: str, timeout: int = NBA_COM_TIMEOUT) -> str:
    """One player's BIRTHDATE from stats.nba.com. Raises OSError on refusal."""
    url = NBA_COM_URL + "?" + urllib.parse.urlencode({"PlayerID": person_id})
    req = urllib.request.Request(url, headers=NBA_COM_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _nba_com_date(json.loads(resp.read().decode("utf-8")))


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class NbaBirthDates:
    """{career player key: (date, source)} for one run."""

    def __init__(self, dates: dict[str, tuple[str, str]], *, note: str = "",
                 not_fetched: list[str] | None = None,
                 rejected: list[dict] | None = None):
        self.dates = dates
        self.note = note
        self.not_fetched = not_fetched or []
        self.rejected = rejected or []

    def suspect(self, name: str) -> str:
        """A bio.json date set aside because his career rules it out."""
        for r in self.rejected:
            if r["player"] == name:
                return r["nba_birth_date"]
        return ""

    def date(self, name: str) -> str:
        return (self.dates.get(name) or ("", ""))[0]

    def source(self, name: str) -> str:
        return (self.dates.get(name) or ("", ""))[1]

    def __bool__(self) -> bool:
        return bool(self.dates)

    def __len__(self) -> int:
        return len(self.dates)


def coerce(nba) -> NbaBirthDates:
    """An NbaBirthDates from itself, None, or a plain {player: date} dict
    (dates taken as nba-player-data's)."""
    if isinstance(nba, NbaBirthDates):
        return nba
    return NbaBirthDates({k: (v, SRC_NPD) for k, v in (nba or {}).items() if v})


def build(players: list[dict], npd_rows: list[dict], *,
          cache: dict | None = None, ids: dict[str, str] | None = None,
          fetch=None, limit: int = NBA_COM_LIMIT,
          today: str | None = None) -> tuple[NbaBirthDates, dict]:
    """(the run's NBA birth dates, the updated NBA.com cache).

    `fetch(person_id) -> date` is the live NBA.com call, or None to use the
    cache alone (offline runs and tests).
    """
    today = today or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    cache = dict(cache or {})
    rejected: list[dict] = []
    npd = match_npd(players, npd_rows, rejected)
    dates: dict[str, tuple[str, str]] = {k: (v, SRC_NPD) for k, v in npd.items()}

    gaps = [p for p in players if p.get("player") and p["player"] not in dates]
    not_fetched, asked, fails = [], 0, 0
    for p in gaps:
        name = p["player"]
        hit = cache.get(name) or {}
        if hit.get("birth_date"):
            if fits_career(p, hit["birth_date"]):
                dates[name] = (hit["birth_date"], SRC_NBA_COM)
            continue
        if hit.get("checked"):
            continue          # NBA.com answered before, with no date
        pid = hit.get("person_id") or next(
            (ids[normkey(n)] for n in _names(p) if normkey(n) in (ids or {})), "")
        if not pid:
            continue
        if fetch is None or asked >= limit or fails >= NBA_COM_GIVE_UP:
            not_fetched.append(name)
            continue
        asked += 1
        try:
            date = fetch(pid)
            fails = 0
        except (OSError, ValueError, urllib.error.URLError) as exc:
            fails += 1
            not_fetched.append(name)
            print(f"::warning::nba.com: no answer for {name} ({pid}): {exc}",
                  file=sys.stderr)
            continue
        cache[name] = {"person_id": pid, "birth_date": date, "checked": today}
        if date and fits_career(p, date):
            dates[name] = (date, SRC_NBA_COM)
    note = (f"{SRC_NPD} bio.json ({len(npd)} players), then {SRC_NBA_COM} "
            f"commonplayerinfo ({sum(1 for v in dates.values() if v[1] == SRC_NBA_COM)})")
    return (NbaBirthDates(dates, note=note, not_fetched=not_fetched,
                          rejected=rejected), cache)


def load(players: list[dict], *, get_text, npd_bio: Path | None = None,
         live_nba_com: bool = True, cache_path: Path = NBA_COM_CACHE,
         limit: int = NBA_COM_LIMIT) -> tuple[NbaBirthDates, dict]:
    """Read nba-player-data (from `npd_bio` or GitHub via `get_text(url)`) and
    the NBA.com cache, and ask NBA.com for what is still missing."""
    if npd_bio:
        rows = json.loads(Path(npd_bio).read_text(encoding="utf-8"))
    else:
        rows = json.loads(get_text(NPD_BIO_URL))
    if not isinstance(rows, list) or not rows:
        raise ValueError("nba-player-data bio.json: no rows")
    ids: dict[str, str] = {}
    fetch = None
    if live_nba_com:
        try:
            ids = person_ids(json.loads(get_text(NPD_IDS_URL)),
                             json.loads(get_text(NPD_SEASON_URL)))
        except Exception as exc:  # noqa: BLE001 -- the IDs only widen coverage
            print(f"::warning::nba-player-data person IDs unavailable, NBA.com "
                  f"not asked this run: {exc}", file=sys.stderr)
        fetch = fetch_nba_com
    return build(players, rows, cache=_read_json(cache_path, {}), ids=ids,
                 fetch=fetch, limit=limit)


def save_cache(cache: dict, path: Path = NBA_COM_CACHE) -> None:
    if not cache and not Path(path).exists():
        return
    Path(path).write_text(json.dumps(cache, ensure_ascii=False, indent=2,
                                     sort_keys=True) + "\n", encoding="utf-8")

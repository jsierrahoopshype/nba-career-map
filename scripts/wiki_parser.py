"""Parse a basketball player's Wikipedia wikitext into structured career data.

Targets the {{Infobox basketball biography}} template, which encodes career
history as paired ``yearsN`` / ``teamN`` fields plus biographical fields
(position, number, birth/death, high school, college, draft).

The parser is brace/bracket aware so nested templates ({{...}}) and wikilinks
([[...]]) inside field values do not confuse the ``|`` / ``=`` splitting.
"""
from __future__ import annotations

import re
from team_normalizer import TeamNormalizer

INFOBOX_RE = re.compile(r"\{\{\s*Infobox\s+basketball\s+biography", re.IGNORECASE)


def _find_infobox(text: str) -> str | None:
    """Return the raw text of the infobox template (without outer {{ }})."""
    m = INFOBOX_RE.search(text)
    if not m:
        return None
    i = m.start()
    depth = 0
    j = i
    while j < len(text) - 1:
        pair = text[j:j + 2]
        if pair == "{{":
            depth += 1
            j += 2
            continue
        if pair == "}}":
            depth -= 1
            j += 2
            if depth == 0:
                return text[i + 2:j - 2]
            continue
        j += 1
    return None


def _split_top_level(body: str, sep: str) -> list[str]:
    """Split on ``sep`` only at brace/bracket depth 0."""
    parts, buf = [], []
    tdepth = ldepth = 0
    i = 0
    while i < len(body):
        two = body[i:i + 2]
        if two == "{{":
            tdepth += 1; buf.append(two); i += 2; continue
        if two == "}}":
            tdepth = max(0, tdepth - 1); buf.append(two); i += 2; continue
        if two == "[[":
            ldepth += 1; buf.append(two); i += 2; continue
        if two == "]]":
            ldepth = max(0, ldepth - 1); buf.append(two); i += 2; continue
        ch = body[i]
        if ch == sep and tdepth == 0 and ldepth == 0:
            parts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    parts.append("".join(buf))
    return parts


def parse_infobox_fields(text: str) -> dict[str, str]:
    body = _find_infobox(text)
    if body is None:
        return {}
    fields: dict[str, str] = {}
    for chunk in _split_top_level(body, "|"):
        if "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        key = key.strip().lower()
        if key:
            fields[key] = val.strip()
    return fields


def _clean_text(val: str) -> str:
    if not val:
        return ""
    n = re.sub(r"<ref[^>]*>.*?</ref>", "", val, flags=re.DOTALL)
    n = re.sub(r"<ref[^>]*/>", "", n)
    n = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", n)
    n = n.replace("'''", "").replace("''", "")
    n = re.sub(r"<br\s*/?>", ", ", n)
    n = re.sub(r"<[^>]+>", "", n)
    # expand {{nbay|YYYY|start|end}} (NBA season year) to its year argument,
    # then strip any other leftover templates so raw wikitext never persists.
    n = re.sub(r"\{\{\s*nbay\s*\|\s*(\d{4})[^{}]*\}\}", r"\1", n, flags=re.IGNORECASE)
    n = re.sub(r"\{\{[^{}]*\}\}", "", n)
    n = re.sub(r"\s+", " ", n).strip().strip(",").strip()
    return n


def _parse_date_template(val: str) -> str:
    """Extract YYYY-MM-DD (or partial) from {{birth date...}} / {{death date...}}."""
    if not val:
        return ""
    m = re.search(r"\{\{\s*(?:birth|death)[^|}]*\|([^}]*)\}\}", val, re.IGNORECASE)
    if m:
        nums = re.findall(r"\d+", m.group(1))
        # templates that show age put the *death* date first, then birth; the
        # leading three numbers are the primary (Y, M, D) in either case.
        if len(nums) >= 3:
            y, mo, d = nums[0], nums[1], nums[2]
            if len(y) == 4:
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        if nums and len(nums[0]) == 4:
            return nums[0]
    return _clean_text(val)


def _parse_numbers(val: str) -> list[str]:
    cleaned = _clean_text(val)
    nums = re.findall(r"\d+", cleaned)
    seen, out = set(), []
    for n in nums:
        if n not in seen:
            seen.add(n); out.append(n)
    return out


def _parse_career_history(fields: dict[str, str], normalizer: TeamNormalizer):
    """Return (career_history, raw_team_names) from yearsN/teamN pairs."""
    idxs = set()
    for k in fields:
        m = re.fullmatch(r"(?:years|team)(\d+)", k)
        if m:
            idxs.add(int(m.group(1)))
    history, raw_names = [], []
    for i in sorted(idxs):
        team_raw = fields.get(f"team{i}", "")
        years = _clean_text(fields.get(f"years{i}", ""))
        if not team_raw:
            continue
        loan = "→" in team_raw or "(loan)" in team_raw.lower()
        team_clean = _clean_text(team_raw.replace("→", ""))
        team_clean = re.sub(r"\(loan\)", "", team_clean, flags=re.IGNORECASE).strip()
        if not team_clean:
            continue
        raw_names.append(team_clean)
        entry = {
            "team": normalizer.normalize(team_clean),
            "team_raw": team_clean,
            "years": years,
        }
        if loan:
            entry["loan"] = True
        history.append(entry)
    return history, raw_names


def parse_player(text: str, player_name: str, normalizer: TeamNormalizer) -> dict:
    """Parse full player wikitext into the project's player record shape."""
    fields = parse_infobox_fields(text)
    if not fields:
        return {"player": player_name, "status": "no_career_data", "career_history": []}

    history, raw_names = _parse_career_history(fields, normalizer)

    draft = {}
    for key in ("draft_year", "draft_round", "draft_pick", "draft_team", "draft"):
        if fields.get(key):
            draft[key.replace("draft_", "")] = _clean_text(fields[key])

    current_team = ""
    for entry in reversed(history):
        yrs = entry.get("years", "")
        if not yrs or "present" in yrs.lower() or re.search(r"[–\-]\s*$", yrs):
            current_team = entry["team"]
            break
    if not current_team and history:
        current_team = history[-1]["team"]

    record = {
        "player": player_name,
        "status": "success" if history else "no_career_data",
        "position": _clean_text(fields.get("position", "")),
        "number": _parse_numbers(fields.get("number", "")),
        "birth_date": _parse_date_template(fields.get("birth_date", "")),
        "birth_place": _clean_text(fields.get("birth_place", "")),
        "high_school": _clean_text(fields.get("high_school", "")),
        "college": _clean_text(fields.get("college", "")),
        "draft": draft,
        "current_team": current_team,
        "career_history": history,
    }
    if fields.get("death_date"):
        record["death_date"] = _parse_date_template(fields["death_date"])
    if fields.get("death_place"):
        record["death_place"] = _clean_text(fields["death_place"])
    record["_raw_teams"] = raw_names  # transient, stripped before saving
    return record

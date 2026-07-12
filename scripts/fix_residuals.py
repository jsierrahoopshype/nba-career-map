"""One-off residual fixes (idempotent):
1. Add 'Ibrahim Kutluay' alias to Ibo Kutluay's record (feeds player_aliases.json / search).
2. Locate the San Diego Clippers (G League, 2024+) stints for Cam Reddish and Jason Preston.
Run from the repo root: python scripts\\fix_residuals.py
Then regenerate: python scripts\\build_dashboard_data.py
"""
import json, io

FILES = [r"data/players/nba_players_careers.json", r"nba_players_careers_READY.json"]
SD_LOC = {"city": "San Diego", "state": "California", "country": "USA"}

def sniff_indent(path):
    with io.open(path, encoding="utf-8") as f:
        f.readline()
        line2 = f.readline()
    n = len(line2) - len(line2.lstrip(" "))
    return n if n > 0 else 1

for path in FILES:
    indent = sniff_indent(path)
    data = json.load(io.open(path, encoding="utf-8"))
    alias_added = stints_fixed = 0
    for p in data:
        if p.get("player") == "Ibo Kutluay":
            aliases = p.get("aliases") or []
            if "Ibrahim Kutluay" not in aliases:
                aliases.append("Ibrahim Kutluay")
                p["aliases"] = aliases
                alias_added += 1
        if p.get("player") in ("Cam Reddish", "Jason Preston"):
            for s in p.get("career_history", []):
                if s.get("team") == "San Diego Clippers" and not str(s.get("city", "")).strip():
                    s.update(SD_LOC)
                    stints_fixed += 1
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    print(f"{path}: alias_added={alias_added}, sd_stints_fixed={stints_fixed}")
print("Done. Now run: python scripts\\build_dashboard_data.py")

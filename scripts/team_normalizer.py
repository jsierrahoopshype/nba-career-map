"""Team-name normalization.

Loads data/teams/team_aliases.json and maps any historical / sponsored team
name to its most-recent canonical name. Also handles light cleanup that is
common in Wikipedia wikitext (stripping wikilink markup, sponsor suffixes,
whitespace) before consulting the alias table.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ALIASES_PATH = DATA_DIR / "teams" / "team_aliases.json"


class TeamNormalizer:
    def __init__(self, aliases_path: Path = ALIASES_PATH):
        self.aliases_path = Path(aliases_path)
        raw = json.loads(self.aliases_path.read_text(encoding="utf-8"))
        self.aliases: dict[str, str] = raw.get("aliases", {})
        # Case-insensitive lookup index built once.
        self._ci = {self._key(k): v for k, v in self.aliases.items()}

    @staticmethod
    def _key(name: str) -> str:
        return re.sub(r"\s+", " ", name).strip().casefold()

    @staticmethod
    def clean(name: str) -> str:
        """Strip wikitext markup and normalize whitespace from a raw name."""
        if not name:
            return ""
        n = name
        # [[Target|Display]] -> Display ; [[Target]] -> Target
        n = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", n)
        n = n.replace("'''", "").replace("''", "")
        n = re.sub(r"<ref[^>]*>.*?</ref>", "", n, flags=re.DOTALL)
        n = re.sub(r"<ref[^>]*/>", "", n)
        n = re.sub(r"<[^>]+>", "", n)  # any remaining html tags
        n = re.sub(r"\{\{[^}]*\}\}", "", n)  # leftover templates
        n = re.sub(r"\s+", " ", n).strip()
        return n

    def normalize(self, name: str) -> str:
        """Return the canonical current name for a (possibly historical) team."""
        cleaned = self.clean(name)
        if not cleaned:
            return cleaned
        hit = self._ci.get(self._key(cleaned))
        return hit if hit else cleaned

    def is_known_alias(self, name: str) -> bool:
        return self._key(self.clean(name)) in self._ci

    def add_alias(self, historical: str, canonical: str) -> None:
        self.aliases[historical] = canonical
        self._ci[self._key(historical)] = canonical

    def save(self) -> None:
        raw = json.loads(self.aliases_path.read_text(encoding="utf-8"))
        raw["aliases"] = dict(sorted(self.aliases.items()))
        self.aliases_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    tn = TeamNormalizer()
    for t in ["Tau Cerámica", "New Jersey Nets", "Seattle SuperSonics",
              "[[Charlotte Bobcats]]", "Fenerbahçe Beko", "Los Angeles Lakers"]:
        print(f"{t!r:35} -> {tn.normalize(t)!r}")

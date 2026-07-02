"""Thin Wikipedia API client (no HTML scraping).

Uses the MediaWiki action API (https://en.wikipedia.org/w/api.php) to fetch
raw wikitext and page metadata. Enforces a polite delay between requests and
sets a descriptive User-Agent as required by the Wikimedia API etiquette.

Rate limiting is intentionally conservative:
  - a configurable delay between requests (default 1.0s)
  - a per-process request budget (default 100) so a single GitHub Actions run
    cannot hammer the API; when the budget is exhausted RequestBudgetExceeded
    is raised and the caller stops for this run and resumes next run.
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import json as _json
from dataclasses import dataclass, field

API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
USER_AGENT = (
    "nba-career-map/1.0 (https://github.com/jsierrahoopshype/nba-career-map; "
    "career-history updater) python-urllib"
)


class RequestBudgetExceeded(Exception):
    """Raised when the per-run request budget has been used up."""


@dataclass
class WikipediaClient:
    delay: float = 1.0
    max_requests: int = 100
    timeout: int = 30
    endpoint: str = API_ENDPOINT
    _count: int = field(default=0, init=False)
    _last_ts: float = field(default=0.0, init=False)

    @property
    def requests_made(self) -> int:
        return self._count

    def remaining(self) -> int:
        return max(0, self.max_requests - self._count)

    def _throttle(self) -> None:
        if self._last_ts:
            elapsed = time.time() - self._last_ts
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)

    def _get(self, params: dict) -> dict:
        if self._count >= self.max_requests:
            raise RequestBudgetExceeded(
                f"Request budget of {self.max_requests} exhausted this run"
            )
        self._throttle()
        params = {**params, "format": "json", "formatversion": "2"}
        url = self.endpoint + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        self._count += 1
        self._last_ts = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return _json.loads(resp.read().decode("utf-8"))

    # -- public API ---------------------------------------------------------

    def get_wikitext(self, title: str) -> str | None:
        """Return raw wikitext of the current revision, or None if missing."""
        data = self._get({
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": title,
            "redirects": 1,
        })
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        try:
            return pages[0]["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError):
            return None

    def resolve_title(self, title: str) -> str | None:
        """Follow redirects/normalization to the canonical article title."""
        data = self._get({
            "action": "query",
            "titles": title,
            "redirects": 1,
        })
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        return pages[0].get("title")

    def get_extract(self, title: str) -> str | None:
        """Return the plain-text lead extract of a page (used for team pages)."""
        data = self._get({
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "titles": title,
            "redirects": 1,
        })
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        return pages[0].get("extract")

    def search(self, query: str, limit: int = 5) -> list[str]:
        data = self._get({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
        })
        return [r["title"] for r in data.get("query", {}).get("search", [])]

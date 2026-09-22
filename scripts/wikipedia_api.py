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
        return self.get_wikitext_and_title(title)[0]

    def get_wikitext_and_title(self, title: str) -> tuple[str | None, str | None]:
        """Return (wikitext, canonical_title) in a single request.

        The canonical title is the article reached after following redirects and
        normalization (e.g. "Bub Carrington" -> "Carlton Carrington"), which is
        what duplicate detection keys on. Either element is None if missing.
        """
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
            return None, None
        canonical = pages[0].get("title")
        try:
            return (pages[0]["revisions"][0]["slots"]["main"]["content"],
                    canonical)
        except (KeyError, IndexError):
            return None, canonical

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

    def resolve_titles(self, titles: list[str],
                       batch: int = 50) -> dict[str, str | None]:
        """Resolve many titles at once: {requested: canonical or None}.

        The API takes 50 titles a request and answers with the normalization
        and redirect chains, so auditing five thousand records costs a hundred
        requests rather than five thousand. A title nothing resolves to comes
        back None (missing article), which is itself worth knowing.
        """
        out: dict[str, str | None] = {}
        titles = [t for t in dict.fromkeys(titles) if t]
        for i in range(0, len(titles), batch):
            chunk = titles[i:i + batch]
            data = self._get({
                "action": "query",
                "titles": "|".join(chunk),
                "redirects": 1,
            })
            q = data.get("query", {})
            # requested -> normalized -> (redirect)* -> final
            hop = {}
            for kind in ("normalized", "redirects"):
                for h in q.get(kind, []) or []:
                    hop[h["from"]] = h["to"]
            live = {p["title"] for p in q.get("pages", [])
                    if not p.get("missing")}
            for t in chunk:
                cur, seen = t, set()
                while cur in hop and cur not in seen:
                    seen.add(cur)
                    cur = hop[cur]
                out[t] = cur if cur in live else None
        return out

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

    def get_extracts(self, titles: list[str],
                     batch: int = 20) -> dict[str, str]:
        """Lead extracts for many titles at once: {requested: extract}.

        The extracts API caps a batch at 20, so auditing eight hundred clubs
        costs forty requests rather than eight hundred. A title with no article
        comes back as an empty string.
        """
        out: dict[str, str] = {}
        titles = [t for t in dict.fromkeys(titles) if t]
        for i in range(0, len(titles), batch):
            chunk = titles[i:i + batch]
            data = self._get({
                "action": "query", "prop": "extracts", "exintro": 1,
                "explaintext": 1, "exlimit": len(chunk),
                "titles": "|".join(chunk), "redirects": 1,
            })
            q = data.get("query", {})
            hop = {}
            for kind in ("normalized", "redirects"):
                for h in q.get(kind, []) or []:
                    hop[h["from"]] = h["to"]
            by_title = {p["title"]: p.get("extract", "")
                        for p in q.get("pages", []) if not p.get("missing")}
            for t in chunk:
                cur, seen = t, set()
                while cur in hop and cur not in seen:
                    seen.add(cur)
                    cur = hop[cur]
                out[t] = by_title.get(cur, "")
        return out

    def search(self, query: str, limit: int = 5) -> list[str]:
        data = self._get({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
        })
        return [r["title"] for r in data.get("query", {}).get("search", [])]

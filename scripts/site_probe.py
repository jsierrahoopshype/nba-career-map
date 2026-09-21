"""Ask the live site which URL it actually serves this section at.

The canonical tag has to name the address a reader reaches, and only the
running site knows what that is: hoopsmatic.com proxies this repo, and the
route has been written both ways (/nba-career-map/ in the share links, and
/career-maps/ when the proxy was first set up). Guessing wrong is how a
canonical ends up pointing at the GitHub copy.

Read-only. Prints what each candidate answers, and what canonical it already
carries, and writes nothing.

Run:  python3 scripts/site_probe.py [url ...]
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request

CANDIDATES = [
    "https://hoopsmatic.com/nba-career-map/",
    "https://hoopsmatic.com/nba-career-map/index.html",
    "https://hoopsmatic.com/career-maps/",
    "https://hoopsmatic.com/career-maps/index.html",
    "https://hoopsmatic.com/nba-career-map/player/joe-ingles.html",
    "https://hoopsmatic.com/career-maps/player/joe-ingles.html",
    "https://jsierrahoopshype.github.io/nba-career-map/",
]

UA = "nba-career-map/1.0 (canonical self-check) python-urllib"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"-> {newurl}",
                                     headers, fp)


def probe(url: str, *, follow: bool) -> dict:
    opener = (urllib.request.build_opener() if follow
              else urllib.request.build_opener(_NoRedirect))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=30) as resp:
            body = resp.read(200_000).decode("utf-8", "replace")
            return {"url": url, "status": resp.status, "final": resp.geturl(),
                    "title": _one(r"<title>(.*?)</title>", body),
                    "canonical": _one(r'rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', body)
                                 or _one(r'href=["\']([^"\']+)["\'][^>]*rel=["\']canonical', body),
                    "og_url": _one(r'property=["\']og:url["\'][^>]*content=["\']([^"\']+)', body),
                    "bytes": len(body)}
    except urllib.error.HTTPError as exc:
        return {"url": url, "status": exc.code, "final": str(exc.reason),
                "title": "", "canonical": "", "og_url": "", "bytes": 0}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "status": 0, "final": f"{type(exc).__name__}: {exc}",
                "title": "", "canonical": "", "og_url": "", "bytes": 0}


def _one(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.I | re.S)
    return m.group(1).strip() if m else ""


def main() -> int:
    urls = sys.argv[1:] or CANDIDATES
    for url in urls:
        hop = probe(url, follow=False)
        out = hop if hop["status"] == 200 else probe(url, follow=True)
        print(f"\n{url}")
        print(f"   first hop : {hop['status']}  {hop['final']}")
        if out is not hop:
            print(f"   followed  : {out['status']}  {out['final']}")
        if out["status"] == 200:
            print(f"   title     : {out['title'][:90]}")
            print(f"   canonical : {out['canonical'] or '(none)'}")
            print(f"   og:url    : {out['og_url'] or '(none)'}")
            print(f"   size      : {out['bytes']} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

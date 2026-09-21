"""Where this site is actually served from.

(Named site_config rather than site: a scripts/site.py would shadow the
standard library module of that name for every script in this directory.)

One constant, imported by everything that writes a URL into a page: the
canonical link, og:url, the per-page share cards, sitemap.xml. It was written
out in three places before, all naming the GitHub Pages origin, which told
Google to index the GitHub copy rather than the address readers reach --
and a section whose canonical points somewhere else does not rank at the
address it is published at.

The probe that settled it (scripts/site_probe.py, run from CI because this
sandbox cannot reach the site): hoopsmatic.com answers 200 on both
/nba-career-map/ and the older /career-maps/, and BOTH of them already
canonicalise to /nba-career-map/. That is the primary path.

The GitHub Pages copy stays reachable and now points here, which is what a
cross-domain canonical is for: one page, one address, signals consolidated on
the domain that publishes it.
"""
from __future__ import annotations

import os

SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://hoopsmatic.com/nba-career-map").rstrip("/")

# The GitHub Pages origin the same files are also served from. Kept so tooling
# can recognise the copy; nothing should write it into a page.
PAGES_MIRROR_URL = "https://jsierrahoopshype.github.io/nba-career-map"

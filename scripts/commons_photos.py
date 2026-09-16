"""Find a freely licensed photo on Wikimedia Commons for players who have none.

WHY. nba-headshots covers current NBA players, which is 29 of the ~1,300 who
qualify for a clip -- the rest are retired or spent their careers overseas. The
gap cannot be filled from a general image search: these clips get posted on
HoopsHype's accounts, and Getty, AP and NBAE photos carry real licensing risk.
Commons is the one large pool where the licence is machine-readable and where
non-free content is not accepted at all.

TWO INDEPENDENT GATES, both of which a file has to pass.

1. It has to live on Commons. Commons policy forbids fair-use uploads outright,
   so a non-free portrait is hosted locally on en.wikipedia instead and simply
   is not there when we ask Commons for it. This is checked by querying
   commons.wikimedia.org directly rather than following the article's copy.
2. Its machine-readable licence has to be on an allowlist. Default deny: an
   empty, unrecognised or unparseable licence is a reject, never a maybe.

Files carrying a Restrictions flag (trademark, personality rights, insignia)
are excluded by default. Those notices do not revoke the licence, but they warn
about exactly the commercial reuse these clips are. --allow-restricted keeps
them and the manifest records the flag either way.

WHICH PHOTO. The lead image of the player's own English Wikipedia article, not
an image search: it is the one picture an encyclopedia has already decided is
of this person. The article must be a basketball article and must not be a
disambiguation page.

ATTRIBUTION. CC BY and CC BY-SA require credit. Every usable file's required
credit line is stored in the manifest and written to assets/photos/CREDITS.md,
and the clip renderer puts it on screen. See the README section at the bottom
of CREDITS.md for what share-alike means for a clip built on one.

Run:  python3 scripts/commons_photos.py            # everyone who needs one
      python3 scripts/commons_photos.py --limit 40 # a sample
      python3 scripts/commons_photos.py --report   # re-read the manifest only
"""
from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og_cards as oc  # noqa: E402
from prerender import slug  # noqa: E402

try:
    from PIL import Image
except ImportError:
    Image = None

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
MANIFEST = ROOT / "data" / "commons_photos.json"
PHOTO_DIR = ROOT / "assets" / "photos"
CREDITS = PHOTO_DIR / "CREDITS.md"

WP_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
UA = ("nba-career-map/1.0 (https://github.com/jsierrahoopshype/nba-career-map; "
      "career-path clips) Python-urllib")

PHOTO_PX = 512
CROP_TOP = 0.06        # square crop taken from near the top: heads sit high
BATCH = 40
PAUSE = 0.15           # courtesy gap between API calls
DOWNLOAD_PAUSE = 0.35  # and a longer one between image downloads


# --- licence gate ------------------------------------------------------------
# Default deny. Each entry maps a machine-readable licence code from Commons'
# extmetadata to (family, attribution required). Anything not matched here is
# rejected, including a blank or missing value.
_PD = re.compile(r"^(pd([-_].*)?|public[ -]?domain.*|cc-?0([-_.].*)?)$")
_BY_SA = re.compile(r"^cc-by-sa-\d(\.\d)?([-_].*)?$")
_BY = re.compile(r"^cc-by-\d(\.\d)?([-_].*)?$")


_NONCOMMERCIAL = {"nc", "nd", "sampling", "devnations"}


def classify_license(meta: dict) -> tuple[str | None, bool, str]:
    """(family, attribution_required, reason). family None means unusable."""
    code = _val(meta, "License").strip().lower()
    if not code:
        return None, False, "no machine-readable licence"
    # Checked before the allowlist, not after: an NC or ND token anywhere in
    # the code disqualifies it however the rest of the code is spelled.
    if _NONCOMMERCIAL & set(re.split(r"[-_.]", code)):
        return None, False, f"licence not on the allowlist ({code})"
    if _PD.match(code):
        return "public-domain", False, code
    if _BY_SA.match(code):
        return "cc-by-sa", True, code
    if _BY.match(code):
        return "cc-by", True, code
    return None, False, f"licence not on the allowlist ({code})"


def _val(meta: dict, key: str) -> str:
    v = meta.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return "" if v is None else str(v)


def _plain(markup: str) -> str:
    """extmetadata fields are HTML fragments; the credit line is not."""
    txt = re.sub(r"<[^>]+>", " ", markup or "")
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()


def attribution_line(rec: dict) -> str:
    who = rec.get("artist") or rec.get("credit") or "Unknown author"
    return f"{who} / {rec.get('license_name') or rec['license']}"


# --- api ---------------------------------------------------------------------
def api(endpoint: str, **params) -> dict:
    params.update(format="json", formatversion="2")
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _title_key(title: str) -> str:
    """MediaWiki treats underscores and spaces as the same character.

    pageimages hands back a filename with underscores; Commons echoes the title
    back with spaces. Joining the two on the raw strings silently matched only
    the filenames that happened to contain neither.
    """
    return (title or "").replace("_", " ").strip()


def _query_all(endpoint: str, **params) -> dict:
    """One query, following continuation and merging the list props.

    A batch of forty articles blows past the category limit in a single
    response, and without following `continue` the pages at the end of the
    batch came back with no categories at all -- so they failed the
    is-this-a-basketball-article check for the wrong reason.
    """
    pages: dict = {}
    cont: dict = {}
    for _ in range(12):
        doc = api(endpoint, **params, **cont)
        q = doc.get("query", {})
        for page in q.get("pages", []) or []:
            key = page.get("title")
            if key not in pages:
                pages[key] = dict(page)
            else:
                for prop in ("categories", "imageinfo"):
                    if page.get(prop):
                        pages[key].setdefault(prop, [])
                        pages[key][prop] += page[prop]
        for name in ("normalized", "redirects"):
            if q.get(name):
                pages.setdefault("__" + name, []).extend(q[name])
        if "continue" not in doc:
            break
        cont = doc["continue"]
        time.sleep(PAUSE)
    return pages


def search_titles(names: list) -> dict:
    """name -> best-guess article title, for names that resolved to nothing.

    A common name lands on a disambiguation page ("Mike Smith") rather than the
    player, and some players are filed under a different spelling than our own
    ("Hidayet Turkoglu" vs "Hedo Turkoglu"). The search is constrained to
    basketball and the result must still carry the player's surname, so it
    narrows the candidate rather than picking a stranger.
    """
    out = {}
    for name in names:
        surname = _norm_word(name.split()[-1]) if name.split() else ""
        if len(surname) < 3:
            continue
        try:
            doc = api(WP_API, action="query", list="search",
                      srsearch=f'"{name}" basketball', srlimit="3",
                      srnamespace="0")
        except Exception:  # noqa: BLE001
            continue
        for hit in doc.get("query", {}).get("search", []) or []:
            title = hit.get("title", "")
            if surname in _norm_word(title):
                out[name] = title
                break
        time.sleep(PAUSE)
    return out


def _norm_word(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def lead_images(names: list) -> tuple[dict, Counter]:
    """name -> (file title, wikipedia title), for basketball articles only."""
    out, lost = {}, Counter()
    for i in range(0, len(names), BATCH):
        chunk = names[i:i + BATCH]
        pages = _query_all(WP_API, action="query", redirects=1,
                           titles="|".join(chunk),
                           prop="pageimages|categories|pageprops",
                           piprop="name", cllimit="max",
                           ppprop="disambiguation")
        back = {}
        for n in pages.pop("__normalized", []):
            back[n["to"]] = n["from"]
        for rd in pages.pop("__redirects", []):
            back[rd["to"]] = back.get(rd["from"], rd["from"])
        for page in pages.values():
            asked = back.get(page.get("title"), page.get("title"))
            if page.get("missing"):
                lost["no English Wikipedia article"] += 1
                continue
            if "disambiguation" in (page.get("pageprops") or {}):
                lost["article is a disambiguation page"] += 1
                continue
            cats = " ".join(c.get("title", "")
                            for c in page.get("categories", []) or [])
            if "basketball" not in cats.lower():
                lost["article is not about basketball"] += 1
                continue
            fn = page.get("pageimage")
            if not fn:
                lost["article has no lead image"] += 1
                continue
            out[asked] = (f"File:{_title_key(fn)}", page.get("title"))
        time.sleep(PAUSE)
    return out, lost


def commons_files(titles: list) -> dict:
    """file title -> imageinfo, for files that exist ON COMMONS."""
    out = {}
    for i in range(0, len(titles), BATCH):
        chunk = titles[i:i + BATCH]
        pages = _query_all(
            COMMONS_API, action="query", titles="|".join(chunk),
            prop="imageinfo", iiprop="extmetadata|url|mime|size",
            iiurlwidth=str(PHOTO_PX * 2),
            iiextmetadatafilter=("License|LicenseShortName|UsageTerms|"
                                 "Artist|Credit|LicenseUrl|Restrictions|"
                                 "AttributionRequired|Copyrighted"))
        pages.pop("__normalized", None)
        pages.pop("__redirects", None)
        for page in pages.values():
            if page.get("missing") or not page.get("imageinfo"):
                continue
            out[_title_key(page["title"])] = page["imageinfo"][0]
        time.sleep(PAUSE)
    return out


# --- image -------------------------------------------------------------------
def fetch_square(url: str) -> "Image.Image | None":
    """Fetch and square-crop, politely.

    Wikimedia throttles a client that fires several hundred image requests
    back to back -- the first full run lost 119 of 378 photos that way, which
    looked like missing files and was really rate limiting. Requests are spaced
    out and retried with backoff.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                im = Image.open(io.BytesIO(r.read()))
                im.load()
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1) ** 2)
    else:
        print(f"    download failed after 3 tries: {last}")
        return None
    time.sleep(DOWNLOAD_PAUSE)
    im = im.convert("RGB")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    # Taken from near the top rather than the middle: a standing photo puts the
    # head high in frame, and a centre crop lands on the torso.
    top = int(min(max(0, (h - side) * CROP_TOP), h - side))
    return im.crop((left, top, left + side, top + side)) \
             .resize((PHOTO_PX, PHOTO_PX), Image.LANCZOS)


# --- driver ------------------------------------------------------------------
def needing_photos(db: list, coords) -> list:
    """Clip candidates with no official NBA headshot."""
    import quiz_clips as q
    # Against the FULL headshot index, not the current-roster one: there is no
    # point going to Commons for a player the official CDN already covers.
    shots = oc.Headshots(enabled=True, index_url=oc.HEADSHOT_INDEX_ALL)
    out = []
    for p in q.candidates(db, coords):
        name = p.get("display_name") or p.get("player") or ""
        if name and not shots.by_name.get(oc._norm(name)):
            out.append(name)
    return out


def build(names: list, *, allow_restricted: bool, redownload: bool) -> dict:
    found, rejects = lead_images(names)
    retry = [n for n in names if n not in found]
    if retry:
        alt = search_titles(retry)
        print(f"  {len(retry)} names did not resolve; searching found "
              f"{len(alt)} plausible titles")
        second, _why = lead_images(sorted(set(alt.values())))
        by_title = {t: n for n, t in alt.items()}
        recovered = 0
        for title, rec in second.items():
            asked = by_title.get(title)
            if asked and asked not in found:
                found[asked] = rec
                recovered += 1
        print(f"  recovered {recovered} of them")
        # The counter says why each name failed on the FIRST pass, so it is
        # rebuilt against who is still missing rather than patched.
        rejects = Counter({k: v for k, v in rejects.items() if v})
        rejects["unresolved after search"] = len(retry) - recovered
        for k in ("no English Wikipedia article",
                  "article is a disambiguation page",
                  "article is not about basketball",
                  "article has no lead image"):
            rejects.pop(k, None)
    print(f"  {len(found)}/{len(names)} have a basketball article with a lead "
          f"image")
    for why, n in sorted(rejects.items(), key=lambda kv: -kv[1]):
        print(f"      {n:5d}  {why}")
    info = commons_files(sorted({f for f, _ in found.values()}))
    print(f"  {len(info)}/{len(found)} of those files are hosted on Commons "
          f"(the rest are local non-free uploads)")

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    players = {}
    for name, (file_title, wp_title) in sorted(found.items()):
        ii = info.get(file_title)
        if ii is None:
            rejects["not on Commons (non-free local upload)"] += 1
            continue
        meta = ii.get("extmetadata", {}) or {}
        family, needs_credit, reason = classify_license(meta)
        if family is None:
            rejects[reason if reason.startswith("no machine")
                    else "licence not on the allowlist"] += 1
            continue
        restrictions = _plain(_val(meta, "Restrictions"))
        if restrictions and not allow_restricted:
            rejects[f"restriction flag ({restrictions})"] += 1
            continue
        if (ii.get("mime") or "").split("/")[-1] not in ("jpeg", "png", "webp"):
            rejects["not a still image"] += 1
            continue
        rec = {
            "wikipedia": wp_title,
            "file": file_title,
            "license": family,
            "license_code": reason,
            "license_name": _plain(_val(meta, "LicenseShortName")),
            "license_url": _plain(_val(meta, "LicenseUrl")),
            "artist": _plain(_val(meta, "Artist")),
            "credit": _plain(_val(meta, "Credit")),
            "attribution_required": needs_credit,
            "restrictions": restrictions,
            "file_page": ii.get("descriptionurl", ""),
            "local": f"assets/photos/{slug(name)}.jpg",
        }
        rec["attribution"] = attribution_line(rec)
        dest = ROOT / rec["local"]
        if redownload or not dest.exists():
            im = fetch_square(ii.get("thumburl") or ii.get("url"))
            if im is None:
                rejects["download failed"] += 1
                continue
            im.save(dest, "JPEG", quality=82, optimize=True)
        players[name] = rec
    return {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "asked": len(names), "players": players,
            "rejected": dict(rejects)}


def prune_orphans(doc: dict) -> int:
    """Delete cached photos no longer in the manifest.

    A player who gains an official headshot drops out of the search, and an
    earlier run's file would otherwise sit in the repo forever with no credit
    line pointing at it -- which for an attribution-required photo is the one
    state that must not happen.
    """
    keep = {(ROOT / r["local"]).resolve() for r in doc["players"].values()}
    gone = 0
    for f in sorted(PHOTO_DIR.glob("*.jpg")):
        if f.resolve() not in keep:
            f.unlink()
            gone += 1
    return gone


def write_credits(doc: dict) -> None:
    by = Counter(r["license"] for r in doc["players"].values())
    lines = [
        "# Photo credits",
        "",
        "Generated by `scripts/commons_photos.py`. Every file here is hosted "
        "on Wikimedia Commons, which does not accept non-free content, and "
        "passed a default-deny licence allowlist (public domain / CC0, CC BY, "
        "CC BY-SA).",
        "",
        f"- {len(doc['players'])} photos: "
        + ", ".join(f"{n} {k}" for k, n in sorted(by.items())),
        "",
        "## Required credit",
        "",
        "Public-domain and CC0 files need no credit. Every CC BY and CC BY-SA "
        "file does, and the clip renderer prints the line below on the reveal "
        "frame. If a photo is reused anywhere else, that line goes with it.",
        "",
        "| Player | Credit line | Licence | File |",
        "| --- | --- | --- | --- |",
    ]
    for name, r in sorted(doc["players"].items()):
        cred = r["attribution"] if r["attribution_required"] else "not required"
        lines.append(f"| {name} | {cred} | [{r['license_name'] or r['license']}]"
                     f"({r['license_url']}) | [{r['file']}]({r['file_page']}) |")
    lines += [
        "",
        "## Share-alike",
        "",
        "A CC BY-SA photo carries a share-alike condition. A clip built around "
        "one is arguably a derivative, which would put the clip under the same "
        "licence. CC BY and public-domain files carry no such condition. If "
        "that matters for a given post, restrict the set with "
        "`--licenses public-domain,cc-by`.",
        "",
    ]
    CREDITS.write_text("\n".join(lines), encoding="utf-8")


def report(doc: dict) -> None:
    by = Counter(r["license"] for r in doc["players"].values())
    need = sum(1 for r in doc["players"].values() if r["attribution_required"])
    print(f"\n  usable photos: {len(doc['players'])} of {doc['asked']} asked")
    for k, n in sorted(by.items()):
        print(f"    {k:16s} {n:4d}")
    print(f"  attribution required: {need}")
    if doc.get("rejected"):
        print("  rejected:")
        for k, n in sorted(doc["rejected"].items(), key=lambda kv: -kv[1])[:12]:
            print(f"    {n:4d}  {k}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--player", action="append", default=[])
    ap.add_argument("--allow-restricted", action="store_true")
    ap.add_argument("--redownload", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="re-read the manifest without touching the network")
    a = ap.parse_args()

    if a.report:
        report(json.loads(MANIFEST.read_text(encoding="utf-8")))
        return 0
    if Image is None:
        print("Pillow is required", file=sys.stderr)
        return 2

    db = json.loads(CAREERS.read_text(encoding="utf-8"))
    names = a.player or needing_photos(db, oc.Coords())
    if a.limit:
        names = names[:a.limit]
    print(f"looking for photos for {len(names)} players")
    doc = build(names, allow_restricted=a.allow_restricted,
                redownload=a.redownload)
    MANIFEST.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    gone = prune_orphans(doc)
    if gone:
        print(f"  removed {gone} cached photos no longer in the manifest")
    write_credits(doc)
    report(doc)
    print(f"\n  -> {MANIFEST.relative_to(ROOT)}  {CREDITS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate a per-player Open Graph card: their real route, drawn.

Now that every player has a prerendered page, the shared career-map image is
the one thing left that is identical across all of them. This draws each
player's own card in the same visual language: red arcs crossing a pale
basemap, their actual stints as the route, their name, and the headline counts.

SCOPE. Not every player gets one. Measured at 1200x630, a card is about 22 KB
after flattening for compression, so all 5,179 would be 111 MB against a 34 MB
repo -- and every daily CI run clones that. Cards are therefore generated for
the players whose pages plausibly get shared: anyone currently active (NBA or
overseas) plus every All-Star, about 1,300 of them, roughly 28 MB. Everyone
else keeps the shared career-map image, which is what they have today, so
nobody loses anything.

Daily cost is small either way: a card is rewritten only when its bytes change,
and the fields it draws from move for 3 to 8 players a day.

HEADSHOTS come from the nba-headshots repo, which holds the ~570 current
players. That covers around 545 of ours, so most cards fall back to a drawn
silhouette -- deliberately in the same frame and position, so a card with a
face and a card without read as the same design.

Run:  python3 scripts/og_cards.py            # generate
      python3 scripts/og_cards.py --basemap  # re-render assets/og-basemap.png
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prerender import slug  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
INDEX_HTML = ROOT / "index.html"
BASEMAP = ROOT / "assets" / "og-basemap.png"
CARD_DIR = ROOT / "assets" / "og" / "player"

W, H = 1200, 630
PANEL_H = 150

# The basemap is rendered at 3x and each card crops the part its route needs.
# A career spent entirely in the USA should not hand 80% of the frame to Asia,
# and cropping a hi-res map keeps the coastline crisp at any zoom.
SCALE = 3
BW, BH = W * SCALE, H * SCALE
# Never zoom past this span, or a one-city career fills the frame with a single
# country and the map stops being recognisable.
MIN_SPAN_X = BW * 0.18
VIEW_PAD = 0.18

# Public-domain simplified world outlines (Natural Earth derived). Only needed
# to re-render the basemap, which is committed, so the pipeline never fetches
# it on a normal run.
WORLD_GEOJSON = ("https://raw.githubusercontent.com/johan/world.geo.json/"
                 "master/countries.geo.json")
HEADSHOT_INDEX = ("https://raw.githubusercontent.com/jsierrahoopshype/"
                  "nba-headshots/main/players/metadata/players.json")
HEADSHOT_FACE = ("https://raw.githubusercontent.com/jsierrahoopshype/"
                 "nba-headshots/main/players/headshots/face/")

WATER = (214, 232, 247)
LAND = (247, 246, 243)
COAST = (223, 224, 226)
RED = (214, 40, 40)
INK = (29, 29, 31)
MUTED = (110, 110, 115)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def _font(name: str, size: int):
    return ImageFont.truetype(str(FONT_DIR / name), size)


def project(lon: float, lat: float, w: int = W, h: int = H) -> tuple[float, float]:
    """Equirectangular, cropped to the inhabited band so the map fills the frame."""
    return (lon + 180.0) / 360.0 * w, (85.0 - lat) / 150.0 * h


def route_view(pts: list) -> tuple[float, float, float, float]:
    """The crop box, in basemap pixels, that frames this route.

    Pads the bounding box, enforces a floor on the zoom and the card's aspect
    ratio, then slides the box back inside the map if padding pushed it out.
    """
    if not pts:
        return (0, 0, BW, BH)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    # The route is framed in the MAP area, which is the card minus the text
    # panel; the crop is then extended downward so the panel covers map the
    # route does not use. Without this a southern stop lands under the text.
    map_h = H - PANEL_H
    raw_x, raw_y = x1 - x0, y1 - y0

    # Fit the route in the map area ABOVE the panel, with as much breathing
    # room as will fit. A tall route (Vilnius down to Adelaide) only needs less
    # padding, not a different frame -- trading padding away first is what keeps
    # the southernmost stop out from behind the text.
    span_x = route_y = total_y = None
    pad = VIEW_PAD
    while pad >= 0.03:
        need_x, need_y = raw_x * (1 + pad * 2), raw_y * (1 + pad * 2)
        cand = min(max(need_x, need_y * (W / map_h), MIN_SPAN_X), BW)
        if cand * (H / W) <= BH and cand * (map_h / W) >= need_y:
            span_x = cand
            total_y, route_y = cand * (H / W), cand * (map_h / W)
            break
        pad -= 0.03
    if span_x is None:
        # Genuinely too tall for the map area: fit the whole card instead. Some
        # arcs pass behind the panel, but every stop stays on the card.
        need_x, need_y = raw_x * 1.06, raw_y * 1.06
        span_x = min(max(need_x, need_y * (W / H), MIN_SPAN_X), BW)
        total_y = min(span_x * (H / W), BH)
        span_x = total_y * (W / H)
        route_y = total_y

    # Deliberately NOT clamped inside the basemap. Clamping was what pushed a
    # route with a far-southern stop down behind the panel: there simply is not
    # always enough map below the route to hold the panel's strip. Crops that
    # run past the edge are filled with water by crop_view(), which costs
    # nothing and keeps the route where it belongs.
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = cx - span_x / 2
    top = cy - route_y / 2
    return (left, top, left + span_x, top + total_y)


# --- coordinates: the same lookup, and the same fallback jitter, as the app ---
def _js_object(name: str) -> dict:
    text = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(rf"const {name}\s*=\s*", text)
    if not m:
        return {}
    i = text.index("{", m.end())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return json.loads(text[i:j + 1])


class Coords:
    def __init__(self):
        self.coords = _js_object("COORDS")
        self.fallback = _js_object("FALLBACK")

    def get(self, city: str, state: str, country: str):
        if not city:
            return None
        for k in (f"{city}|{state or ''}|{country or ''}", f"{city}||{country or ''}"):
            if k in self.coords:
                return self.coords[k]
        base = self.fallback.get(country)
        if not base:
            return None
        # Mirrors getCoords()'s deterministic per-city offset so a card and the
        # live map put the same club in the same place.
        h = 0
        for ch in city:
            h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
        if h >= 2 ** 31:
            h -= 2 ** 32
        return [base[0] + ((h % 100) - 50) / 30, base[1] + (((h >> 8) % 100) - 50) / 30]


def crop_view(base, left: float, top: float, right: float, bottom: float):
    """Crop the basemap to a box that may extend past its edges.

    Anything outside the map is water, which is what is actually there: the
    projection is cropped to the inhabited band, so beyond its edges is ocean
    and polar sea. Filling it lets a route sit where the composition wants it
    rather than where the image happens to end.
    """
    l, t, r, b = int(left), int(top), int(right), int(bottom)
    out = Image.new("RGB", (max(r - l, 1), max(b - t, 1)), WATER)
    sl, st_, sr, sb = max(l, 0), max(t, 0), min(r, BW), min(b, BH)
    if sr > sl and sb > st_:
        out.paste(base.crop((sl, st_, sr, sb)), (sl - l, st_ - t))
    return out.resize((W, H), Image.LANCZOS)


def build_basemap() -> None:
    """Rasterise the world once. The result is committed; this rarely runs."""
    with urllib.request.urlopen(WORLD_GEOJSON, timeout=120) as r:
        geo = json.loads(r.read().decode("utf-8"))
    im = Image.new("RGB", (BW, BH), WATER)
    d = ImageDraw.Draw(im)
    for f in geo["features"]:
        g = f["geometry"]
        rings = (g["coordinates"] if g["type"] == "Polygon"
                 else [c for part in g["coordinates"] for c in part])
        for ring in rings:
            pts = [project(p[0], p[1], BW, BH) for p in ring]
            if len(pts) > 2:
                d.polygon(pts, fill=LAND, outline=COAST)
    BASEMAP.parent.mkdir(parents=True, exist_ok=True)
    im.save(BASEMAP, "PNG", optimize=True)
    print(f"wrote {BASEMAP.relative_to(ROOT)} ({BASEMAP.stat().st_size:,} bytes)")


# --- headshots ---------------------------------------------------------------
def _norm(name: str) -> str:
    import unicodedata
    n = "".join(c for c in unicodedata.normalize("NFKD", name or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", n.lower())


class Headshots:
    """Lazy, failure-tolerant access to the nba-headshots repo."""

    def __init__(self, enabled: bool = True):
        self.by_name: dict[str, str] = {}
        self.cache: dict[str, object] = {}
        self.enabled = enabled
        if not enabled:
            return
        try:
            with urllib.request.urlopen(HEADSHOT_INDEX, timeout=60) as r:
                doc = json.loads(r.read().decode("utf-8"))
            for rec in doc.get("players", []):
                shot = rec.get("headshot") or {}
                if shot.get("face") and shot.get("filename"):
                    self.by_name[_norm(rec.get("full_name", ""))] = shot["filename"]
        except Exception as exc:  # noqa: BLE001
            # Never fail a build over portraits: every card has a silhouette.
            print(f"  [headshots] index unavailable ({exc}); using silhouettes")
            self.enabled = False

    def face(self, name: str):
        if not self.enabled:
            return None
        fn = self.by_name.get(_norm(name))
        if not fn:
            return None
        if fn in self.cache:
            return self.cache[fn]
        try:
            with urllib.request.urlopen(HEADSHOT_FACE + fn, timeout=60) as r:
                im = Image.open(io.BytesIO(r.read())).convert("RGBA")
        except Exception:  # noqa: BLE001
            im = None
        self.cache[fn] = im
        return im


def _draw_silhouette(im, box):
    """A plain head-and-shoulders mark, drawn rather than fetched.

    The headshots repo ships an SVG silhouette, but rasterising SVG would add a
    dependency for a shape this simple. Same frame and position as a real
    portrait, so the two card variants read identically.
    """
    x, y, size = box
    # Drawn on its own layer and masked to the circle, so the shoulders end at
    # the frame instead of running off the bottom of the card.
    layer = Image.new("RGBA", (size, size), (226, 228, 232, 255))
    ld = ImageDraw.Draw(layer)
    hr = size * 0.30
    hx, hy = size / 2, size * 0.36
    ld.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=(196, 200, 208, 255))
    br = size * 0.44
    ld.ellipse([hx - br, size * 0.66, hx + br, size * 1.55],
               fill=(196, 200, 208, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    im.paste(layer, (x, y), mask)


def _arc(p0, p1, lift=0.26, n=30):
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = x1 - x0, y1 - y0
    cx, cy = mx - dy * lift, my + dx * lift
    return [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1,
             (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1)
            for t in (i / n for i in range(n + 1))]


def _years(hist) -> str:
    ys = [int(y) for s in hist for y in re.findall(r"\d{4}", s.get("years") or "")]
    if not ys:
        return "—"
    lo, hi = min(ys), max(ys)
    return f"{lo}–{hi}" if lo != hi else str(lo)


def render_card(player: dict, base, coords: Coords, shots: Headshots):
    hist = player.get("career_history", []) or []

    hi = []
    for st in hist:
        c = coords.get((st.get("city") or "").strip(),
                       (st.get("state") or "").strip(),
                       (st.get("country") or "").strip())
        if c:
            hi.append(project(c[1], c[0], BW, BH))

    left, top, right, bottom = route_view(hi)
    im = crop_view(base, left, top, right, bottom)
    sx, sy = W / (right - left), H / (bottom - top)
    pts = [((x - left) * sx, (y - top) * sy) for x, y in hi]
    d = ImageDraw.Draw(im, "RGBA")

    # Earlier stops fade, the latest reads strongest, so the route has direction.
    for i in range(len(pts) - 1):
        frac = (i + 1) / max(1, len(pts) - 1)
        d.line(_arc(pts[i], pts[i + 1]), fill=RED + (int(70 + 150 * frac),),
               width=4, joint="curve")
    for i, (x, y) in enumerate(pts):
        r = 8 if i == len(pts) - 1 else 6
        d.ellipse([x - r, y - r, x + r, y + r], fill=RED,
                  outline=(255, 255, 255), width=2)

    d.rectangle([0, H - PANEL_H, W, H], fill=(255, 255, 255, 236))
    d.line([(0, H - PANEL_H), (W, H - PANEL_H)], fill=(0, 0, 0, 22), width=2)

    portrait = 112
    px, py = W - portrait - 48, H - PANEL_H + (PANEL_H - portrait) // 2
    face = shots.face(player.get("display_name") or player.get("player") or "")
    if face is not None:
        face = face.resize((portrait, portrait), Image.LANCZOS)
        mask = Image.new("L", (portrait, portrait), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, portrait, portrait], fill=255)
        ring = Image.new("RGBA", (portrait, portrait), (0, 0, 0, 0))
        ring.paste(face, (0, 0), face)
        im.paste(ring, (px, py), mask)
        d.ellipse([px, py, px + portrait, py + portrait],
                  outline=(226, 228, 232), width=3)
    else:
        _draw_silhouette(im, (px, py, portrait))
        d.ellipse([px, py, px + portrait, py + portrait],
                  outline=(226, 228, 232), width=3)

    name = player.get("display_name") or player.get("player") or ""
    size = 54 if len(name) <= 22 else (44 if len(name) <= 30 else 36)
    d.text((48, H - PANEL_H + 20), name, font=_font("DejaVuSans-Bold.ttf", size),
           fill=INK)

    clubs = len({(s.get("team") or "").strip() for s in hist} - {""})
    countries = len({(s.get("country") or "").strip() for s in hist} - {""})
    stats = [(str(clubs), "TEAMS"), (str(countries), "COUNTRIES"),
             (_years(hist), "YEARS"), (str(len(hist)), "STOPS")]
    fs, fl = _font("DejaVuSans-Bold.ttf", 32), _font("DejaVuSans.ttf", 17)
    x = 48
    for val, lab in stats:
        d.text((x, H - 76), val, font=fs, fill=RED)
        d.text((x, H - 38), lab, font=fl, fill=MUTED)
        x += max(d.textlength(val, font=fs), d.textlength(lab, font=fl)) + 46
    return im


def selected(players: list) -> list:
    """Currently active anywhere, plus every All-Star. See SCOPE above."""
    return [p for p in players
            if p.get("status") in ("nba_active", "overseas_active")
            or p.get("all_star")]


def write_all(players: list, out_dir: Path = CARD_DIR,
              headshots: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Image.open(BASEMAP).convert("RGB")
    coords, shots = Coords(), Headshots(enabled=headshots)
    expected, written, unchanged, faces = set(), 0, 0, 0

    for p in selected(players):
        key = str(p.get("player") or "").strip()
        if not key:
            continue
        path = out_dir / f"{slug(key)}.png"
        expected.add(path.name)
        im = render_card(p, base, coords, shots)
        if shots.face(p.get("display_name") or key) is not None:
            faces += 1
        # Flattened to a fixed palette: the card is flat colour plus a portrait,
        # and this roughly halves the bytes with no visible change.
        buf = io.BytesIO()
        im.quantize(colors=64, dither=Image.NONE).save(buf, "PNG", optimize=True)
        body = buf.getvalue()
        if path.exists() and path.read_bytes() == body:
            unchanged += 1
            continue
        path.write_bytes(body)
        written += 1

    removed = 0
    for stale in out_dir.glob("*.png"):
        if stale.name not in expected:
            stale.unlink()
            removed += 1
    return {"total": len(expected), "written": written, "unchanged": unchanged,
            "removed": removed, "with_face": faces}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--basemap", action="store_true",
                    help="re-render assets/og-basemap.png and exit")
    ap.add_argument("--no-headshots", action="store_true",
                    help="skip the portrait fetch (silhouettes everywhere)")
    ap.add_argument("--limit", type=int, help="only the first N, for a quick look")
    args = ap.parse_args()
    if Image is None:
        sys.exit("Pillow is required:  pip install Pillow")
    if args.basemap:
        return build_basemap()
    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    if args.limit:
        players = selected(players)[:args.limit]
    s = write_all(players, headshots=not args.no_headshots)
    print(f"player cards: {s['total']} total, {s['written']} written, "
          f"{s['unchanged']} unchanged, {s['removed']} removed, "
          f"{s['with_face']} with a real portrait")


if __name__ == "__main__":
    main()

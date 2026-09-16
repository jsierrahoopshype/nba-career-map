"""Build "guess the player" clips: a career route drawing itself, then a reveal.

A vertical 9:16 clip for social. A plane flies the player's route leg by leg
and the camera follows it, panning with the plane and easing out over long
crossings, back in as it arrives. Each club's name appears as the plane lands.
The route completes, the camera pulls back to show the whole journey, and only
then does the name and portrait appear.

NO YEARS BEFORE THE REVEAL. Dates narrow a guess enormously -- "2003-2005" all
but names the player -- so nothing before the reveal frame carries a year. Club
names only.

WHAT IT REUSES. All the drawing comes from scripts/og_cards.py: the same
projection, the same vector country outlines drawn at the view's own crop, the
same red arcs, the same headshot fetch with its silhouette fallback. Nothing
about the visual language is re-invented here; this adds time to it.

NO EARLY LEAK. The name, the portrait and the stat line exist only in frames at
or after the reveal, which is asserted in the tests rather than eyeballed. Club
names and years DO appear as each stop lands -- that is the puzzle itself, not a
leak. No team logos are drawn at any point.

THE MAP IS RE-PROJECTED EVERY FRAME, which turned out to be the cheap option
rather than the expensive one. Drawing the country outlines at a given view
costs 2ms when zoomed in and 7ms at whole-world, because rings outside the view
are skipped -- so a following camera needs no pre-rendered zoom levels and no
panning of an oversized raster. The earlier "~105ms a frame" figure was the
whole card render including PNG encode, not the map.

What did cost real time was writing every frame to disk as a PNG for ffmpeg to
read back. Frames are now piped to ffmpeg as raw video on stdin, which removes
the encode and the I/O entirely.

Run:  python3 scripts/quiz_clips.py --sample 3      # a few to look at
      python3 scripts/quiz_clips.py --count 50      # a batch
      python3 scripts/quiz_clips.py --player "Patty Mills"
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og_cards as oc  # noqa: E402
from era_correct_teams import ERA_TABLE  # noqa: E402
from prerender import slug  # noqa: E402
from rosters import NBA_TEAMS  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
# Clips are for hand-posting, so they are NOT committed: out/ is gitignored and
# the workflow uploads the folder as a downloadable artifact instead.
OUT_DIR = ROOT / "out" / "clips"
COMMONS_MANIFEST = ROOT / "data" / "commons_photos.json"
# Which Commons licences a clip is allowed to use. CC BY-SA carries a
# share-alike condition that arguably extends to a clip built around the photo;
# drop it from this set to restrict the clips to photos with no such condition.
CLIP_LICENSES = ("public-domain", "cc-by", "cc-by-sa")

W, H = 1080, 1920
FPS = 30

# --- HoopsMatic treatment: deep indigo, purple-to-pink accents, soft cards ---
FONT_DIR_LOCAL = ROOT / "assets" / "fonts"
EMOJI_FONT = Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")

BG_TOP = (28, 23, 68)
BG_BOT = (15, 12, 38)
CARD = (38, 32, 86)
CARD_EDGE = (62, 52, 124)
ACCENT_A = (124, 58, 237)      # purple
ACCENT_B = (236, 72, 153)      # pink
TEXT = (246, 244, 255)
MUTED = (163, 156, 208)

# The map has to sit ON the indigo rather than fight it, so the basemap is
# re-tinted instead of keeping the site's light palette: land a lifted indigo,
# water darker than the page, coastlines a violet hairline.
MAP_WATER = (19, 15, 48)
MAP_LAND = (47, 40, 100)
MAP_COAST = (72, 61, 136)
ROUTE = (244, 114, 182)        # pink, which reads on both land and water
ROUTE_DIM = (168, 85, 190)

CARD_R = 44                    # corner radius

MAP_X0, MAP_X1 = 40, W - 40
MAP_TOP, MAP_BOTTOM = 300, 1404
BAND_W = MAP_X1 - MAP_X0
VIEW_PAD = 0.12

# Timing. The ending carries most of the change: a real beat to think, then a
# full-screen answer held long enough to read and screenshot.
T_INTRO = 1.2
T_THINK = 3.6          # finished route, "Who is it?", no answer
T_REVEAL = 4.6         # full-screen answer
BODY_MAX = 17.0        # all the flying, before the ending
PER_LEG_MIN, PER_LEG_MAX = 0.85, 2.60
FLIGHT_SHARE = 0.66

SPAN_CLOSE = oc.BW * (26 / 360)
SPAN_MAX = oc.BW * 0.92
CRUISE_LIFT = 1.9
# How far out a crossing is allowed to pull. Going to whole-world made the
# camera do two frantic things at once -- a huge zoom AND a map racing
# underneath a plane pinned to the centre of frame. Capped, the crossing reads
# as speed instead of chaos.
SPAN_CRUISE_MAX = SPAN_CLOSE * 5.5
STOP_SPAN_MAX = SPAN_CLOSE * 3.2   # widest a stop is framed at when parked

# The motion budget the leg timings are derived from, rather than giving every
# leg the same slot and letting a half-world crossing snap through it.
MAX_ZOOM_RATE = 0.075  # |d ln(span)| per frame
MAX_PAN_RATE = 34.0    # screen px per frame
MAX_TURN_RATE = 9.0    # degrees per frame while pivoting on the ground
PEAK = 1.5             # smoothstep runs 1.5x its mean rate at the midpoint

# The emoji points up-right; verified by rendering it at four headings.
PLANE_GLYPH_HEADING = 45.0

_NBA = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA.add(_n)


_FONT_CACHE: dict = {}


def _font(weight: str, size: int):
    """Poppins, the HoopsMatic face. Falls back to DejaVu if it is missing."""
    key = (weight, size)
    if key not in _FONT_CACHE:
        path = FONT_DIR_LOCAL / f"Poppins-{weight}.ttf"
        if not path.exists():
            path = oc.FONT_DIR / ("DejaVuSans-Bold.ttf" if weight in
                                  ("Bold", "SemiBold") else "DejaVuSans.ttf")
        _FONT_CACHE[key] = ImageFont.truetype(str(path), size)
    return _FONT_CACHE[key]


def _gradient(size, c0, c1, horizontal=False):
    """A two-stop linear gradient. Pillow has none, and these are everywhere."""
    w, h = size
    n = w if horizontal else h
    strip = Image.new("RGB", (n, 1))
    px = strip.load()
    for i in range(n):
        t = i / max(1, n - 1)
        px[i, 0] = tuple(int(a + (b - a) * t) for a, b in zip(c0, c1))
    strip = strip.resize((w, h) if horizontal else (1, n))
    return strip if horizontal else strip.resize((w, h))


def _card(draw, box, radius=CARD_R, fill=CARD, outline=CARD_EDGE, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                           width=width)


def _accent_pill(im, xy, text, *, pad=(26, 12), size=30):
    """A small gradient pill -- the treatment's signature element."""
    d = ImageDraw.Draw(im)
    f = _font("SemiBold", size)
    tw = d.textlength(text, font=f)
    w, h = int(tw + pad[0] * 2), int(size + pad[1] * 2)
    grad = _gradient((w, h), ACCENT_A, ACCENT_B, horizontal=True)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           radius=h // 2, fill=255)
    im.paste(grad, (int(xy[0]), int(xy[1])), mask)
    d.text((xy[0] + pad[0], xy[1] + h / 2), text, font=f, fill=TEXT,
           anchor="lm")
    return w, h


# --- selection ---------------------------------------------------------------
def nba_years(player: dict) -> int:
    years = set()
    for s in player.get("career_history", []) or []:
        if (s.get("team") or "").strip() in _NBA:
            yy = [int(y) for y in re.findall(r"\d{4}", s.get("years") or "")]
            if yy:
                years.update(range(min(yy), max(yy) + 1))
    return len(years)


def _draft(player: dict) -> dict:
    d = player.get("draft")
    return d if isinstance(d, dict) else {}


def first_round(player: dict) -> bool:
    return _draft(player).get("round") == "1"


def lottery(player: dict) -> bool:
    d = _draft(player)
    try:
        return d.get("round") == "1" and int(d.get("pick", 99)) <= 14
    except (TypeError, ValueError):
        return False


def recognition(player: dict) -> int:
    """Rough ordering of who an NBA audience will actually recognise."""
    return (nba_years(player) * 2 + 8 * bool(player.get("all_star"))
            + 3 * lottery(player) + 2 * first_round(player))


def route(player: dict, coords: oc.Coords) -> list:
    """Stints that have coordinates, collapsing immediate repeats of a club."""
    out = []
    for s in player.get("career_history", []) or []:
        c = coords.get((s.get("city") or "").strip(), (s.get("state") or "").strip(),
                       (s.get("country") or "").strip())
        if not c:
            continue
        if out and out[-1][0].get("team") == s.get("team"):
            continue
        out.append((s, c))
    return out


def eligible(player: dict, coords: oc.Coords) -> bool:
    """Enough of a journey to be worth guessing, short enough to stay watchable.

    The 6..14 stop band is what keeps a clip inside 15-25 seconds; countries>=3
    is what stops it being a list of NBA trades; the NBA footprint is what makes
    the answer satisfying rather than obscure.
    """
    r = route(player, coords)
    if not 6 <= len(r) <= 14:
        return False
    countries = {(s.get("country") or "").strip() for s, _ in r} - {""}
    if len(countries) < 3:
        return False
    return nba_years(player) >= 3 or first_round(player) or player.get("all_star")


def candidates(players: list, coords: oc.Coords) -> list:
    out = [p for p in players if eligible(p, coords)]
    out.sort(key=lambda p: (-recognition(p),
                            p.get("display_name") or p.get("player") or ""))
    return out


# --- geometry ----------------------------------------------------------------
BAND_H = MAP_BOTTOM - MAP_TOP


def timings(pts: list) -> dict:
    """How long each leg gets, derived from how much the camera has to move.

    A fixed slot per leg is what made the zoom snap: Adelaide to Tel Aviv got
    the same 0.8s as a hop across Spain, so it had to pull out twelvefold and
    back inside it. Each leg now asks for the time its own zoom and pan need at
    a bounded rate, and the whole set is scaled down together if the clip would
    run long -- so legs keep their relative pacing instead of one being singled
    out.
    """
    flights, lands = [], []
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        s0, far, s1 = leg_zoom(pts, i)
        zoom = PEAK * max(math.log(far / s0), math.log(far / s1)) \
            / (MAX_ZOOM_RATE * FPS * ZOOM_EDGE)
        travel = _arc_len(oc._arc(a, b)) * BAND_W / far
        pan = PEAK * travel / (MAX_PAN_RATE * FPS)
        flights.append(min(PER_LEG_MAX * FLIGHT_SHARE,
                           max(zoom, pan, PER_LEG_MIN * FLIGHT_SHARE)))
        # The beat on the ground has to be long enough for the plane to swing
        # round to the next heading without spinning. A reversal needs most of
        # a second; carrying straight on needs none.
        turn = abs((departure_heading(pts, i + 1)
                    - heading_at(pts, i, 1.0) + 180.0) % 360.0 - 180.0)
        lands.append(max(PER_LEG_MIN * (1 - FLIGHT_SHARE),
                         PEAK * turn / (MAX_TURN_RATE * FPS)))
    if not flights:
        flights, lands = [PER_LEG_MIN * FLIGHT_SHARE], [PER_LEG_MIN * 0.34]
    legs = [f + l for f, l in zip(flights, lands)]
    body = sum(legs)
    if body > BODY_MAX:
        k = BODY_MAX / body
        flights = [f * k for f in flights]
        lands = [l * k for l in lands]
        legs = [p * k for p in legs]
        body = BODY_MAX
    return {"per": legs, "flight": flights, "land": lands,
            "legs": len(legs), "body": body,
            "total": T_INTRO + body + T_THINK + T_REVEAL}


def _arc_len(seg) -> float:
    return sum(math.hypot(q[0] - p[0], q[1] - p[1])
               for p, q in zip(seg, seg[1:]))


def _fit(draw, text, weight, size, max_w):
    while size > 18:
        f = _font(weight, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(weight, 18)


# --- camera ------------------------------------------------------------------
def clip_view(pts: list) -> tuple[float, float, float, float]:
    if not pts:
        return (0, 0, oc.BW, oc.BW * BAND_H / BAND_W)
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    need_x = max(x1 - x0, 1) * (1 + VIEW_PAD * 2)
    need_y = max(y1 - y0, 1) * (1 + VIEW_PAD * 2)
    span_x = min(max(need_x, need_y * (BAND_W / BAND_H), SPAN_CLOSE), SPAN_MAX)
    return box_from(((x0 + x1) / 2, (y0 + y1) / 2), span_x, clamp=True)


def box_from(center, span_x, *, clamp=False):
    """A view box around a point.

    clamp keeps the box inside the projection band (85N to 65S), which is right
    for the still views -- a view sliding past the band shows a strip of empty
    water for no reason. It is wrong while the plane is flying: the plane is the
    subject and has to stay centred, so a high-latitude or whole-world leg gets
    the empty strip rather than losing the plane off the bottom of the frame.
    """
    cx, cy = center
    span_y = span_x * (BAND_H / BAND_W)
    top = cy - span_y / 2
    if clamp:
        if span_y <= oc.BH:
            top = max(0.0, min(top, oc.BH - span_y))
        else:
            top = (oc.BH - span_y) / 2
    return (cx - span_x / 2, top, cx + span_x / 2, top + span_y)


def to_band(box, pt):
    left, top, right, bottom = box
    return ((pt[0] - left) * BAND_W / (right - left),
            (pt[1] - top) * BAND_H / (bottom - top))


def ease(t: float) -> float:
    """Smoothstep: zero rate of change at both ends."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


ZOOM_EDGE = 0.40          # share of a leg spent pulling out, and coming back in


def bell(t: float) -> float:
    """The zoom profile for one leg: out, cruise, in.

    A plain sine starts and ends at full rate -- the zoom is already moving at
    its fastest the instant a leg begins, which is what read as a snap. This
    eases out over the first third, cruises, and eases back in, so the rate of
    change is zero at both ends and at the joins.
    """
    t = max(0.0, min(1.0, t))
    if t < ZOOM_EDGE:
        return ease(t / ZOOM_EDGE)
    if t > 1.0 - ZOOM_EDGE:
        return ease((1.0 - t) / ZOOM_EDGE)
    return 1.0


_SCHED_CACHE: dict = {}
SCHED_N = 96
TAPER = 0.14           # share of a leg spent easing into and out of the move


def leg_schedule(s0: float, far: float, s1: float) -> list:
    """Cumulative distance along the leg, sampled, as a function of time.

    Moving at a fixed rate along the arc looks wrong: while the camera is still
    zoomed in, a small step across the map is a huge step across the screen, so
    the leg tore away at take-off and crawled at cruise. Here the rate is made
    proportional to the current span, which holds the SCREEN velocity roughly
    constant, and tapered at both ends so the move starts and stops from rest.
    """
    key = (round(s0, 3), round(far, 3), round(s1, 3))
    if key in _SCHED_CACHE:
        return _SCHED_CACHE[key]
    acc, total = [0.0], 0.0
    for i in range(1, SCHED_N + 1):
        t = i / SCHED_N
        env = ease(min(1.0, t / TAPER)) * ease(min(1.0, (1.0 - t) / TAPER))
        total += span_profile(s0, far, s1, t) * env
        acc.append(total)
    sched = [v / total for v in acc]
    _SCHED_CACHE[key] = sched
    return sched


def leg_progress(s0: float, far: float, s1: float, t: float) -> float:
    """Where along the arc the plane is at time t, 0..1."""
    sched = leg_schedule(s0, far, s1)
    i = max(0.0, min(1.0, t)) * SCHED_N
    lo, frac = int(i), i - int(i)
    hi = min(lo + 1, SCHED_N)
    return sched[lo] + (sched[hi] - sched[lo]) * frac


def arc_point(seg, u: float):
    i = max(0.0, min(1.0, u)) * (len(seg) - 1)
    lo, frac = int(i), i - int(i)
    hi = min(lo + 1, len(seg) - 1)
    return (seg[lo][0] + (seg[hi][0] - seg[lo][0]) * frac,
            seg[lo][1] + (seg[hi][1] - seg[lo][1]) * frac)


def stop_span(pts, i: int) -> float:
    """How close the camera sits while parked at stop i.

    Zooming all the way back to city level at every stop is what made a
    ten-stop route feel frantic: five NBA cities in a row meant five full
    pull-outs and five dives back in, all of it competing for the same handful
    of seconds. A stop is framed at the scale of its nearest neighbour instead,
    so a cluster is viewed at one steady zoom and the big moves are saved for
    the crossings that earn them.
    """
    d = []
    if i > 0:
        d.append(math.hypot(pts[i][0] - pts[i - 1][0],
                            pts[i][1] - pts[i - 1][1]))
    if i + 1 < len(pts):
        d.append(math.hypot(pts[i + 1][0] - pts[i][0],
                            pts[i + 1][1] - pts[i][1]))
    near = min(d) if d else 0.0
    want = near * 1.35
    # Only hold two stops in one view if they actually fit in one. Past that
    # the neighbour is a journey rather than a neighbour, and the arrival is
    # worth diving in for.
    if want > STOP_SPAN_MAX:
        return SPAN_CLOSE
    return max(SPAN_CLOSE, want)


def span_profile(s0: float, far: float, s1: float, t: float) -> float:
    """Out from the stop's own zoom, cruise, in to the next stop's."""
    t = max(0.0, min(1.0, t))
    if t < ZOOM_EDGE:
        return s0 * (far / s0) ** ease(t / ZOOM_EDGE)
    if t > 1.0 - ZOOM_EDGE:
        return s1 * (far / s1) ** ease((1.0 - t) / ZOOM_EDGE)
    return far


def leg_span(a, b) -> float:
    dist = math.hypot(b[0] - a[0], b[1] - a[1])
    return min(max(SPAN_CLOSE, dist * CRUISE_LIFT), SPAN_CRUISE_MAX)


def leg_zoom(pts, i: int):
    """The three spans a leg moves through: this stop, cruise, the next."""
    a, b = pts[i], pts[i + 1]
    s0, s1 = stop_span(pts, i), stop_span(pts, i + 1)
    far = max(s0, s1, leg_span(a, b))
    return s0, far, s1


def leg_camera(pts, i: int, t: float):
    a, b = pts[i], pts[i + 1]
    s0, far, s1 = leg_zoom(pts, i)
    pos = arc_point(oc._arc(a, b), leg_progress(s0, far, s1, t))
    # Zoom is interpolated geometrically, not linearly: doubling the span
    # always takes the same time, which is what makes the pull-out read as one
    # continuous move instead of a rush followed by a crawl.
    return (pos, span_profile(s0, far, s1, t)), heading_at(pts, i, t)


def heading_at(pts, i: int, t: float) -> float:
    """Which way the plane is pointing at t, from the arc's own tangent."""
    seg = oc._arc(pts[i], pts[i + 1])
    s0, far, s1 = leg_zoom(pts, i)
    u = leg_progress(s0, far, s1, t)
    step = 1.0 / (len(seg) - 1)
    p0 = arc_point(seg, max(0.0, u - step))
    p1 = arc_point(seg, min(1.0, u + step))
    if p0 == p1:
        p0, p1 = seg[0], seg[1]
    return math.degrees(math.atan2(-(p1[1] - p0[1]), p1[0] - p0[0]))


def departure_heading(pts, i: int) -> float:
    """Where the plane points while sitting at stop i: down the next leg."""
    if i + 1 < len(pts):
        return heading_at(pts, i, 0.0)
    if i > 0:
        return heading_at(pts, i - 1, 1.0)
    return 0.0


def blend_heading(h0: float, h1: float, t: float) -> float:
    """Turn the short way round, easing in and out of the pivot."""
    d = (h1 - h0 + 180.0) % 360.0 - 180.0
    return h0 + d * ease(t)


def lerp_box(b0, b1, t):
    """Ease from one view to another, zooming geometrically.

    Lerping the corners would make the pull-back crawl at the wide end, where
    the same number of pixels a frame is a much smaller proportional change.
    Interpolating the span geometrically keeps the apparent rate constant.
    """
    e = ease(t)
    s0, s1 = b0[2] - b0[0], b1[2] - b1[0]
    span = s0 * (s1 / s0) ** e
    span_y = span * (BAND_H / BAND_W)
    cx = (b0[0] + b0[2]) / 2 + ((b1[0] + b1[2]) - (b0[0] + b0[2])) / 2 * e
    cy = (b0[1] + b0[3]) / 2 + ((b1[1] + b1[3]) - (b0[1] + b0[3])) / 2 * e
    return (cx - span / 2, cy - span_y / 2, cx + span / 2, cy + span_y / 2)


# --- plane -------------------------------------------------------------------
_PLANE_CACHE: dict = {}


def plane_sprite(size: int):
    """The ✈️ colour emoji, scaled down from its native bitmap strike.

    Noto Color Emoji is a CBDT bitmap face: it only renders at 109px, so the
    sprite is drawn once there and resized. Checked at four headings before
    committing to it -- it scales and rotates with clean edges on the indigo.
    """
    if size in _PLANE_CACHE:
        return _PLANE_CACHE[size]
    sprite = None
    if EMOJI_FONT.exists():
        try:
            f = ImageFont.truetype(str(EMOJI_FONT), 109)
            im = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((120, 120), "\u2708\ufe0f", font=f,
                                    embedded_color=True, anchor="mm")
            box = im.getbbox()
            if box:
                sprite = im.crop(box).resize((size, size), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            sprite = None
    if sprite is None:                      # no colour emoji: plain glyph
        f = _font("Bold", size)
        sprite = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        ImageDraw.Draw(sprite).text((size, size), "\u2708", font=f,
                                    fill=TEXT + (255,), anchor="mm")
    _PLANE_CACHE[size] = (sprite, PLANE_GLYPH_HEADING)
    return _PLANE_CACHE[size]


def draw_plane(im, xy, heading: float, size: int = 92):
    sprite, own = plane_sprite(size)
    rot = sprite.rotate(heading - own, resample=Image.BICUBIC, expand=True)
    im.paste(rot, (int(xy[0] - rot.width / 2), int(xy[1] - rot.height / 2)), rot)


# --- static chrome, built once per clip --------------------------------------
_ROUND_MASK: dict = {}


def _round_mask(size, radius):
    key = (size, radius)
    if key not in _ROUND_MASK:
        m = Image.new("L", size, 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                            radius=radius, fill=255)
        _ROUND_MASK[key] = m
    return _ROUND_MASK[key]


def build_chrome():
    """Background, header pill and caption card: identical on every map frame."""
    im = _gradient((W, H), BG_TOP, BG_BOT).convert("RGB")
    d = ImageDraw.Draw(im)
    _card(d, [MAP_X0 - 6, MAP_TOP - 6, MAP_X1 + 6, MAP_BOTTOM + 6],
          radius=CARD_R + 6, fill=CARD, outline=CARD_EDGE, width=2)
    _card(d, [MAP_X0, MAP_BOTTOM + 44, MAP_X1, H - 60], radius=CARD_R)
    return im


def draw_map_band(rings, box):
    band = Image.new("RGB", (BAND_W, BAND_H), MAP_WATER)
    d = ImageDraw.Draw(band)
    left, top, right, bottom = box
    sx, sy = BAND_W / (right - left), BAND_H / (bottom - top)
    for x0, y0, x1, y1, pts in rings:
        if x1 < left or x0 > right or y1 < top or y0 > bottom:
            continue
        d.polygon([((x - left) * sx, (y - top) * sy) for x, y in pts],
                  fill=MAP_LAND, outline=MAP_COAST)
    return band


def draw_route_on(band, box, pts, landed: int, flying=None, *, scale=1.0):
    d = ImageDraw.Draw(band, "RGBA")
    w = max(3, int(7 * scale))
    for i in range(max(0, landed - 1)):
        seg = [to_band_scaled(box, q, band.size) for q in oc._arc(pts[i], pts[i + 1])]
        d.line(seg, fill=ROUTE + (220,), width=w, joint="curve")
    if flying is not None:
        i, t = flying
        seg = [to_band_scaled(box, q, band.size) for q in oc._arc(pts[i], pts[i + 1])]
        cut = max(2, int(len(seg) * ease(t)))
        d.line(seg[:cut], fill=ROUTE + (220,), width=w, joint="curve")
    for i in range(min(landed, len(pts))):
        x, y = to_band_scaled(box, pts[i], band.size)
        r = max(4, int((17 if i == landed - 1 else 12) * scale))
        d.ellipse([x - r, y - r, x + r, y + r], fill=ACCENT_B,
                  outline=(255, 255, 255), width=max(2, int(4 * scale)))


def to_band_scaled(box, pt, size):
    left, top, right, bottom = box
    return ((pt[0] - left) * size[0] / (right - left),
            (pt[1] - top) * size[1] / (bottom - top))


def compose_map_frame(chrome, rings, box, pts, landed, flying, plane):
    im = chrome.copy()
    band = draw_map_band(rings, box)
    draw_route_on(band, box, pts, landed, flying)
    im.paste(band, (MAP_X0, MAP_TOP), _round_mask(band.size, CARD_R))
    if plane is not None:
        xy, heading = plane
        draw_plane(im, (xy[0] + MAP_X0, xy[1] + MAP_TOP), heading)
    return im


def draw_caption(im, *, landed, total, club, place, thinking=False):
    d = ImageDraw.Draw(im)
    _accent_pill(im, (MAP_X0, 92), "GUESS THE PLAYER")
    d = ImageDraw.Draw(im)
    if thinking:
        d.text((MAP_X0, 210), "Route complete", font=_font("Medium", 34),
               fill=MUTED)
        f = _font("Bold", 96)
        d.text((MAP_X0 + 8, (MAP_BOTTOM + 44 + H - 60) // 2), "Who is it?",
               font=f, fill=TEXT, anchor="lm")
        return
    d.text((MAP_X0, 210), f"STOP {landed} OF {total}",
           font=_font("Medium", 34), fill=MUTED)
    mid = (MAP_BOTTOM + 44 + H - 60) // 2
    if club:
        f_club = _fit(d, club, "Bold", 82, BAND_W - 80)
        d.text((MAP_X0 + 40, mid - 34), club, font=f_club, fill=TEXT,
               anchor="lm")
    if place:
        f_place = _fit(d, place, "Medium", 44, BAND_W - 80)
        d.text((MAP_X0 + 40, mid + 52), place, font=f_place, fill=MUTED,
               anchor="lm")


def _portrait(face, size: int):
    """The portrait square: a real headshot when there is one, else a mark.

    Headshots arrive as RGBA with a transparent background, so they are
    composited onto the card colour -- a plain convert("RGB") would flatten
    the alpha onto black and leave a hole in the layout. Aspect is kept by
    cover-cropping rather than squashing the face into a square.
    """
    base = Image.new("RGB", (size, size), CARD)
    if face is None:
        d = ImageDraw.Draw(base)
        hr = size * 0.20
        hx, hy = size / 2, size * 0.36
        d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=MAP_COAST)
        br = size * 0.34
        d.ellipse([hx - br, size * 0.62, hx + br, size * 1.35], fill=MAP_COAST)
        return base
    fw, fh = face.size
    k = size / min(fw, fh)
    face = face.resize((max(1, round(fw * k)), max(1, round(fh * k))),
                       Image.LANCZOS)
    left = (face.size[0] - size) // 2
    face = face.crop((left, 0, left + size, size))
    base.paste(face, (0, 0), face)
    return base


# --- full-screen reveal ------------------------------------------------------
def _gradient_text(im, xy, text, font, *, anchor="lm", c0=ACCENT_A,
                   c1=ACCENT_B, window=None):
    """Text filled with the purple-to-pink gradient, not a flat colour.

    window is the horizontal range the gradient is sampled from. Passing the
    same window for several pieces of text makes them read as one ramp instead
    of each restarting at purple, which is what gave a one-digit number a very
    different colour from a two-digit one.
    """
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = d.textbbox(xy, text, font=font, anchor=anchor)
    w, h = max(1, int(x1 - x0) + 6), max(1, int(y1 - y0) + 6)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((xy[0] - x0 + 3, xy[1] - y0 + 3), text,
                              font=font, fill=255, anchor=anchor)
    if window is None:
        grad = _gradient((w, h), c0, c1, horizontal=True)
    else:
        wx0, wx1 = window
        full = _gradient((max(1, int(wx1 - wx0)), h), c0, c1, horizontal=True)
        left = int(x0) - 3 - int(wx0)
        grad = full.crop((max(0, left), 0, max(1, left + w), h))
        if grad.size != (w, h):
            grad = grad.resize((w, h))
    im.paste(grad, (int(x0) - 3, int(y0) - 3), mask)
    return x1 - x0


def _grad_edge(im, box, radius, width=3):
    """A rounded panel whose hairline border is the gradient."""
    x0, y0, x1, y1 = (int(v) for v in box)
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    md.rounded_rectangle([width, width, w - 1 - width, h - 1 - width],
                         radius=max(1, radius - width), fill=0)
    im.paste(_gradient((w, h), ACCENT_A, ACCENT_B, horizontal=True), (x0, y0),
             mask)


def _grad_pill(im, xy, text, *, size=34, pad=(24, 10)):
    f = _font("SemiBold", size)
    d = ImageDraw.Draw(im)
    w = int(d.textlength(text, font=f) + pad[0] * 2)
    h = int(size + pad[1] * 2)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           radius=h // 2, fill=255)
    im.paste(_gradient((w, h), ACCENT_A, ACCENT_B, horizontal=True),
             (int(xy[0]), int(xy[1])), mask)
    d.text((xy[0] + w / 2, xy[1] + h / 2), text, font=f, fill=TEXT,
           anchor="mm")
    return w, h


REVEAL_X0, REVEAL_X1 = 40, W - 40
REVEAL_W = REVEAL_X1 - REVEAL_X0


def build_reveal(name, face, stints, span, *, credit=""):
    """The answer as its own screen: who it was, and every club in order.

    The route in miniature used to sit here. It is the one thing the viewer has
    just spent twenty seconds looking at, and at this size the club names were
    the part nobody could read -- so the map goes and the list arrives.
    """
    im = _gradient((W, H), BG_TOP, BG_BOT).convert("RGB")
    d = ImageDraw.Draw(im)
    _accent_pill(im, (REVEAL_X0, 82), "THE ANSWER")
    d = ImageDraw.Draw(im)

    teams = [(s.get("team") or "").strip() for s in stints]
    teams = [t for t in teams if t]
    countries = [(s.get("country") or "").strip() for s in stints]

    # --- one hero panel, gradient-edged: portrait, name, span, counts --------
    hy0, hy1 = 178, 594
    _card(d, [REVEAL_X0, hy0, REVEAL_X1, hy1], radius=CARD_R, fill=CARD,
          outline=CARD, width=1)
    _grad_edge(im, [REVEAL_X0, hy0, REVEAL_X1, hy1], CARD_R, width=3)
    d = ImageDraw.Draw(im)

    pt = 252
    px, py = REVEAL_X0 + 30, hy0 + 30
    im.paste(_portrait(face, pt), (px, py), _round_mask((pt, pt), 34))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([px, py, px + pt, py + pt], radius=34,
                        outline=CARD_EDGE, width=3)
    if credit:
        # CC BY and CC BY-SA require the credit to travel with the picture, so
        # it is rendered into the frame rather than left to a post caption.
        f_cr = _fit(d, credit, "Regular", 19, REVEAL_W - 60)
        d.text((REVEAL_X0 + 30, hy1 + 22), credit, font=f_cr, fill=MUTED,
               anchor="lt")

    nx = px + pt + 34
    nw = REVEAL_X1 - 34 - nx
    f_name = _fit(d, name, "Bold", 82, nw)
    d.text((nx, py + 74), name, font=f_name, fill=TEXT, anchor="lm")
    if span:
        _grad_pill(im, (nx, py + 118), span, size=32)
        d = ImageDraw.Draw(im)

    # counts as a scoreboard rather than three identical boxes
    sy = py + pt + 46
    d.line([REVEAL_X0 + 40, sy - 30, REVEAL_X1 - 40, sy - 30], fill=CARD_EDGE,
           width=2)
    cols = [(len(stints), "STOPS"), (len({c for c in countries if c}),
                                     "COUNTRIES")]
    step = REVEAL_W / len(cols)
    for i, (val, lab) in enumerate(cols):
        cx = REVEAL_X0 + step * (i + 0.5)
        if i:
            d.line([REVEAL_X0 + step * i, sy - 12, REVEAL_X0 + step * i,
                    sy + 60], fill=CARD_EDGE, width=2)
        _gradient_text(im, (cx, sy + 12), str(val), _font("Bold", 64),
                       anchor="mm", window=(REVEAL_X0 + 40, REVEAL_X1 - 40))
        d = ImageDraw.Draw(im)
        d.text((cx, sy + 58), lab, font=_font("Medium", 24), fill=MUTED,
               anchor="mm")

    # --- every club, in career order ----------------------------------------
    f_sec = _font("SemiBold", 28)
    d.text((REVEAL_X0 + 4, 652), "CAREER PATH", font=f_sec, fill=MUTED,
           anchor="lm")
    rule_x = REVEAL_X0 + 4 + int(d.textlength("CAREER PATH", font=f_sec)) + 28
    im.paste(_gradient((REVEAL_X1 - rule_x, 3), ACCENT_A, ACCENT_B,
                       horizontal=True), (rule_x, 651))
    d = ImageDraw.Draw(im)

    ly0, ly1 = 700, H - 56
    n = max(1, len(stints))
    row = min(104.0, (ly1 - ly0) / n)
    top = ly0 + max(0.0, ((ly1 - ly0) - row * n) / 2)

    # the spine: the route, redrawn as a timeline
    spine_x = REVEAL_X0 + 26
    if n > 1:
        y_a, y_b = top + row / 2, top + row * (n - 0.5)
        strip = _gradient((6, int(y_b - y_a)), ACCENT_A, ACCENT_B)
        im.paste(strip, (spine_x - 3, int(y_a)))
        d = ImageDraw.Draw(im)

    name_size = int(min(46, row * 0.46))
    for i, st in enumerate(stints):
        cy = top + row * (i + 0.5)
        r = 11 if i in (0, n - 1) else 8
        d.ellipse([spine_x - r, cy - r, spine_x + r, cy + r],
                  fill=ACCENT_B if i == n - 1 else CARD,
                  outline=ACCENT_B, width=3)
        team = (st.get("team") or "").strip() or "—"
        country = (countries[i] or "").upper()
        f_ct = _font("Medium", max(20, int(name_size * 0.52)))
        cw = d.textlength(country, font=f_ct) if country else 0
        tx = spine_x + 34
        f_t = _fit(d, team, "SemiBold", name_size,
                   REVEAL_X1 - tx - cw - 34)
        d.text((tx, cy), team, font=f_t, fill=TEXT, anchor="lm")
        if country:
            d.text((REVEAL_X1, cy), country, font=f_ct, fill=MUTED,
                   anchor="rm")
        if i < n - 1:
            d.line([tx, cy + row / 2, REVEAL_X1, cy + row / 2],
                   fill=CARD_EDGE, width=1)
    return im


def clip_view_for(pts, size):
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    need_x = max(x1 - x0, 1) * 1.3
    need_y = max(y1 - y0, 1) * 1.3
    span_x = min(max(need_x, need_y * (size[0] / size[1]), SPAN_CLOSE), SPAN_MAX)
    span_y = span_x * (size[1] / size[0])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    top = cy - span_y / 2
    if span_y <= oc.BH:
        top = max(0.0, min(top, oc.BH - span_y))
    else:
        top = (oc.BH - span_y) / 2
    return (cx - span_x / 2, top, cx + span_x / 2, top + span_y)


def draw_map_band_sized(rings, box, size):
    band = Image.new("RGB", size, MAP_WATER)
    d = ImageDraw.Draw(band)
    left, top, right, bottom = box
    sx, sy = size[0] / (right - left), size[1] / (bottom - top)
    for x0, y0, x1, y1, pts in rings:
        if x1 < left or x0 > right or y1 < top or y0 > bottom:
            continue
        d.polygon([((x - left) * sx, (y - top) * sy) for x, y in pts],
                  fill=MAP_LAND, outline=MAP_COAST)
    return band


# --- plan --------------------------------------------------------------------
def frame_plan(pts: list) -> list:
    t = timings(pts)
    n_stops = len(pts)
    plan = []
    for _ in range(int(T_INTRO * FPS)):
        plan.append({"kind": "intro", "landed": 1, "leg": None, "t": 0.0,
                     "reveal": False})
    for i in range(n_stops - 1):
        n_f = max(1, int(t["flight"][i] * FPS))
        for k in range(n_f):
            plan.append({"kind": "flight", "landed": i + 1, "leg": i,
                         "t": (k + 1) / n_f, "reveal": False})
        n_l = max(1, int(t["land"][i] * FPS))
        for k in range(n_l):
            # t runs 0..1 across the beat on the ground, so the plane can pivot
            # towards the next leg instead of snapping to it at take-off.
            plan.append({"kind": "land", "landed": i + 2, "leg": i,
                         "t": (k + 1) / n_l, "reveal": False})
    n_t = int(T_THINK * FPS)
    for k in range(n_t):
        plan.append({"kind": "think", "landed": n_stops, "leg": None,
                     "t": min(1.0, (k + 1) / (n_t * 0.45)), "reveal": False})
    for _ in range(int(T_REVEAL * FPS)):
        plan.append({"kind": "reveal", "landed": n_stops, "leg": None,
                     "t": 1.0, "reveal": True})
    return plan


def frame_camera(pts, fr, final_box):
    """The view box and the plane for one frame, with no drawing.

    Split out from render_frame so the motion can be measured frame by frame
    rather than judged from the source: see test_camera_motion_is_smooth.
    """
    kind = fr["kind"]
    if kind == "intro":
        box = box_from(pts[0], stop_span(pts, 0))
        return box, (to_band(box, pts[0]), departure_heading(pts, 0))
    if kind == "flight":
        (center, span), heading = leg_camera(pts, fr["leg"], fr["t"])
        box = box_from(center, span)
        return box, (to_band(box, center), heading)
    if kind == "land":
        i = fr["leg"]
        box = box_from(pts[i + 1], stop_span(pts, i + 1))
        # On the ground the plane swings round to face the next leg, so the
        # take-off that follows continues the same movement.
        arrive = heading_at(pts, i, 1.0)
        return box, (to_band(box, pts[i + 1]),
                     blend_heading(arrive, departure_heading(pts, i + 1),
                                   fr["t"]))
    box = lerp_box(box_from(pts[-1], stop_span(pts, len(pts) - 1)),
                   final_box, fr["t"])
    # The plane stays parked at the last stop rather than blinking out the
    # moment the route completes.
    return box, (to_band(box, pts[-1]),
                 heading_at(pts, len(pts) - 2, 1.0) if len(pts) > 1 else 0.0)


def render_frame(chrome, rings, pts, stints, fr, final_box, *, reveal_im=None):
    """One frame. The descriptor decides whether this is the answer."""
    if fr.get("reveal"):
        if reveal_im is None:
            raise RuntimeError("reveal frame without a reveal screen")
        return reveal_im
    kind, landed = fr["kind"], fr["landed"]
    box, plane = frame_camera(pts, fr, final_box)
    im = compose_map_frame(chrome, rings, box, pts, landed,
                           (fr["leg"], fr["t"]) if kind == "flight" else None,
                           plane)
    club = place = ""
    if kind != "think":
        st = stints[min(landed, len(stints)) - 1]
        club = (st.get("team") or "").strip()
        place = ", ".join(x for x in [(st.get("city") or "").strip(),
                                      (st.get("country") or "").strip()] if x)
    draw_caption(im, landed=min(landed, len(pts)), total=len(pts), club=club,
                 place=place, thinking=(kind == "think"))
    return im


class Photos:
    """Where a player's picture comes from, in order of preference.

    1. The official NBA headshot, which is a clean transparent cut-out but only
       exists for current players.
    2. A freely licensed Commons photo, cached in the repo by
       scripts/commons_photos.py along with the credit it requires.
    3. The drawn mark.

    Deliberately separate from og_cards.Headshots rather than folded into it:
    the site's own cards keep the behaviour they already had, and only the
    clips take the wider net.
    """

    def __init__(self, shots, *, licenses: set | None = None):
        self.shots = shots
        self.licenses = licenses or set(CLIP_LICENSES)
        self.commons = {}
        try:
            doc = json.loads(COMMONS_MANIFEST.read_text(encoding="utf-8"))
            self.commons = doc.get("players", {})
        except (OSError, ValueError):
            pass
        self.cache: dict = {}

    def get(self, name: str) -> tuple:
        """(image or None, credit line or empty, source label)."""
        if name in self.cache:
            return self.cache[name]
        face = self.shots.face(name)
        if face is not None:
            got = (face, "", "headshot")
        else:
            got = (None, "", "silhouette")
            rec = self.commons.get(name)
            if rec and rec.get("license") in self.licenses:
                path = ROOT / rec.get("local", "")
                if path.exists():
                    try:
                        im = Image.open(path).convert("RGBA")
                        credit = (f"Photo: {rec['attribution']}"
                                  if rec.get("attribution_required") else "")
                        got = (im, credit, "commons")
                    except OSError:
                        pass
        self.cache[name] = got
        return got


def build_clip(player: dict, rings, coords, shots, out_dir: Path) -> dict:
    name = player.get("display_name") or player.get("player") or ""
    r = route(player, coords)
    stints = [s for s, _ in r]
    pts = [oc.project(c[1], c[0], oc.BW, oc.BH) for _, c in r]
    final_box = clip_view(pts)
    face, credit, source = shots.get(name)
    span = _career_span(stints)

    chrome = build_chrome()
    reveal_im = build_reveal(name, face, stints, span, credit=credit)

    plan = frame_plan(pts)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug(player.get('player') or name)}.mp4"
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-framerate", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "23",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    for fr in plan:
        im = render_frame(chrome, rings, pts, stints, fr, final_box,
                          reveal_im=reveal_im)
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {name}")
    return {"player": name, "file": out, "stops": len(pts), "frames": len(plan),
            "seconds": len(plan) / FPS, "bytes": out.stat().st_size,
            "face": face is not None, "source": source,
            "render_s": time.time() - t0}


def _career_span(stints: list) -> str:
    """First and last year across the career -- REVEAL ONLY.

    Years are the strongest possible hint at a player's identity, so this is
    called nowhere near a pre-reveal frame.
    """
    ys = [int(y) for s in stints for y in re.findall(r"\d{4}", s.get("years") or "")]
    if not ys:
        return ""
    return f"{min(ys)}–{max(ys)}"


def ffmpeg_exe() -> str:
    """A working ffmpeg without depending on one being installed.

    imageio-ffmpeg ships a static build, so the same binary is used here and on
    a CI runner whatever its image happens to include.
    """
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, help="build N spread across the ranking")
    ap.add_argument("--count", type=int, help="build the top N by recognisability")
    ap.add_argument("--offset", type=int, default=0,
                    help="skip this many from the top, for a different batch")
    ap.add_argument("--player", action="append", help="build this player by name")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()
    if Image is None:
        sys.exit("Pillow is required:  pip install Pillow")

    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    coords = oc.Coords()
    ranked = candidates(players, coords)
    by_name = {(p.get("display_name") or p["player"]): p for p in players}

    if args.player:
        picks = [by_name[n] for n in args.player if n in by_name]
    elif args.count:
        picks = ranked[args.offset:args.offset + args.count]
    else:
        k = args.sample or 3
        # spread across the ranking so a sample shows the range, not just the top
        step = max(1, len(ranked) // k)
        picks = [ranked[i * step] for i in range(k)]

    rings = oc.load_rings()
    shots = Photos(oc.Headshots(enabled=True))
    out_dir = Path(args.out)
    total = 0
    for p in picks:
        info = build_clip(p, rings, coords, shots, out_dir)
        total += info["bytes"]
        print(f"  {info['player']:26} {info['stops']:2d} stops  "
              f"{info['seconds']:5.1f}s  {info['bytes']/1024/1024:5.2f} MB  "
              f"{info['source']:10s}  "
              f"-> {info['file'].relative_to(ROOT)}")
    print(f"\n{len(picks)} clips, {total/1024/1024:.1f} MB total")


if __name__ == "__main__":
    main()

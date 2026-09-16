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
TARGET_BODY = 11.0
PER_LEG_MIN, PER_LEG_MAX = 0.95, 2.00
FLIGHT_SHARE = 0.66

SPAN_CLOSE = oc.BW * (26 / 360)
SPAN_MAX = oc.BW * 0.92
CRUISE_LIFT = 1.9

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


def timings(n_stops: int) -> dict:
    legs = max(1, n_stops - 1)
    per = max(PER_LEG_MIN, min(PER_LEG_MAX, TARGET_BODY / legs))
    flight = per * FLIGHT_SHARE
    return {"per": per, "flight": flight, "land": per - flight, "legs": legs,
            "body": per * legs,
            "total": T_INTRO + per * legs + T_THINK + T_REVEAL}


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
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def bell(t: float) -> float:
    return math.sin(math.pi * max(0.0, min(1.0, t)))


def leg_camera(a, b, t: float):
    seg = oc._arc(a, b)
    i = ease(t) * (len(seg) - 1)
    lo, frac = int(i), i - int(i)
    hi = min(lo + 1, len(seg) - 1)
    pos = (seg[lo][0] + (seg[hi][0] - seg[lo][0]) * frac,
           seg[lo][1] + (seg[hi][1] - seg[lo][1]) * frac)
    dist = math.hypot(b[0] - a[0], b[1] - a[1])
    far = min(max(SPAN_CLOSE, dist * CRUISE_LIFT), SPAN_MAX)
    span = SPAN_CLOSE + (far - SPAN_CLOSE) * bell(t)
    heading = math.degrees(math.atan2(-(seg[hi][1] - seg[lo][1]),
                                      seg[hi][0] - seg[lo][0]))
    return (pos, span), heading


def lerp_box(b0, b1, t):
    e = ease(t)
    return tuple(v0 + (v1 - v0) * e for v0, v1 in zip(b0, b1))


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
def build_reveal(rings, pts, name, face, stats):
    """The answer as its own screen: name, portrait, counts, route in miniature."""
    im = _gradient((W, H), BG_TOP, BG_BOT).convert("RGB")
    d = ImageDraw.Draw(im)
    _accent_pill(im, (MAP_X0, 92), "THE ANSWER")
    d = ImageDraw.Draw(im)

    top = 230
    portrait = 300
    _card(d, [MAP_X0, top, MAP_X0 + portrait, top + portrait], radius=36,
          fill=CARD)
    inner = portrait - 16
    im.paste(_portrait(face, inner), (MAP_X0 + 8, top + 8),
             _round_mask((inner, inner), 30))
    name_x, name_w = MAP_X0 + portrait + 40, W - MAP_X0 * 2 - portrait - 40
    f_name = _fit(d, name, "Bold", 92, name_w)
    d.text((name_x, top + portrait / 2), name, font=f_name, fill=TEXT,
           anchor="lm")

    # stat blocks
    y = top + portrait + 56
    gap, n = 24, len(stats)
    bw = (BAND_W - gap * (n - 1)) // n
    for i, (val, lab) in enumerate(stats):
        x = MAP_X0 + i * (bw + gap)
        _card(d, [x, y, x + bw, y + 190], radius=32)
        # A year span is far wider than a two-digit count, so the value is
        # fitted to its card rather than set at a fixed size.
        f_val = _fit(d, str(val), "Bold", 62, bw - 28)
        d.text((x + bw / 2, y + 74), str(val), font=f_val, fill=ACCENT_B,
               anchor="mm")
        d.text((x + bw / 2, y + 142), lab, font=_font("Medium", 26),
               fill=MUTED, anchor="mm")

    # the whole journey, in miniature
    my0 = y + 190 + 56
    my1 = H - 90
    _card(d, [MAP_X0, my0, MAP_X1, my1], radius=CARD_R)
    inner = (BAND_W - 24, my1 - my0 - 24)
    box = clip_view_for(pts, inner)
    band = draw_map_band_sized(rings, box, inner)
    draw_route_on(band, box, pts, len(pts), scale=0.55)
    im.paste(band, (MAP_X0 + 12, my0 + 12), _round_mask(inner, 32))
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
def frame_plan(n_stops: int) -> list:
    t = timings(n_stops)
    plan = []
    for _ in range(int(T_INTRO * FPS)):
        plan.append({"kind": "intro", "landed": 1, "leg": None, "t": 0.0,
                     "reveal": False})
    for i in range(n_stops - 1):
        n_f = max(1, int(t["flight"] * FPS))
        for k in range(n_f):
            plan.append({"kind": "flight", "landed": i + 1, "leg": i,
                         "t": (k + 1) / n_f, "reveal": False})
        for _ in range(max(1, int(t["land"] * FPS))):
            plan.append({"kind": "land", "landed": i + 2, "leg": i, "t": 1.0,
                         "reveal": False})
    n_t = int(T_THINK * FPS)
    for k in range(n_t):
        plan.append({"kind": "think", "landed": n_stops, "leg": None,
                     "t": min(1.0, (k + 1) / (n_t * 0.45)), "reveal": False})
    for _ in range(int(T_REVEAL * FPS)):
        plan.append({"kind": "reveal", "landed": n_stops, "leg": None,
                     "t": 1.0, "reveal": True})
    return plan


def render_frame(chrome, rings, pts, stints, fr, final_box, *, reveal_im=None):
    """One frame. The descriptor decides whether this is the answer."""
    if fr.get("reveal"):
        if reveal_im is None:
            raise RuntimeError("reveal frame without a reveal screen")
        return reveal_im
    kind, landed = fr["kind"], fr["landed"]
    plane = None
    if kind == "intro":
        box = box_from(pts[0], SPAN_CLOSE)
        plane = (to_band(box, pts[0]), 0.0)
    elif kind == "flight":
        (center, span), heading = leg_camera(pts[fr["leg"]],
                                             pts[fr["leg"] + 1], fr["t"])
        box = box_from(center, span)
        plane = (to_band(box, center), heading)
    elif kind == "land":
        box = box_from(pts[fr["leg"] + 1], SPAN_CLOSE)
        plane = (to_band(box, pts[fr["leg"] + 1]), 0.0)
    else:                                            # think
        box = lerp_box(box_from(pts[-1], SPAN_CLOSE), final_box, fr["t"])
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


def build_clip(player: dict, rings, coords, shots, out_dir: Path) -> dict:
    name = player.get("display_name") or player.get("player") or ""
    r = route(player, coords)
    stints = [s for s, _ in r]
    pts = [oc.project(c[1], c[0], oc.BW, oc.BH) for _, c in r]
    final_box = clip_view(pts)
    face = shots.face(name)
    countries = len({(s.get("country") or "").strip() for s in stints} - {""})
    span = _career_span(stints)

    chrome = build_chrome()
    reveal_im = build_reveal(rings, pts, name, face, [
        (len(pts), "STOPS"), (countries, "COUNTRIES"),
        (span or "—", "YEARS")])

    plan = frame_plan(len(pts))
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
            "face": face is not None, "render_s": time.time() - t0}


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
    shots = oc.Headshots(enabled=True)
    out_dir = Path(args.out)
    total = 0
    for p in picks:
        info = build_clip(p, rings, coords, shots, out_dir)
        total += info["bytes"]
        print(f"  {info['player']:26} {info['stops']:2d} stops  "
              f"{info['seconds']:5.1f}s  {info['bytes']/1024/1024:5.2f} MB  "
              f"{'portrait' if info['face'] else 'silhouette'}  "
              f"-> {info['file'].relative_to(ROOT)}")
    print(f"\n{len(picks)} clips, {total/1024/1024:.1f} MB total")


if __name__ == "__main__":
    main()

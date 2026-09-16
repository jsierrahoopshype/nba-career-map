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
# A wider, shorter band than the frame's own 9:16. A route spanning the USA to
# China needs width; forcing it into a near-square band padded the frame with
# empty ocean. This also buys the type below more room, which is what a phone
# actually needs.
MAP_TOP, MAP_BOTTOM = 340, 1240
VIEW_PAD = 0.12

# Timing, in seconds. Per-stop time flexes so a 6-stop and a 14-stop career both
# land in the 15-25s window rather than one feeling rushed and the other slow.
T_INTRO = 1.4          # sitting at the first club before the first take-off
T_HOLD = 1.3           # camera pulled back over the finished route
T_REVEAL = 3.2
TARGET_BODY = 13.0     # the flying part, spread over the legs
PER_LEG_MIN, PER_LEG_MAX = 1.10, 2.20
FLIGHT_SHARE = 0.65    # of a leg; the rest is the landed beat

# Camera. Spans are in projection pixels; BW is 360 degrees of longitude.
SPAN_CLOSE = oc.BW * (26 / 360)     # arriving somewhere
SPAN_MAX = oc.BW * 0.92             # never quite the whole world
CRUISE_LIFT = 1.9                   # how far out a long crossing pulls back

RED = oc.RED
INK = oc.INK
MUTED = oc.MUTED
PANEL = (255, 255, 255)

_NBA = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA.add(_n)


def _font(name: str, size: int):
    return ImageFont.truetype(str(oc.FONT_DIR / name), size)


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
    land = per - flight
    return {"per": per, "flight": flight, "land": land, "legs": legs,
            "body": per * legs,
            "total": T_INTRO + per * legs + T_HOLD + T_REVEAL}


def _fit(draw, text, font_name, size, max_w):
    """Largest size at or below `size` whose text fits max_w."""
    while size > 18:
        f = _font(font_name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(font_name, 18)


def base_frame(rings, view) -> Image.Image:
    """The map at this view: band in the middle, opaque panels above and below."""
    left, top, right, bottom = view
    band = Image.new("RGB", (W, BAND_H), oc.WATER)
    sx, sy = W / (right - left), BAND_H / (bottom - top)
    d = ImageDraw.Draw(band)
    for x0, y0, x1, y1, pts in rings:
        if x1 < left or x0 > right or y1 < top or y0 > bottom:
            continue
        d.polygon([((x - left) * sx, (y - top) * sy) for x, y in pts],
                  fill=oc.LAND, outline=oc.COAST)
    full = Image.new("RGB", (W, H), PANEL)
    full.paste(band, (0, MAP_TOP))
    fd = ImageDraw.Draw(full)
    fd.line([(0, MAP_TOP), (W, MAP_TOP)], fill=(224, 226, 230), width=2)
    fd.line([(0, MAP_BOTTOM), (W, MAP_BOTTOM)], fill=(224, 226, 230), width=2)
    return full


def clip_view(pts: list) -> tuple[float, float, float, float]:
    """Crop box framing the whole route, for the final pull-back and reveal."""
    if not pts:
        return (0, 0, oc.BW, oc.BW * BAND_H / W)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    need_x = max(x1 - x0, 1) * (1 + VIEW_PAD * 2)
    need_y = max(y1 - y0, 1) * (1 + VIEW_PAD * 2)
    span_x = min(max(need_x, need_y * (W / BAND_H), SPAN_CLOSE), SPAN_MAX)
    return box_from(( (x0 + x1) / 2, (y0 + y1) / 2 ), span_x)


def box_from(center, span_x) -> tuple[float, float, float, float]:
    cx, cy = center
    span_y = span_x * (BAND_H / W)
    return (cx - span_x / 2, cy - span_y / 2, cx + span_x / 2, cy + span_y / 2)


def to_band(box, pt):
    """Projection-space point -> pixel inside the map band."""
    left, top, right, bottom = box
    return ((pt[0] - left) * W / (right - left),
            (pt[1] - top) * BAND_H / (bottom - top))


def ease(t: float) -> float:
    """Smoothstep, so the camera and the plane start and stop gently."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def bell(t: float) -> float:
    """0 at the ends, 1 in the middle: the shape of a cruise altitude."""
    return math.sin(math.pi * max(0.0, min(1.0, t)))


def leg_camera(a, b, t: float) -> tuple[tuple, float]:
    """Where the camera looks, and how wide, part-way through a leg.

    The plane stays centred. The span eases out over the crossing in
    proportion to its length, so an ocean leg pulls right back and a hop
    between two European clubs barely moves -- which is also what stops the
    frame ever being mostly empty sea.
    """
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


_PLANE_CACHE: dict = {}


def _glyph_heading(im) -> float:
    """Which way the drawn glyph points, measured rather than assumed.

    The nose is the ink pixel furthest from the ink centroid. Measuring it
    means the rotation stays correct if the font ever changes -- the first
    version hard-coded a 45 degree offset and flew every plane sideways.
    """
    px = im.convert("L").load()
    ink = [(x, y) for y in range(im.height) for x in range(im.width)
           if px[x, y] > 40]
    if not ink:
        return 0.0
    cx = sum(x for x, _ in ink) / len(ink)
    cy = sum(y for _, y in ink) / len(ink)
    nx, ny = max(ink, key=lambda q: (q[0] - cx) ** 2 + (q[1] - cy) ** 2)
    return math.degrees(math.atan2(-(ny - cy), nx - cx))


def plane_sprite(size: int):
    """The plane glyph and its measured heading, rendered once and cached."""
    if size in _PLANE_CACHE:
        return _PLANE_CACHE[size]
    font = _font("DejaVuSans.ttf", size)
    im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((size, size), "\u2708", font=font,
                            fill=INK + (255,), anchor="mm")
    _PLANE_CACHE[size] = (im, _glyph_heading(im))
    return _PLANE_CACHE[size]


def draw_plane(im, xy, heading: float, size: int = 76):
    sprite, own = plane_sprite(size)
    sprite = sprite.rotate(heading - own, resample=Image.BICUBIC, expand=False)
    x, y = xy
    im.paste(sprite, (int(x - sprite.width / 2), int(y - sprite.height / 2)),
             sprite)


def draw_route(im, box, pts, landed: int, flying=None):
    """Arcs already flown, markers for clubs visited. Never past `landed`."""
    layer = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    for i in range(max(0, landed - 1)):
        seg = [to_band(box, p) for p in oc._arc(pts[i], pts[i + 1])]
        ld.line(seg, fill=RED + (190,), width=7, joint="curve")
    if flying is not None:
        i, t = flying
        seg = [to_band(box, p) for p in oc._arc(pts[i], pts[i + 1])]
        cut = max(2, int(len(seg) * ease(t)))
        ld.line(seg[:cut], fill=RED + (190,), width=7, joint="curve")
    for i in range(min(landed, len(pts))):
        x, y = to_band(box, pts[i])
        r = 17 if i == landed - 1 else 12
        ld.ellipse([x - r, y - r, x + r, y + r], fill=RED,
                   outline=(255, 255, 255), width=4)
    im.paste(layer, (0, MAP_TOP), layer)


def draw_panels(im, *, club: str, landed: int, total: int, reveal=None,
                face=None):
    """Top prompt and bottom caption. No years anywhere unless revealing."""
    d = ImageDraw.Draw(im, "RGBA")
    title = _font("DejaVuSans-Bold.ttf", 62)
    if reveal is None:
        d.text((60, 92), "Guess the player", font=title, fill=INK)
        d.text((60, 178), f"STOP {landed} OF {total}",
               font=_font("DejaVuSans.ttf", 34), fill=MUTED)
        if club:
            f_club = _fit(d, club, "DejaVuSans-Bold.ttf", 92, W - 120)
            # Centred in the caption panel rather than pinned to its top, so a
            # one-line club name does not sit in a sea of white.
            d.text((60, (MAP_BOTTOM + H) // 2), club, font=f_club, fill=INK,
                   anchor="lm")
    else:
        name, stats = reveal
        d.text((60, 92), "The answer", font=title, fill=INK)
        d.text((60, 178), f"{total} STOPS", font=_font("DejaVuSans.ttf", 34),
               fill=MUTED)
        if face is not None:
            size = 300
            px, py = W - size - 60, (MAP_BOTTOM + H) // 2 - size // 2
            f = face.resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
            im.paste(f.convert("RGB"), (px, py), mask)
            d.ellipse([px, py, px + size, py + size],
                      outline=(226, 228, 232), width=5)
            text_w = W - size - 160
        else:
            text_w = W - 120
        mid = (MAP_BOTTOM + H) // 2
        f_name = _fit(d, name, "DejaVuSans-Bold.ttf", 104, text_w)
        d.text((60, mid - 40), name, font=f_name, fill=INK, anchor="lm")
        d.text((60, mid + 56), stats, font=_font("DejaVuSans.ttf", 44),
               fill=MUTED, anchor="lm")


def frame_plan(n_stops: int) -> list:
    """The whole clip as frame descriptors, before any drawing.

    Each entry is a dict; only entries with reveal=True may carry the answer,
    which is what the leak test checks.
    """
    t = timings(n_stops)
    plan = []
    for k in range(int(T_INTRO * FPS)):
        plan.append({"kind": "intro", "landed": 1, "leg": None, "t": 0.0,
                     "reveal": False})
    for i in range(n_stops - 1):
        for k in range(int(t["flight"] * FPS)):
            plan.append({"kind": "flight", "landed": i + 1, "leg": i,
                         "t": (k + 1) / max(1, int(t["flight"] * FPS)),
                         "reveal": False})
        for k in range(int(t["land"] * FPS)):
            plan.append({"kind": "land", "landed": i + 2, "leg": i, "t": 1.0,
                         "reveal": False})
    for k in range(int(T_HOLD * FPS)):
        plan.append({"kind": "pullback", "landed": n_stops, "leg": None,
                     "t": (k + 1) / max(1, int(T_HOLD * FPS)), "reveal": False})
    for k in range(int(T_REVEAL * FPS)):
        plan.append({"kind": "reveal", "landed": n_stops, "leg": None,
                     "t": 1.0, "reveal": True})
    return plan


def render_frame(rings, pts, stints, fr, final_box, *, reveal=None, face=None):
    """One frame, map and all. The camera is derived from the descriptor.

    The descriptor -- not the caller -- decides whether this is a reveal frame.
    Passing reveal= for a frame the plan says is pre-reveal draws nothing: the
    answer cannot be leaked by a caller getting an argument wrong, only by
    frame_plan itself being wrong, and that is what the plan test covers.
    """
    kind, landed = fr["kind"], fr["landed"]
    if not fr.get("reveal"):
        reveal, face = None, None
    heading = None
    if kind == "intro":
        box = box_from(pts[0], SPAN_CLOSE)
        plane_at = to_band(box, pts[0])
        heading = 0.0
    elif kind == "flight":
        (center, span), heading = leg_camera(pts[fr["leg"]],
                                             pts[fr["leg"] + 1], fr["t"])
        box = box_from(center, span)
        plane_at = to_band(box, center)
    elif kind == "land":
        box = box_from(pts[fr["leg"] + 1], SPAN_CLOSE)
        plane_at = to_band(box, pts[fr["leg"] + 1])
        heading = 0.0
    else:                                   # pullback and reveal
        last = box_from(pts[-1], SPAN_CLOSE)
        box = lerp_box(last, final_box, fr["t"]) if kind == "pullback" else final_box
        plane_at = None

    im = base_frame(rings, box)
    draw_route(im, box, pts, landed,
               flying=(fr["leg"], fr["t"]) if kind == "flight" else None)
    if plane_at is not None:
        px, py = plane_at
        draw_plane(im, (px, py + MAP_TOP), heading or 0.0)
    club = ""
    if reveal is None and landed >= 1:
        club = (stints[min(landed, len(stints)) - 1].get("team") or "").strip()
    draw_panels(im, club=club, landed=min(landed, len(pts)), total=len(pts),
                reveal=reveal, face=face)
    return im


def build_clip(player: dict, rings, coords, shots, out_dir: Path) -> dict:
    name = player.get("display_name") or player.get("player") or ""
    r = route(player, coords)
    stints = [s for s, _ in r]
    pts = [oc.project(c[1], c[0], oc.BW, oc.BH) for _, c in r]
    final_box = clip_view(pts)
    face = shots.face(name)
    countries = len({(s.get("country") or "").strip() for s in stints} - {""})
    years = _career_span(stints)
    stats = f"{len(pts)} clubs · {countries} countries" + (f" · {years}" if years else "")

    plan = frame_plan(len(pts))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug(player.get('player') or name)}.mp4"

    # Frames go straight down a pipe as raw RGB. Writing 600 PNGs and having
    # ffmpeg read them back was the real cost of the previous version; the map
    # re-projection this replaces it with is 2-7ms a frame.
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-framerate", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "23",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    for fr in plan:
        im = render_frame(rings, pts, stints, fr, final_box,
                          reveal=(name, stats) if fr["reveal"] else None,
                          face=face if fr["reveal"] else None)
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

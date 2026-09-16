"""Build "guess the player" clips: a career route drawing itself, then a reveal.

A vertical 9:16 clip for social. The map holds a fixed view of the whole route,
then stops land one at a time -- an arc draws in, a marker drops, and the club
and years appear -- so the viewer accumulates clues without knowing who it is.
The route completes, holds a beat, and only then does the name and portrait
appear.

WHAT IT REUSES. All the drawing comes from scripts/og_cards.py: the same
projection, the same vector country outlines drawn at the view's own crop, the
same red arcs, the same headshot fetch with its silhouette fallback. Nothing
about the visual language is re-invented here; this adds time to it.

NO EARLY LEAK. The name, the portrait and the stat line exist only in frames at
or after the reveal, which is asserted in the tests rather than eyeballed. Club
names and years DO appear as each stop lands -- that is the puzzle itself, not a
leak. No team logos are drawn at any point.

THE MAP IS DRAWN ONCE PER CLIP, not once per frame. The view is fixed for the
whole clip, so the basemap is rendered a single time and each frame composites
arcs, markers and text over a copy. That is the difference between a clip
taking ten seconds and taking a minute.

Run:  python3 scripts/quiz_clips.py --sample 3      # a few to look at
      python3 scripts/quiz_clips.py --count 50      # a batch
      python3 scripts/quiz_clips.py --player "Patty Mills"
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
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
MAP_TOP, MAP_BOTTOM = 430, 1190
VIEW_PAD = 0.12

# Timing, in seconds. Per-stop time flexes so a 6-stop and a 14-stop career both
# land in the 15-25s window rather than one feeling rushed and the other slow.
T_INTRO = 1.6
T_HOLD = 1.6
T_REVEAL = 3.2
TARGET_BODY = 13.0
PER_STOP_MIN, PER_STOP_MAX = 0.85, 1.55

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


def clip_view(pts: list) -> tuple[float, float, float, float]:
    """Crop box framing the route, in the SAME projection space as og_cards.

    The box is cut to the aspect of the MAP BAND (1080x1130), not of the whole
    1080x1920 frame. Forcing the frame's 9:16 onto the map is what wrecked the
    first attempt: a route spanning the USA to China needs a wide box, and
    stretching that to 9:16 left the route in a corner of an empty ocean. The
    band gets its own aspect; the panels above and below are opaque anyway.
    """
    if not pts:
        return (0, 0, oc.BW, oc.BW * BAND_H / W)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    need_x = max(x1 - x0, 1) * (1 + VIEW_PAD * 2)
    need_y = max(y1 - y0, 1) * (1 + VIEW_PAD * 2)
    span_x = min(max(need_x, need_y * (W / BAND_H)), oc.BW)
    span_y = span_x * (BAND_H / W)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return (cx - span_x / 2, cy - span_y / 2,
            cx + span_x / 2, cy + span_y / 2)


def frame_plan(n_stops: int) -> list:
    """The whole clip as (shown, arc_t, is_reveal) tuples, before any drawing.

    Pulled out so the no-leak rule is a property that can be asserted rather
    than something to eyeball: no entry may carry is_reveal before the final
    reveal block.
    """
    t = timings(n_stops)
    plan = []
    for _ in range(int(T_INTRO * FPS)):
        plan.append((0, 1.0, False))
    steps = int(t["per"] * FPS)
    draw_steps = max(1, int(steps * 0.45))
    for i in range(1, n_stops + 1):
        for k in range(steps):
            plan.append((i, min(1.0, (k + 1) / draw_steps), False))
    for _ in range(int(T_HOLD * FPS)):
        plan.append((n_stops, 1.0, False))
    for _ in range(int(T_REVEAL * FPS)):
        plan.append((n_stops, 1.0, True))
    return plan


def timings(n_stops: int) -> dict:
    per = max(PER_STOP_MIN, min(PER_STOP_MAX, TARGET_BODY / n_stops))
    body = per * n_stops
    return {"per": per, "body": body,
            "total": T_INTRO + body + T_HOLD + T_REVEAL}


# --- drawing -----------------------------------------------------------------
def _fit(draw, text, font_name, size, max_w):
    """Largest size at or below `size` whose text fits max_w."""
    while size > 18:
        f = _font(font_name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(font_name, 18)


def base_frame(rings, view) -> Image.Image:
    """The map, drawn once per clip, into the band; panels above and below."""
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


def draw_frame(base, pts, shown: int, arc_t: float, stints, *,
               reveal=None, face=None):
    """One frame. `shown` stops are placed; the arc into `shown` is arc_t done.

    reveal is None for every frame before the reveal, which is what keeps the
    answer out of the clip: there is no code path that can draw a name or a
    portrait while it is None.
    """
    im = base.copy()
    # Drawn on a band-sized layer and pasted, so an arc reaching past the map
    # can never paint over the text panels.
    layer = Image.new("RGBA", (W, BAND_H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    for i in range(min(shown, len(pts)) - 1):
        ld.line(oc._arc(pts[i], pts[i + 1]), fill=RED + (200,), width=7,
                joint="curve")
    if shown >= 2 and shown <= len(pts) and arc_t < 1.0:
        seg = oc._arc(pts[shown - 2], pts[shown - 1])
        cut = max(2, int(len(seg) * arc_t))
        ld.line(seg[:cut], fill=RED + (200,), width=7, joint="curve")
    for i in range(min(shown, len(pts))):
        x, y = pts[i]
        r = 18 if i == shown - 1 else 13
        ld.ellipse([x - r, y - r, x + r, y + r], fill=RED,
                   outline=(255, 255, 255), width=4)
    im.paste(layer, (0, MAP_TOP), layer)
    d = ImageDraw.Draw(im, "RGBA")

    # top panel: prompt and progress -- never the answer
    title = _font("DejaVuSans-Bold.ttf", 62)
    if reveal is None:
        d.text((60, 92), "Guess the player", font=title, fill=INK)
        lab = _font("DejaVuSans.ttf", 34)
        d.text((60, 178), f"STOP {min(shown, len(pts))} OF {len(pts)}",
               font=lab, fill=MUTED)
    else:
        d.text((60, 92), "The answer", font=title, fill=INK)
        d.text((60, 178), f"{len(pts)} STOPS", font=_font("DejaVuSans.ttf", 34),
               fill=MUTED)

    # bottom panel
    if reveal is None:
        idx = max(0, min(shown, len(stints)) - 1)
        if shown > 0:
            st = stints[idx]
            club = (st.get("team") or "").strip()
            years = (st.get("years") or "").strip()
            f_club = _fit(d, club, "DejaVuSans-Bold.ttf", 88, W - 120)
            d.text((60, MAP_BOTTOM + 96), club, font=f_club, fill=INK)
            d.text((60, MAP_BOTTOM + 224), years,
                   font=_font("DejaVuSansMono.ttf", 54), fill=RED)
            loc = ", ".join(x for x in [(st.get("city") or "").strip(),
                                        (st.get("country") or "").strip()] if x)
            d.text((60, MAP_BOTTOM + 314), loc,
                   font=_font("DejaVuSans.ttf", 42), fill=MUTED)
    else:
        name, stats = reveal
        # Only 29 of the ~840 eligible players have a portrait: the well-travelled
        # ones are mostly retired or overseas, and the headshots repo holds
        # current NBA players. So when there is no face the portrait slot is
        # dropped entirely and the name runs full width -- a generic grey
        # silhouette on a reveal frame reads as a missing asset, where a big
        # name reads as the point of the shot.
        if face is not None:
            size = 300
            px, py = W - size - 60, MAP_BOTTOM + 72
            f = face.resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
            im.paste(f.convert("RGB"), (px, py), mask)
            d.ellipse([px, py, px + size, py + size],
                      outline=(226, 228, 232), width=5)
            text_w = W - size - 160
        else:
            text_w = W - 120
        f_name = _fit(d, name, "DejaVuSans-Bold.ttf", 104, text_w)
        d.text((60, MAP_BOTTOM + 96), name, font=f_name, fill=INK)
        d.text((60, MAP_BOTTOM + 232), stats,
               font=_font("DejaVuSans.ttf", 44), fill=MUTED)
    return im


def build_clip(player: dict, rings, coords, shots, out_dir: Path) -> dict:
    name = player.get("display_name") or player.get("player") or ""
    r = route(player, coords)
    stints = [s for s, _ in r]
    # Same projection space as the OG cards, so a club sits where the site
    # already puts it.
    hi = [oc.project(c[1], c[0], oc.BW, oc.BH) for _, c in r]
    view = clip_view(hi)
    left, top, right, bottom = view
    sx, sy = W / (right - left), BAND_H / (bottom - top)
    # Band coordinates: the route layer is pasted at MAP_TOP.
    pts = [((x - left) * sx, (y - top) * sy) for x, y in hi]

    base = base_frame(rings, view)
    t = timings(len(pts))
    face = shots.face(name)
    countries = len({(s.get("country") or "").strip() for s in stints} - {""})
    stats = f"{len(pts)} clubs · {countries} countries"

    frames_dir = Path(tempfile.mkdtemp()) / "f"
    frames_dir.mkdir(parents=True)
    plan = frame_plan(len(pts))
    for n, (shown, arc_t, is_reveal) in enumerate(plan):
        im = draw_frame(base, pts, shown, arc_t, stints,
                        reveal=(name, stats) if is_reveal else None,
                        face=face if is_reveal else None)
        im.save(frames_dir / f"{n:05d}.png", "PNG", compress_level=1)
    n = len(plan)

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug(player.get('player') or name)}.mp4"
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(FPS),
           "-i", str(frames_dir / "%05d.png"),
           "-c:v", "libx264", "-preset", "medium", "-crf", "23",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    for f in frames_dir.glob("*.png"):
        f.unlink()
    return {"player": name, "file": out, "stops": len(pts), "frames": n,
            "seconds": n / FPS, "bytes": out.stat().st_size,
            "face": face is not None}


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
        picks = ranked[:args.count]
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

"""Tests for the guess-the-player clips.

The one that matters most is the leak test: a clip whose answer appears early
is worthless, and that is not something to check by watching.

Run:  python3 scripts/test_quiz_clips.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og_cards as oc  # noqa: E402
import quiz_clips as q  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                .read_text(encoding="utf-8"))
COORDS = oc.Coords()


def test_no_reveal_before_the_end():
    """No frame may carry the answer until the final reveal block."""
    for n in (6, 9, 14):
        plan = q.frame_plan(_ring(n))
        reveal_at = [i for i, f in enumerate(plan) if f["reveal"]]
        assert reveal_at, "clip has no reveal at all"
        first = reveal_at[0]
        assert reveal_at == list(range(first, len(plan))), \
            "reveal frames must be one unbroken block at the end"
        assert not any(f["reveal"] for f in plan[:first]), "answer leaks early"
        assert first >= int((q.T_INTRO + q.T_THINK) * q.FPS), \
            "reveal starts implausibly early"
        think = [f for f in plan if f["kind"] == "think"]
        assert len(think) == int(q.T_THINK * q.FPS), \
            "the thinking beat must sit between the last stop and the answer"
        assert not any(f["reveal"] for f in think), "the beat gives it away"
        assert plan[0]["kind"] == "intro" and plan[-1]["kind"] == "reveal"
        assert len(plan) - first == int(q.T_REVEAL * q.FPS)
    print("test_no_reveal_before_the_end PASS")


def _setup(name="Joe Ingles"):
    coords = oc.Coords()
    rings = oc.load_rings()
    player = next(p for p in DB if (p.get("display_name") or p["player"]) == name)
    r = q.route(player, coords)
    pts = [oc.project(c[1], c[0], oc.BW, oc.BH) for _, c in r]
    return rings, pts, [s for s, _ in r], q.clip_view(pts)


def test_pre_reveal_frames_cannot_contain_the_name():
    """Rendered proof: a pre-reveal frame is identical whoever the player is.

    The same frame is rendered for two different names and portraits. If
    anything identifying reached a pre-reveal frame, the two would differ.
    """
    rings, pts, stints, box = _setup()
    chrome = q.build_chrome()
    plan = q.frame_plan(pts)
    mid = plan[len(plan) // 3]
    assert not mid["reveal"]
    answer = q.build_reveal("Someone Else Entirely", None, stints, "1990–2001")
    a = q.render_frame(chrome, rings, pts, stints, mid, box)
    b = q.render_frame(chrome, rings, pts, stints, mid, box, reveal_im=answer)
    # the descriptor decides, not the caller, so passing the answer changes
    # nothing on a pre-reveal frame
    assert a.tobytes() == b.tobytes(), "identity leaked into a pre-reveal frame"
    assert b.tobytes() != answer.tobytes(), "the answer screen was substituted"
    print("test_pre_reveal_frames_cannot_contain_the_name PASS")


def test_no_years_before_the_reveal():
    """Years date a career and all but name the player, so none may appear.

    Rendered proof rather than a source scan: the same pre-reveal frames are
    drawn with the years mutated. If a year reached the pixels, they'd differ.
    """
    rings, pts, stints, box = _setup()
    chrome = q.build_chrome()
    import copy
    other = copy.deepcopy(stints)
    for st in other:
        st["years"] = "1911-1913"
    plan = q.frame_plan(pts)
    checked = 0
    for fr in plan:
        if fr["reveal"]:
            continue
        if fr["kind"] not in ("land", "intro"):
            continue
        a = q.render_frame(chrome, rings, pts, stints, fr, box)
        b = q.render_frame(chrome, rings, pts, other, fr, box)
        assert a.tobytes() == b.tobytes(), \
            f"a year reached a {fr['kind']} frame"
        checked += 1
        if checked >= 6:
            break
    assert checked, "no pre-reveal frames examined"
    # ...and the reveal is where a span may legitimately appear
    span = q._career_span(stints)
    assert span and "–" in span, span
    print(f"test_no_years_before_the_reveal PASS ({checked} frames)")


def test_reveal_lists_every_club_in_career_order():
    """The final screen is the payoff, so it has to carry the whole route.

    Rendered proof that the list is complete and legible: each club name is
    drawn on its own row, and a fourteen-stop career has to fit without the
    type collapsing to something nobody can read on a phone.
    """
    from PIL import ImageDraw

    for name in ("Joe Ingles", "Michael Beasley"):
        _rings, _pts, stints, _box = _setup(name)
        im = q.build_reveal(name, None, stints, q._career_span(stints))
        assert im.size == (q.W, q.H)
        # every row must have room for its own name at a readable size
        rows = len(stints)
        row = min(104.0, (q.H - 96 - 700) / rows)   # worst case: with a credit
        assert row >= 60, f"{name}: {rows} stops squeezed into {row:.0f}px rows"
        assert int(min(46, row * 0.46)) >= 28, "club names became unreadable"
        # and the names have to be the ones the route actually visited
        d = ImageDraw.Draw(im)
        for st in stints:
            club = (st.get("team") or "").strip()
            f = q._fit(d, club, "SemiBold", int(min(46, row * 0.46)), 880)
            assert f.size >= 20, f"{club} shrank to {f.size}px to fit"
    print("test_reveal_lists_every_club_in_career_order PASS")


def test_reveal_credits_a_photo_that_needs_one():
    """A CC BY / CC BY-SA photo has to carry its credit into the frame."""
    _rings, _pts, stints, _box = _setup()
    plain = q.build_reveal("Someone", None, stints, "2001–2010")
    credited = q.build_reveal("Someone", None, stints, "2001–2010",
                              credit="Photo: Jane Doe / CC BY-SA 4.0")
    assert plain.tobytes() != credited.tobytes(), "the credit never got drawn"
    print("test_reveal_credits_a_photo_that_needs_one PASS")


def test_countdown_runs_one_number_a_second_and_never_shows_zero():
    """The beat is the clock, so the clock has to be right.

    Each number holds for a second, the sequence is strictly decreasing, it
    starts at COUNTDOWN_FROM and ends at 1 -- zero is the reveal, not a frame.
    """
    from itertools import groupby

    plan = [f for f in q.frame_plan(_ring(9)) if f["kind"] == "think"]
    assert plan, "no thinking beat"
    seq = [q.countdown_number(f["left"]) for f in plan]
    runs = [(v, len(list(g))) for v, g in groupby(seq)]
    assert [v for v, _ in runs] == list(range(q.COUNTDOWN_FROM, 0, -1)), runs
    for value, frames in runs:
        assert abs(frames - q.FPS) <= 1, \
            f"{value} is on screen for {frames} frames, not about {q.FPS}"
    assert 0 not in seq, "zero got drawn instead of triggering the reveal"
    # and the reveal starts on the very next frame after the last 1
    whole = q.frame_plan(_ring(9))
    last_think = max(i for i, f in enumerate(whole) if f["kind"] == "think")
    assert whole[last_think + 1]["reveal"], "the countdown does not fire it"
    print(f"test_countdown_runs_one_number_a_second_and_never_shows_zero PASS "
          f"({runs})")


def test_countdown_is_visible_and_changes():
    """Rendered proof: consecutive numbers look different, and it is drawn."""
    rings, pts, stints, box = _setup()
    chrome = q.build_chrome()
    plan = [f for f in q.frame_plan(pts) if f["kind"] == "think"]
    seen = {}
    for fr in plan:
        n = q.countdown_number(fr["left"])
        seen.setdefault(n, q.render_frame(chrome, rings, pts, stints, fr, box))
    assert len(seen) == q.COUNTDOWN_FROM, sorted(seen)
    blobs = {n: im.tobytes() for n, im in seen.items()}
    assert len(set(blobs.values())) == len(blobs), "the number never changed"
    print("test_countdown_is_visible_and_changes PASS")


def test_the_prompt_is_centred_in_its_card():
    """"Who is it?" sits in the middle of the caption card, not off to a side."""
    from PIL import ImageDraw

    im = q._gradient((q.W, q.H), q.BG_TOP, q.BG_BOT).convert("RGB")
    q.draw_caption(im, landed=9, total=9, club="", place="", thinking=True)
    d = ImageDraw.Draw(im)
    x0, _y0, x1, _y1 = d.textbbox(
        ((q.MAP_X0 + q.MAP_X1) / 2, (q.MAP_BOTTOM + 44 + q.H - 60) / 2),
        "Who is it?", font=q._font("Bold", 96), anchor="mm")
    card_mid = (q.MAP_X0 + q.MAP_X1) / 2
    assert abs((x0 + x1) / 2 - card_mid) < 2, "not horizontally centred"
    gaps = (x0 - q.MAP_X0, q.MAP_X1 - x1)
    assert abs(gaps[0] - gaps[1]) < 4, f"uneven margins {gaps}"
    print("test_the_prompt_is_centred_in_its_card PASS")


def test_the_reveal_drops_the_disambiguator_but_the_key_keeps_it():
    """"Charles Jones (1957)" is a record key, not a name anyone would say.

    Display only. Three Charles Joneses share this dataset and the key is the
    only thing keeping their photos and their output files apart, so it has to
    survive untouched.
    """
    assert q.reveal_name("Charles Jones (1957)") == "Charles Jones"
    assert q.reveal_name("Chris Johnson (basketball, born 1990)") == "Chris Johnson"
    assert q.reveal_name("Joe Ingles") == "Joe Ingles"
    assert q.reveal_name("(1957)") == "(1957)", "never strip a name to nothing"

    _rings, _pts, stints, _box = _setup()
    keyed = q.build_reveal(q.reveal_name("Charles Jones (1957)"), None, stints, "")
    plain = q.build_reveal("Charles Jones", None, stints, "")
    assert keyed.tobytes() == plain.tobytes(), "the year still reached the frame"

    # the keys themselves stay distinct, in the data and in the filenames
    keys = [p.get("player") for p in DB
            if q.reveal_name(p.get("player") or "") == "Charles Jones"]
    assert len(keys) > 1, "expected several Charles Joneses to tell apart"
    assert len(set(q.slug(k) for k in keys)) == len(keys), \
        f"two of {keys} would write to the same file"
    print("test_the_reveal_drops_the_disambiguator_but_the_key_keeps_it PASS "
          f"({len(keys)} keys kept apart)")


def test_camera_follows_the_plane():
    """The plane must stay near the centre of frame while flying."""
    rings, pts, stints, box = _setup()
    off = []
    for leg in range(len(pts) - 1):
        for t in (0.15, 0.5, 0.85):
            (center, span), _h = q.leg_camera(pts, leg, t)
            x, y = q.to_band(q.box_from(center, span), center)
            # to_band returns coordinates inside the map band, which is inset
            # from the frame -- so the target is the band's centre, not W/2.
            off.append(abs(x - q.BAND_W / 2) + abs(y - q.BAND_H / 2))
    assert max(off) < 2.0, f"camera lost the plane (max offset {max(off):.1f}px)"
    print("test_camera_follows_the_plane PASS")


def test_camera_zooms_out_for_long_legs():
    """A crossing should pull back; a short hop should not."""
    hop = [(0.0, 0.0), (q.SPAN_CLOSE * 0.15, 0.0), (q.SPAN_CLOSE * 0.3, 0.0)]
    cross = [(0.0, 0.0), (oc.BW * 0.45, 0.0), (oc.BW * 0.5, 0.0)]
    (_c, span_near), _ = q.leg_camera(hop, 0, 0.5)
    (_c, span_far), _ = q.leg_camera(cross, 0, 0.5)
    (_c, span_start), _ = q.leg_camera(cross, 0, 0.0)
    assert span_far > span_near * 2, (span_near, span_far)
    assert span_start < span_far, "should be zoomed in at take-off"
    assert span_far <= q.SPAN_CRUISE_MAX
    print("test_camera_zooms_out_for_long_legs PASS")


def _ring(n: int) -> list:
    """n stops spread round a circle, for tests that only need a shape."""
    import math as _m
    r = oc.BW * 0.16
    return [(oc.BW / 2 + r * _m.cos(2 * _m.pi * i / n),
             oc.BH / 2 + r * _m.sin(2 * _m.pi * i / n)) for i in range(n)]


def test_camera_motion_is_smooth():
    """Measured frame by frame, not read off the source.

    Abruptness is a rate problem, so the whole plan is walked and the camera
    asked how far it moved between consecutive frames: how much it zoomed (as a
    proportion, since a fixed number of map units is a very different move when
    you are close), how far the view panned in screen pixels, and how far the
    plane turned. Each is capped, and so is the change in each -- a move that
    starts at full speed is exactly the snap this is guarding against.
    """
    import math as _m
    for name in ("Joe Ingles", "Metta World Peace", "Dominique Wilkins"):
        rings, pts, stints, box = _setup(name)
        plan = q.frame_plan(pts)
        cam = [q.frame_camera(pts, fr, box) for fr in plan]
        span = [c[0][2] - c[0][0] for c in cam]
        mid = [((c[0][0] + c[0][2]) / 2, (c[0][1] + c[0][3]) / 2) for c in cam]
        head = [c[1][1] for c in cam]
        dz = [abs(_m.log(span[i + 1] / span[i])) for i in range(len(span) - 1)]
        dp = [_m.hypot(mid[i + 1][0] - mid[i][0], mid[i + 1][1] - mid[i][1])
              * q.BAND_W / span[i] for i in range(len(span) - 1)]
        dh = [abs((head[i + 1] - head[i] + 180) % 360 - 180)
              for i in range(len(head) - 1)]
        jz = [abs(dz[i + 1] - dz[i]) for i in range(len(dz) - 1)]
        jp = [abs(dp[i + 1] - dp[i]) for i in range(len(dp) - 1)]
        assert max(dz) < 0.22, f"{name}: zoom snaps ({max(dz) * 100:.1f}%/frame)"
        assert max(dp) < 90, f"{name}: pan snaps ({max(dp):.0f}px/frame)"
        assert max(dh) < 14, f"{name}: plane spins ({max(dh):.0f}deg/frame)"
        assert max(jz) < 0.05, f"{name}: zoom starts abruptly ({max(jz):.3f})"
        assert max(jp) < 30, f"{name}: pan starts abruptly ({max(jp):.0f}px)"
        # and the first frame of every leg has to continue the last one
        for i, fr in enumerate(plan[1:], 1):
            if fr["kind"] != plan[i - 1]["kind"]:
                assert dz[i - 1] < 0.22 and dp[i - 1] < 90, \
                    f"{name}: hard cut at the {plan[i - 1]['kind']} boundary"
    print("test_camera_motion_is_smooth PASS")


def test_plane_points_where_it_is_going():
    """The rotation offset is checked against pixels, not taken on trust.

    A wrong offset is invisible in the source and obvious on screen, so the
    sprite is rotated east and west and the ink asked which way it leans: flying
    east, the nose has to be right of the tail.
    """
    from PIL import Image, ImageDraw

    def nose_bias(heading):
        im = Image.new("RGB", (400, 400), (0, 0, 0))
        q.draw_plane(im, (200, 200), heading, size=92)
        px = im.load()
        xs = [x for y in range(400) for x in range(400) if sum(px[x, y]) > 90]
        assert xs, "the plane drew nothing"
        # the nose is the narrow end: compare ink mass either side of centre
        return sum(1 for x in xs if x > 200) - sum(1 for x in xs if x < 200)

    east, west = nose_bias(0.0), nose_bias(180.0)
    assert east > 0 > west, f"plane faces the wrong way (E {east}, W {west})"
    print("test_plane_points_where_it_is_going PASS")


def test_a_cutout_stands_on_the_card_and_a_photograph_gets_a_ring():
    """Two sources, two treatments, and the reason is transparency.

    An official headshot is a subject on nothing, so it is rendered as a
    cut-out standing on the rule: no photo box, nothing behind it but the card.
    A Commons photograph is opaque to its own edges, so the same treatment
    would paste a rectangle of somebody's back garden onto the card -- it gets
    a circular crop and a gradient ring instead.
    """
    from PIL import Image, ImageDraw

    cut = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(cut).ellipse([69, 51, 190, 204], fill=(210, 120, 60, 255))
    photo = Image.new("RGBA", (512, 512), (210, 120, 60, 255))
    assert q._is_cutout(cut), "a transparent cut-out was read as a photograph"
    assert not q._is_cutout(photo), "an opaque photo was read as a cut-out"
    assert not q._is_cutout(None)

    _rings, _pts, stints, _box = _setup()
    im_cut = q.build_reveal("Someone", cut, stints, "2001–2010")
    im_pic = q.build_reveal("Someone", photo, stints, "2001–2010")

    def subject_rows(im, x):
        px = im.load()
        return [y for y in range(q.H)
                if abs(px[x, y][0] - 210) < 45 and abs(px[x, y][2] - 60) < 45]

    # the cut-out reaches the rule it stands on, and its own bottom edge is
    # faded rather than ending on a hard line
    col = q.REVEAL_X0 + 30 + q.PORTRAIT_W // 2
    rows = subject_rows(im_cut, col)
    assert rows, "the cut-out never got drawn"
    rule_y = (178 + 30) + 252 + 46 - 30
    assert min(rows) > 178, "the cut-out escaped the top of the panel"
    assert max(rows) - min(rows) > 180, "the head is not filling the height"
    # It reaches the rule, but the last rows are faded into the card rather
    # than ending on a hard edge -- so the full-strength colour stops short
    # while SOMETHING is still there right down at the rule.
    assert max(rows) >= rule_y - 60, \
        f"the cut-out floats {rule_y - max(rows)}px above the rule"
    near = im_cut.load()[col, rule_y - 6]
    assert near != q.CARD, "nothing reaches the rule at all"
    assert abs(near[0] - 210) > 8, "the bottom edge was not faded"

    # the photograph is round: its widest row is well inside the column
    rows_pic = subject_rows(im_pic, col)
    assert rows_pic, "the photo never got drawn"
    px = im_pic.load()
    mid = (min(rows_pic) + max(rows_pic)) // 2
    span_mid = [x for x in range(q.REVEAL_X0, q.REVEAL_X0 + 320)
                if abs(px[x, mid][0] - 210) < 45]
    span_top = [x for x in range(q.REVEAL_X0, q.REVEAL_X0 + 320)
                if abs(px[x, min(rows_pic) + 6][0] - 210) < 45]
    assert len(span_mid) > len(span_top) + 20, \
        "the photo is square, not circular"
    print("test_a_cutout_stands_on_the_card_and_a_photograph_gets_a_ring PASS")


def test_no_photo_means_no_portrait_slot():
    """A generic silhouette says nothing; the name is worth the room.

    Measured as the reader would see it: where the name starts. With a picture
    it begins a portrait's width in; without one it begins at the margin.
    """
    from PIL import Image, ImageDraw

    cut = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(cut).ellipse([69, 51, 190, 204], fill=(210, 120, 60, 255))
    _rings, _pts, stints, _box = _setup()

    def name_starts_at(im, y):
        px = im.load()
        ink = [x for x in range(q.REVEAL_X0, q.REVEAL_X1)
               for yy in range(y - 40, y + 40)
               if px[x, yy] == q.TEXT]
        assert ink, "the name never got drawn"
        return min(ink)

    bare = name_starts_at(q.build_reveal("Someone", None, stints, ""), 309)
    shot = name_starts_at(q.build_reveal("Someone", cut, stints, ""), 282)
    assert bare < q.REVEAL_X0 + 80, f"the name starts {bare}px in"
    assert shot >= q.REVEAL_X0 + 30 + q.PORTRAIT_W, \
        f"the name overlaps the portrait ({shot}px)"
    assert shot - bare > 200, "the name did not take the vacated room"
    print(f"test_no_photo_means_no_portrait_slot PASS "
          f"(name at x={bare} without a photo, x={shot} with one)")


def test_the_longest_career_in_the_pool_fits_on_screen():
    """Every stop visible, for every player a batch can pick.

    Not Dorell Wright alone: the whole eligible pool, at its longest. The rows
    divide the room so the list always fits by construction -- what can fail is
    the TYPE, if a route ever gets long enough to squeeze club names below the
    floor. That is the point at which this needs two columns, and this is what
    will say so.
    """
    longest = 0
    for p in q.candidates(DB, COORDS):
        longest = max(longest, len(q.route(p, COORDS)))
    assert longest >= 12, f"pool tops out at {longest} stops -- is this right?"

    for n in range(1, longest + 1):
        for foot in (0, 40):
            avail = q.H - q.BOTTOM_SAFE - foot - q.LIST_TOP
            row = min(q.ROW_MAX, avail / n)
            size = int(min(q.NAME_MAX, row * 0.46))
            bottom = q.LIST_TOP + row * n
            assert bottom <= q.H - q.BOTTOM_SAFE - foot + 0.5, \
                f"{n} stops run {bottom - (q.H - q.BOTTOM_SAFE)}px past the line"
            assert bottom <= q.H - 100, f"{n} stops reach the frame edge"
            assert size >= q.NAME_MIN, \
                f"{n} stops squeeze the club names to {size}px"

    # and rendered, at the longest: the last club is drawn where it is claimed
    worst = max(q.candidates(DB, COORDS), key=lambda p: len(q.route(p, COORDS)))
    stints = [s for s, _ in q.route(worst, COORDS)]
    im = q.build_reveal("Someone", None, stints, "2001–2010")
    px = im.load()
    rows = [y for y in range(q.LIST_TOP, q.H)
            if any(px[x, y] == q.TEXT for x in range(140, q.REVEAL_X1))]
    assert rows, "the list never got drawn"
    assert max(rows) <= q.H - q.BOTTOM_SAFE, \
        f"club names reach y={max(rows)} of {q.H}"
    print(f"test_the_longest_career_in_the_pool_fits_on_screen PASS "
          f"({longest} stops, last row ends at y={max(rows)})")


def test_clip_length_lands_in_the_target_window():
    for n in range(6, 15):
        total = q.timings(_ring(n))["total"]
        assert 15.0 <= total <= 27.0, f"{n} stops -> {total:.1f}s, outside 15-27s"
    print("test_clip_length_lands_in_the_target_window PASS")


def test_selection_rule():
    coords = oc.Coords()
    cand = q.candidates(DB, coords)
    assert 600 < len(cand) < 1200, f"selection drifted: {len(cand)}"
    for p in cand[:50]:
        r = q.route(p, coords)
        assert 6 <= len(r) <= 14, (p["player"], len(r))
        countries = {(s.get("country") or "").strip() for s, _ in r} - {""}
        assert len(countries) >= 3, (p["player"], countries)
        assert (q.nba_years(p) >= 3 or q.first_round(p) or p.get("all_star"))
    # the ranking must put recognisable names first
    top = [p.get("display_name") or p["player"] for p in cand[:12]]
    assert any(n in top for n in ("Danilo Gallinari", "Metta World Peace",
                                  "DeMarcus Cousins", "Tracy McGrady")), top
    print(f"test_selection_rule PASS ({len(cand)} eligible)")


def test_route_collapses_repeat_clubs():
    coords = oc.Coords()
    fake = {"career_history": [
        {"team": "A", "city": "Denver", "state": "Colorado", "country": "USA",
         "years": "2001"},
        {"team": "A", "city": "Denver", "state": "Colorado", "country": "USA",
         "years": "2002"},
        {"team": "B", "city": "Madrid", "state": "", "country": "Spain",
         "years": "2003"},
    ]}
    assert len(q.route(fake, coords)) == 2, "consecutive repeats must collapse"
    print("test_route_collapses_repeat_clubs PASS")


def test_ffmpeg_is_available():
    import subprocess
    exe = q.ffmpeg_exe()
    out = subprocess.run([exe, "-hide_banner", "-encoders"],
                         capture_output=True, text=True)
    assert "libx264" in out.stdout, "no h264 encoder available"
    print("test_ffmpeg_is_available PASS")


def test_output_is_not_committed():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "out/" in gitignore, "clips would be committed to the repo"
    assert str(q.OUT_DIR).endswith("out/clips")
    print("test_output_is_not_committed PASS")


if __name__ == "__main__":
    test_no_reveal_before_the_end()
    test_pre_reveal_frames_cannot_contain_the_name()
    test_no_years_before_the_reveal()
    test_reveal_lists_every_club_in_career_order()
    test_reveal_credits_a_photo_that_needs_one()
    test_countdown_runs_one_number_a_second_and_never_shows_zero()
    test_countdown_is_visible_and_changes()
    test_the_prompt_is_centred_in_its_card()
    test_the_reveal_drops_the_disambiguator_but_the_key_keeps_it()
    test_camera_follows_the_plane()
    test_camera_zooms_out_for_long_legs()
    test_camera_motion_is_smooth()
    test_plane_points_where_it_is_going()
    test_a_cutout_stands_on_the_card_and_a_photograph_gets_a_ring()
    test_no_photo_means_no_portrait_slot()
    test_the_longest_career_in_the_pool_fits_on_screen()
    test_clip_length_lands_in_the_target_window()
    test_selection_rule()
    test_route_collapses_repeat_clubs()
    test_ffmpeg_is_available()
    test_output_is_not_committed()
    print("\nall quiz-clip tests PASS")

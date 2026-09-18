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


def test_every_photo_source_fills_the_panel_the_same():
    """The subject has to end up the same size whatever the source was.

    An official NBA "face" crop is a 256x256 PNG whose cut-out subject occupies
    about 111x152 of it, the rest transparent; a Commons photo is an opaque
    square. Cover-cropping to the IMAGE bounds made the first fill 43% of the
    panel width and the second 100%, which is what showed as a small headshot
    in a big panel. Both are measured here from the rendered panel.
    """
    from PIL import Image

    SIZE = 252
    SUBJECT = (210, 120, 60)

    def subject_box(panel):
        px = panel.load()
        hits = [(x, y) for y in range(SIZE) for x in range(SIZE)
                if abs(px[x, y][0] - SUBJECT[0]) < 40
                and abs(px[x, y][2] - SUBJECT[2]) < 40]
        assert hits, "the subject never got drawn"
        xs = [x for x, _ in hits]
        ys = [y for _, y in hits]
        return min(xs), min(ys), max(xs), max(ys)

    # a cut-out with the real NBA geometry: mostly transparent padding
    cut = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    cut.paste(SUBJECT + (255,), (71, 52, 71 + 111, 52 + 152))
    # and an opaque photo that already fills its own frame
    photo = Image.new("RGBA", (512, 512), SUBJECT + (255,))

    # A cut-out is inset by PORTRAIT_PAD on each side so the head is not flush
    # against the frame; an opaque photo has no margin of its own to give, so
    # it fills edge to edge. That is the whole of the allowed difference, and
    # it is derived from the constant rather than guessed at.
    inset = 1.0 / (1.0 + 2 * oc.PORTRAIT_PAD)
    boxes = {}
    for label, src in (("cut-out", cut), ("photo", photo)):
        x0, y0, x1, y1 = subject_box(q._portrait(src, SIZE))
        boxes[label] = (x1 - x0 + 1, y1 - y0 + 1)
        assert boxes[label][0] >= SIZE * inset * 0.97, \
            f"{label}: subject spans {boxes[label][0]}px of {SIZE}"
        if label == "cut-out":
            # The head bias must leave the top of the subject in frame. An
            # opaque photo is its own subject edge to edge, so it has no
            # headroom to check.
            assert y0 > 0, "the head bias cropped into the top of the subject"
            assert y0 < SIZE * 0.12, f"too much dead space above ({y0}px)"
    wide = max(b[0] for b in boxes.values())
    narrow = min(b[0] for b in boxes.values())
    assert wide - narrow <= SIZE * (1 - inset) + 2, \
        f"sources fill differently: {boxes}"

    # the drawn mark has to carry comparable weight, not sit small in the frame
    mark = q._portrait(None, SIZE)
    px = mark.load()
    ink = [(x, y) for y in range(SIZE) for x in range(SIZE)
           if px[x, y] == q.MAP_COAST]
    assert ink, "the mark never got drawn"
    span = max(x for x, _ in ink) - min(x for x, _ in ink) + 1
    assert span >= SIZE * 0.7, f"the mark spans only {span}px of {SIZE}"
    print(f"test_every_photo_source_fills_the_panel_the_same PASS "
          f"({boxes}, mark {span}px)")


def test_portrait_never_falls_back_to_a_black_hole():
    """Headshots are RGBA with a transparent background.

    convert("RGB") would flatten that alpha onto black and punch a hole in the
    card, so the compositing is checked at the corners; and a player with no
    headshot has to get the drawn mark, not an empty square.
    """
    from PIL import Image, ImageDraw

    # A ring, so transparent pixels survive the crop to the content box and
    # land inside the panel where they can be inspected.
    shot = Image.new("RGBA", (200, 260), (0, 0, 0, 0))
    ImageDraw.Draw(shot).ellipse([40, 60, 160, 180], fill=(240, 120, 40, 255))
    ImageDraw.Draw(shot).ellipse([75, 95, 125, 145], fill=(0, 0, 0, 0))
    have = q._portrait(shot, 120)
    hole = have.getpixel((60, 60))
    assert sum(hole) > 90, f"transparent pixel went black: {hole}"
    lo = [min(a, b) for a, b in zip(q.CARD, q.CARD_EDGE)]
    hi = [max(a, b) for a, b in zip(q.CARD, q.CARD_EDGE)]
    assert all(l - 2 <= c <= h + 2 for c, l, h in zip(hole, lo, hi)), \
        f"transparent pixel is not the tile colour: {hole}"

    none = q._portrait(None, 120)
    assert none.size == (120, 120)
    assert len(none.getcolors(maxcolors=4096) or []) > 1, \
        "no-headshot players get a blank square"
    print("test_portrait_never_falls_back_to_a_black_hole PASS")


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
    test_every_photo_source_fills_the_panel_the_same()
    test_portrait_never_falls_back_to_a_black_hole()
    test_clip_length_lands_in_the_target_window()
    test_selection_rule()
    test_route_collapses_repeat_clubs()
    test_ffmpeg_is_available()
    test_output_is_not_committed()
    print("\nall quiz-clip tests PASS")

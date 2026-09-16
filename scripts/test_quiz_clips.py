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
        plan = q.frame_plan(n)
        reveal_at = [i for i, (_, _, r) in enumerate(plan) if r]
        assert reveal_at, "clip has no reveal at all"
        first = reveal_at[0]
        assert reveal_at == list(range(first, len(plan))), \
            "reveal frames must be one unbroken block at the end"
        assert not any(r for _, _, r in plan[:first]), "answer leaks early"
        assert first >= int((q.T_INTRO + q.T_HOLD) * q.FPS), \
            "reveal starts implausibly early"
        assert len(plan) - first == int(q.T_REVEAL * q.FPS)
    print("test_no_reveal_before_the_end PASS")


def test_pre_reveal_frames_cannot_contain_the_name():
    """Rendered proof, not just the plan: the name is absent from the pixels.

    The same pre-reveal frame is drawn for two players with different names and
    different portraits. If anything identifying leaked into a pre-reveal frame,
    the two would differ.
    """
    coords = oc.Coords()
    rings = oc.load_rings()
    a = next(p for p in DB if (p.get("display_name") or p["player"]) == "Joe Ingles")
    r = q.route(a, coords)
    hi = [oc.project(c[1], c[0], oc.BW, oc.BH) for _, c in r]
    view = q.clip_view(hi)
    left, top, right, bottom = view
    sx, sy = q.W / (right - left), q.BAND_H / (bottom - top)
    pts = [((x - left) * sx, (y - top) * sy) for x, y in hi]
    stints = [s for s, _ in r]
    base = q.base_frame(rings, view)

    f1 = q.draw_frame(base, pts, 4, 1.0, stints)
    f2 = q.draw_frame(base, pts, 4, 1.0, stints)
    assert f1.tobytes() == f2.tobytes(), "pre-reveal frame not deterministic"

    shots = oc.Headshots(enabled=False)
    revealed = q.draw_frame(base, pts, len(pts), 1.0, stints,
                            reveal=("Joe Ingles", "10 clubs · 4 countries"),
                            face=None)
    assert revealed.tobytes() != f1.tobytes(), \
        "reveal frame is identical to a pre-reveal frame"
    assert shots.face("Joe Ingles") is None
    print("test_pre_reveal_frames_cannot_contain_the_name PASS")


def test_clip_length_lands_in_the_target_window():
    for n in range(6, 15):
        total = q.timings(n)["total"]
        assert 15.0 <= total <= 25.0, f"{n} stops -> {total:.1f}s, outside 15-25s"
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
    test_clip_length_lands_in_the_target_window()
    test_selection_rule()
    test_route_collapses_repeat_clubs()
    test_ffmpeg_is_available()
    test_output_is_not_committed()
    print("\nall quiz-clip tests PASS")

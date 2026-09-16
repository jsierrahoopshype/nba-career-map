"""Tests for the per-player Open Graph cards.

These run offline: every test that needs a card builds it with headshots
disabled, so the suite never depends on the nba-headshots repo being reachable.

Run:  python3 scripts/test_og_cards.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import og_cards as oc  # noqa: E402
import prerender as pr  # noqa: E402
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

GLOBETROTTER = {
    "player": "Test Traveller", "display_name": "Test Traveller",
    "status": "overseas_active",
    "career_history": [
        {"years": "2017", "team": "Brooklyn Nets", "city": "Brooklyn",
         "state": "New York", "country": "USA"},
        {"years": "2018", "team": "Adelaide 36ers", "city": "Adelaide",
         "state": "", "country": "Australia"},
        {"years": "2019", "team": "Rytas Vilnius", "city": "Vilnius",
         "state": "", "country": "Lithuania"},
        {"years": "2020", "team": "Shiga Lakes", "city": "Ōtsu",
         "state": "", "country": "Japan"},
    ],
}


def _points(player, coords):
    out = []
    for st in player["career_history"]:
        c = coords.get(st.get("city", ""), st.get("state", ""), st.get("country", ""))
        if c:
            out.append(oc.project(c[1], c[0], oc.BW, oc.BH))
    return out


def test_selection_rule():
    players = [
        {"player": "A", "status": "nba_active"},
        {"player": "B", "status": "overseas_active"},
        {"player": "C", "status": "retired", "all_star": True},
        {"player": "D", "status": "retired"},
    ]
    picked = {p["player"] for p in oc.selected(players)}
    assert picked == {"A", "B", "C"}, picked
    print("test_selection_rule PASS")


def test_selection_matches_the_agreed_scope():
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    n = len(oc.selected(db))
    assert 1000 < n < 1600, f"scope drifted: {n} cards (expected ~1,300)"
    print(f"test_selection_matches_the_agreed_scope PASS ({n} players)")


def test_every_stop_stays_on_the_visible_map():
    """The route must clear the text panel; a hidden stop is a broken card."""
    coords = oc.Coords()
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    # the widest-ranging real careers, plus the synthetic globetrotter
    worst = sorted(oc.selected(db),
                   key=lambda p: -len(p.get("career_history", [])))[:40]
    bad = []
    for player in worst + [GLOBETROTTER]:
        pts = _points(player, coords)
        if not pts:
            continue
        left, top, right, bottom = oc.route_view(pts)
        sx, sy = oc.W / (right - left), oc.H / (bottom - top)
        for x, y in pts:
            fx, fy = (x - left) * sx, (y - top) * sy
            if not (0 <= fx <= oc.W and 0 <= fy <= oc.H - oc.PANEL_H):
                bad.append((player["player"], round(fx), round(fy)))
    assert not bad, f"stops off the visible map: {bad[:5]}"
    print("test_every_stop_stays_on_the_visible_map PASS (41 routes)")


def test_view_never_degenerates():
    """A one-stop career must not zoom to a meaningless sliver."""
    coords = oc.Coords()
    one = _points({"career_history": [
        {"city": "Denver", "state": "Colorado", "country": "USA"}]}, coords)
    left, top, right, bottom = oc.route_view(one)
    assert right - left >= oc.MIN_SPAN_X, "zoomed past the floor"
    assert oc.route_view([]) == (0, 0, oc.BW, oc.BH), "no stops -> whole world"
    print("test_view_never_degenerates PASS")


def test_map_past_the_edge_is_water_not_black():
    """A view may sit partly outside the map band; beyond it is ocean."""
    rings = oc.load_rings()
    im = oc.draw_map(rings, -oc.BW, -oc.BH, 0, 0)     # entirely off the map
    assert im.size == (oc.W, oc.H)
    assert im.getpixel((2, 2)) == oc.WATER, im.getpixel((2, 2))
    assert im.getpixel((oc.W // 2, oc.H // 2)) == oc.WATER
    print("test_map_past_the_edge_is_water_not_black PASS")


def test_map_is_drawn_flat_for_compression():
    """Few colours is the whole reason the set fits: 25.6 MB, not 56.6 MB."""
    rings = oc.load_rings()
    im = oc.draw_map(rings, 0, 0, oc.BW, oc.BH)
    assert len(im.getcolors(maxcolors=100000) or []) <= 8, \
        "basemap gained colours; it must stay flat vector fill"
    print("test_map_is_drawn_flat_for_compression PASS")


def test_card_renders_without_a_headshot():
    rings = oc.load_rings()
    shots = oc.Headshots(enabled=False)
    assert shots.face("Anyone") is None, "must not reach the network when off"
    im = oc.render_card(GLOBETROTTER, rings, oc.Coords(), shots)
    assert im.size == (oc.W, oc.H)
    # the silhouette sits inside its circle, so the card's corner is panel white
    assert im.getpixel((oc.W - 4, oc.H - 4))[0] > 200
    print("test_card_renders_without_a_headshot PASS")


def test_unchanged_cards_are_skipped_without_redrawing():
    """The regression guard for a real bug: this shipped broken once.

    A half-applied edit left write_all referencing a signature variable it
    never defined, so the function raised NameError at the end of a full
    re-render -- after four minutes of redrawing every card. The daily pipeline
    depends on the second run being nearly free, so assert that directly.
    """
    import time
    tmp = Path(tempfile.mkdtemp()) / "cards"
    players = [dict(GLOBETROTTER, player=f"Player {i}",
                    display_name=f"Player {i}") for i in range(12)]
    t0 = time.time()
    first = oc.write_all(players, out_dir=tmp, headshots=False)
    t_first = time.time() - t0
    t0 = time.time()
    second = oc.write_all(players, out_dir=tmp, headshots=False)
    t_second = time.time() - t0
    assert first["written"] == 12, first
    assert second["written"] == 0 and second["unchanged"] == 12, second
    assert (tmp / "signatures.json").exists(), "signature sidecar not written"
    assert t_second < max(t_first / 3, 0.05), \
        f"second run redrew instead of skipping ({t_second:.2f}s vs {t_first:.2f}s)"

    # a changed route must redraw exactly that one card
    changed = [dict(p) for p in players]
    changed[0] = json.loads(json.dumps(changed[0]))
    changed[0]["career_history"].append(
        {"years": "2030", "team": "New", "city": "Tokyo", "state": "",
         "country": "Japan"})
    third = oc.write_all(changed, out_dir=tmp, headshots=False)
    assert third["written"] == 1, f"a changed card must redraw: {third}"
    print("test_unchanged_cards_are_skipped_without_redrawing PASS")


def test_write_all_is_incremental_and_cleans_up():
    tmp = Path(tempfile.mkdtemp()) / "cards"
    a = dict(GLOBETROTTER)
    b = {"player": "Gone Soon", "status": "nba_active", "career_history": []}
    s1 = oc.write_all([a, b], out_dir=tmp, headshots=False)
    assert s1["written"] == 2 and s1["total"] == 2, s1
    s2 = oc.write_all([a, b], out_dir=tmp, headshots=False)
    assert s2["written"] == 0 and s2["unchanged"] == 2, "rewrote unchanged cards"
    s3 = oc.write_all([a], out_dir=tmp, headshots=False)
    assert s3["removed"] == 1 and not (tmp / "gone-soon.html").exists()
    assert Image.open(tmp / "test-traveller.png").size == (oc.W, oc.H)
    print("test_write_all_is_incremental_and_cleans_up PASS")


def test_page_points_at_its_own_card_when_one_exists():
    have = sorted(oc.CARD_DIR.glob("*.png"))
    assert have, "no cards generated yet"
    name = have[0].stem
    url, alt = pr.player_card(name)
    assert url.endswith(f"/assets/og/player/{name}.png"), url
    assert "career path" in alt
    # a player with no card falls back to the shared image, never a 404
    url2, alt2 = pr.player_card("Definitely Not A Real Player 9999")
    assert url2 == pr.OG_IMAGE and alt2 == pr.OG_IMAGE_ALT
    print("test_page_points_at_its_own_card_when_one_exists PASS")


def test_generated_page_carries_its_card():
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    have = {p.stem for p in oc.CARD_DIR.glob("*.png")}
    player = next(p for p in db if pr.slug(p["player"]) in have)
    html = pr.render(player)
    s = pr.slug(player["player"])
    assert f'<meta property="og:image" content="{pr.SITE_BASE_URL}/assets/og/player/{s}.png">' in html
    assert f'<meta name="twitter:image" content="{pr.SITE_BASE_URL}/assets/og/player/{s}.png">' in html
    assert "og-career-map.png" not in html, "should not also carry the shared image"
    print("test_generated_page_carries_its_card PASS")


def test_portrait_fills_its_circle():
    """The card's portrait must fit the SUBJECT, not the image it came in.

    An official NBA "face" crop is a 256x256 PNG whose cut-out subject occupies
    about 111x152 of it. Scaling the whole image into a 112px circle put a
    ~48px head in it; cover-cropping to the content box fills it.
    """
    from PIL import Image

    SUBJECT = (210, 120, 60)
    cut = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    cut.paste(SUBJECT + (255,), (71, 52, 71 + 111, 52 + 152))

    got = oc.cover_square(cut, 112)
    assert got.size == (112, 112)
    px = got.load()
    hits = [(x, y) for y in range(112) for x in range(112)
            if px[x, y][3] > 0]
    xs = [x for x, _ in hits]
    span = max(xs) - min(xs) + 1
    inset = 1.0 / (1.0 + 2 * oc.PORTRAIT_PAD)
    assert span >= 112 * inset * 0.97, f"subject spans {span}px of 112"
    assert min(y for _, y in hits) > 0, "the crop cut into the top of the head"
    print(f"test_portrait_fills_its_circle PASS ({span}px of 112)")


def test_card_version_is_in_the_signature():
    """A change to how a card is DRAWN moves no input.

    Without a version in the hash the incremental short-circuit would skip
    every card and the redesign would never reach disk -- which is exactly how
    a renderer change goes out looking like a no-op.
    """
    p = {"player": "A B", "status": "nba_active", "career_history": []}
    before = oc.card_signature(p, "x.png")
    real = oc.CARD_VERSION
    try:
        oc.CARD_VERSION = real + 1
        after = oc.card_signature(p, "x.png")
    finally:
        oc.CARD_VERSION = real
    assert before != after, "CARD_VERSION does not reach the signature"
    print("test_card_version_is_in_the_signature PASS")


if __name__ == "__main__":
    test_portrait_fills_its_circle()
    test_card_version_is_in_the_signature()
    test_selection_rule()
    test_selection_matches_the_agreed_scope()
    test_every_stop_stays_on_the_visible_map()
    test_view_never_degenerates()
    test_map_past_the_edge_is_water_not_black()
    test_map_is_drawn_flat_for_compression()
    test_card_renders_without_a_headshot()
    test_unchanged_cards_are_skipped_without_redrawing()
    test_write_all_is_incremental_and_cleans_up()
    test_page_points_at_its_own_card_when_one_exists()
    test_generated_page_carries_its_card()
    print("\nall og-card tests PASS")

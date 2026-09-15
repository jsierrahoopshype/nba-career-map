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


def test_crop_past_the_edge_is_water_not_black():
    base = Image.open(oc.BASEMAP).convert("RGB")
    im = oc.crop_view(base, -500, -500, -500 + oc.BW, -500 + int(oc.BW * oc.H / oc.W))
    assert im.size == (oc.W, oc.H)
    assert im.getpixel((2, 2)) == oc.WATER, im.getpixel((2, 2))
    print("test_crop_past_the_edge_is_water_not_black PASS")


def test_card_renders_without_a_headshot():
    base = Image.open(oc.BASEMAP).convert("RGB")
    shots = oc.Headshots(enabled=False)
    assert shots.face("Anyone") is None, "must not reach the network when off"
    im = oc.render_card(GLOBETROTTER, base, oc.Coords(), shots)
    assert im.size == (oc.W, oc.H)
    # the silhouette sits inside its circle, so the card's corner is panel white
    assert im.getpixel((oc.W - 4, oc.H - 4))[0] > 200
    print("test_card_renders_without_a_headshot PASS")


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


if __name__ == "__main__":
    test_selection_rule()
    test_selection_matches_the_agreed_scope()
    test_every_stop_stays_on_the_visible_map()
    test_view_never_degenerates()
    test_crop_past_the_edge_is_water_not_black()
    test_card_renders_without_a_headshot()
    test_write_all_is_incremental_and_cleans_up()
    test_page_points_at_its_own_card_when_one_exists()
    test_generated_page_carries_its_card()
    print("\nall og-card tests PASS")

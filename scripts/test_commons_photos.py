"""Tests for the Commons photo gate.

The whole point of this module is saying no, so that is what gets tested: real
extmetadata shapes from Commons, and the ones that must be refused. A false
positive here is a licensing problem on HoopsHype's accounts, not a rendering
bug, so the gate is default-deny and the test asserts that directly.

Run:  python3 scripts/test_commons_photos.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import commons_photos as cp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _meta(**kv):
    return {k: {"value": v} for k, v in kv.items()}


USABLE = [
    ("cc-by-sa-4.0", "cc-by-sa", True),
    ("cc-by-sa-3.0", "cc-by-sa", True),
    ("cc-by-sa-3.0-migrated", "cc-by-sa", True),
    ("cc-by-2.0", "cc-by", True),
    ("cc-by-4.0", "cc-by", True),
    ("cc0", "public-domain", False),
    ("CC0-1.0", "public-domain", False),
    ("pd", "public-domain", False),
    ("pd-usgov", "public-domain", False),
    ("Public Domain", "public-domain", False),
]

REFUSED = [
    "",
    "fair use",
    "non-free",
    "cc-by-nc-2.0",
    "cc-by-nc-sa-4.0",
    "cc-by-nd-3.0",
    "no license specified",
    "attribution",
    "fal",
    "gfdl",
    "copyrighted free use",
    "cc by sa",
    "pdq",
]


def test_allowlist_accepts_the_four_free_families():
    for code, family, needs in USABLE:
        got, req, _why = cp.classify_license(_meta(License=code))
        assert got == family, f"{code} -> {got}, expected {family}"
        assert req is needs, f"{code} attribution flag wrong"
    print(f"test_allowlist_accepts_the_four_free_families PASS "
          f"({len(USABLE)} codes)")


def test_everything_else_is_refused():
    """Default deny: anything unrecognised is a no, never a maybe."""
    for code in REFUSED:
        family, _req, why = cp.classify_license(_meta(License=code))
        assert family is None, f"{code!r} was accepted as {family}"
        assert why, "a refusal has to say why"
    # a missing key, not just an empty one
    assert cp.classify_license({})[0] is None
    assert cp.classify_license({"License": {}})[0] is None
    print(f"test_everything_else_is_refused PASS ({len(REFUSED)} codes)")


def test_nc_and_nd_are_never_read_as_cc_by():
    """The trap: cc-by-nc-2.0 starts with the same nine characters as cc-by-."""
    for code in ("cc-by-nc-2.0", "cc-by-nc-sa-4.0", "cc-by-nd-4.0",
                 "cc-by-sa-nc-3.0", "cc-by-3.0-nc", "cc-by-sa-4.0-nd",
                 "cc-by-2.0-sampling", "cc-by-2.5-devnations"):
        assert cp.classify_license(_meta(License=code))[0] is None, code
    print("test_nc_and_nd_are_never_read_as_cc_by PASS")


def test_attribution_line_survives_the_html():
    """Artist arrives as an HTML fragment, and a credit line is not HTML."""
    rec = {
        "artist": cp._plain('<a href="//commons.wikimedia.org/wiki/User:Foo" '
                            'title="User:Foo">Jane&nbsp;Doe</a>'),
        "license_name": "CC BY-SA 4.0",
        "license": "cc-by-sa",
    }
    line = cp.attribution_line(rec)
    assert "<" not in line and ">" not in line and "&nbsp;" not in line, line
    assert line == "Jane Doe / CC BY-SA 4.0", line
    assert cp.attribution_line({"license": "cc-by"}) \
        .startswith("Unknown author"), "a missing author still gets a line"
    print("test_attribution_line_survives_the_html PASS")


def test_end_to_end_with_a_stubbed_api():
    """The whole pipe, with the network replaced by recorded response shapes.

    Covers the two rejects that matter and cannot be seen from classify_license
    alone: an article that is not about basketball, and a portrait that exists
    on en.wikipedia but not on Commons -- which is what a fair-use upload looks
    like from here.
    """
    from PIL import Image

    pages = {
        "Free Player": dict(title="Free Player",
                            pageimage="Free_Player_2011.jpg",
                            categories=[{"title": "Category: "
                                         "American basketball players"}]),
        "Fairuse Player": dict(title="Fairuse Player", pageimage="Fair.jpg",
                               categories=[{"title": "Category: Basketball"}]),
        "Nc Player": dict(title="Nc Player", pageimage="Nc.jpg",
                          categories=[{"title": "Category: Basketball"}]),
        "Some Cyclist": dict(title="Some Cyclist", pageimage="Bike.jpg",
                             categories=[{"title": "Category: Cyclists"}]),
        "No Article": dict(title="No Article", missing=True),
    }
    files = {
        # on Commons, freely licensed. Commons echoes the title back with
        # SPACES where pageimages used underscores -- the join has to survive
        # that, which is exactly what silently lost 90% of the first run.
        "File:Free Player 2011.jpg": {
            "mime": "image/jpeg", "thumburl": "https://x/free.jpg",
            "descriptionurl": "https://commons/File:Free.jpg",
            "extmetadata": {"License": {"value": "cc-by-sa-4.0"},
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "Artist": {"value": "<a href=\'#\'>Jane Doe</a>"}},
        },
        # on Commons but non-commercial
        "File:Nc.jpg": {
            "mime": "image/jpeg", "thumburl": "https://x/nc.jpg",
            "extmetadata": {"License": {"value": "cc-by-nc-2.0"}},
        },
        # File:Fair.jpg is deliberately absent: a local non-free upload
    }

    def fake_api(endpoint, **params):
        if endpoint == cp.WP_API:
            want = params["titles"].split("|")
            return {"query": {"pages": [pages[t] for t in want if t in pages]}}
        want = params["titles"].split("|")
        return {"query": {"pages": [
            {"title": t, "imageinfo": [files[t]]} if t in files
            else {"title": t, "missing": True} for t in want]}}

    real_api, real_fetch, real_dir = cp.api, cp.fetch_square, cp.PHOTO_DIR
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    cp.api = fake_api
    cp.fetch_square = lambda url: Image.new("RGB", (cp.PHOTO_PX, cp.PHOTO_PX),
                                            (10, 20, 30))
    cp.PHOTO_DIR = tmp / "assets" / "photos"
    cp.ROOT = tmp
    try:
        doc = cp.build(list(pages), allow_restricted=False, redownload=True)
    finally:
        cp.api, cp.fetch_square, cp.PHOTO_DIR = real_api, real_fetch, real_dir
        cp.ROOT = ROOT

    assert set(doc["players"]) == {"Free Player"}, sorted(doc["players"])
    rec = doc["players"]["Free Player"]
    assert rec["license"] == "cc-by-sa" and rec["attribution_required"]
    assert rec["attribution"] == "Jane Doe / CC BY-SA 4.0", rec["attribution"]
    rejected = " ".join(doc["rejected"])
    assert rec["file"] == "File:Free Player 2011.jpg", rec["file"]
    assert "not on Commons" in rejected, doc["rejected"]
    assert "allowlist" in rejected, doc["rejected"]
    # every player who did not get a photo is accounted for by name
    assert sum(doc["rejected"].values()) == len(pages) - 1, doc["rejected"]
    assert "article is not about basketball" in doc["rejected"], \
        "the cyclist should be refused before the licence gate"
    assert "no English Wikipedia article" in doc["rejected"], doc["rejected"]
    print("test_end_to_end_with_a_stubbed_api PASS")


def test_manifest_matches_the_files_on_disk():
    """Every manifest entry must point at a photo that is actually there."""
    if not cp.MANIFEST.exists():
        print("test_manifest_matches_the_files_on_disk SKIP (no manifest yet)")
        return
    doc = json.loads(cp.MANIFEST.read_text(encoding="utf-8"))
    for name, rec in doc["players"].items():
        assert (ROOT / rec["local"]).exists(), f"{name}: {rec['local']} missing"
        assert rec["license"] in ("public-domain", "cc-by", "cc-by-sa"), \
            f"{name}: {rec['license']} is not a usable family"
        if rec["attribution_required"]:
            assert rec["attribution"], f"{name}: credit required but empty"
    assert cp.CREDITS.exists(), "no credits sheet"
    credits = cp.CREDITS.read_text(encoding="utf-8")
    for name, rec in doc["players"].items():
        if rec["attribution_required"]:
            assert name in credits, f"{name} missing from CREDITS.md"
    print(f"test_manifest_matches_the_files_on_disk PASS "
          f"({len(doc['players'])} photos)")


if __name__ == "__main__":
    test_allowlist_accepts_the_four_free_families()
    test_everything_else_is_refused()
    test_nc_and_nd_are_never_read_as_cc_by()
    test_attribution_line_survives_the_html()
    test_end_to_end_with_a_stubbed_api()
    test_manifest_matches_the_files_on_disk()
    print("\nall commons-photo tests PASS")

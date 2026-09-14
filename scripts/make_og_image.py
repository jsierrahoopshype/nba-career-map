#!/usr/bin/env python3
"""Build assets/og-career-map.png (1200x630) from the career-paths map source.

The source art is 800x451 (ratio 1.774). The Open Graph standard card is
1200x630 (ratio 1.905) -- WIDER than the source. Something has to give, and
the one option ruled out is stretching: a non-uniform scale would visibly
squash the US map and the team logos. Every mode below scales uniformly.

  --fit cover  (default)  Scale width-driven to 1200x676, then take the 46
                          surplus pixels of height off the TOP and BOTTOM --
                          but not blindly down the middle. The script finds
                          the artwork's real content (red arcs, team logos;
                          map water and land are not content) and spends the
                          crop where the clearance is, taking from whichever
                          edge has more room until the two are level. On this
                          image the top carries 52px of empty Canadian water
                          and the bottom only 9px before the Heat logo, so all
                          46px come off the top and the bottom is untouched.
                          Fills the card edge to edge, nothing clipped. The
                          build refuses if the surplus cannot fit in the
                          available margins.

  --fit extend            Scale to fit inside (1120x630) and replicate the
                          outermost pixel column outward 40px on each side, so
                          the map simply reaches further out to sea. Only
                          invisible when those columns are a flat wash --
                          measured, and refused when they are not.

  --fit contain           Same scale, but pad the 40px with a flat colour
                          sampled from the source's corners. Always safe,
                          always shows bars.

Usage:
    pip install Pillow
    python scripts/make_og_image.py assets/career-map-source.webp
    python scripts/make_og_image.py assets/career-map-source.webp --report
"""

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required:  pip install Pillow")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "og-career-map.png"

TARGET_W, TARGET_H = 1200, 630

# Above this per-channel spread, an edge column is not a flat wash and
# replicating it sideways would smear visible structure.
EDGE_FLAT_MAX = 40


def is_content(p):
    """True for artwork that must not be cropped: the red arcs and the team
    logos. The basemap is not content -- its land is a near-neutral off-white
    and its water a pale desaturated blue, both of which are safe to lose.
    """
    r, g, b = p
    if max(p) < 150:                                  # dark: logo linework
        return True
    if r > g + 25 and r > b + 25:                     # red: the career arcs
        return True
    if max(p) - min(p) > 60:                          # any saturated logo fill
        # ...except the basemap's pale blue water, which is saturated by this
        # measure but is background.
        pale_water = b >= r and b >= g and min(p) > 120
        return not pale_water
    return False


def content_box(img):
    """Bounding box of real artwork, as (top, bottom, left, right) margins."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    rows = [y for y in range(h) if any(is_content(px[x, y]) for x in range(w))]
    cols = [x for x in range(w) if any(is_content(px[x, y]) for y in range(h))]
    if not rows or not cols:
        return None
    return {"top": rows[0], "bottom": h - 1 - rows[-1],
            "left": cols[0], "right": w - 1 - cols[-1]}


def edge_stats(img):
    """Per-channel spread down the outermost pixel column on each side."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    out = {}
    for name, x in (("left", 0), ("right", w - 1)):
        col = [px[x, y] for y in range(h)]
        out[name] = max(max(p[i] for p in col) - min(p[i] for p in col)
                        for i in range(3))
    return out


def sample_background(img):
    """Average the four corner pixels -- the pad colour for --fit contain."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in
               ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    return tuple(sum(c[i] for c in corners) // 4 for i in range(3))


def split_crop(cut, avail_a, avail_b):
    """Spend `cut` pixels across two edges, taking from the roomier one first.

    Returns (from_a, from_b). Both stay within their available margin, and the
    leftover clearances end up as level as the starting margins allow.
    """
    if cut <= 0:
        return 0, 0
    if cut > avail_a + avail_b:
        return None
    if cut <= avail_a - avail_b:          # roomier edge absorbs all of it
        return cut, 0
    if cut <= avail_b - avail_a:
        return 0, cut
    from_a = (cut + avail_a - avail_b) // 2
    return from_a, cut - from_a


def build(src_path, fit, force=False):
    src = Image.open(src_path)
    print(f"source: {src_path}  {src.size[0]}x{src.size[1]}  {src.format}")
    src = src.convert("RGB")
    sw, sh = src.size

    if fit == "cover":
        scale = max(TARGET_W / sw, TARGET_H / sh)
        nw, nh = round(sw * scale), round(sh * scale)
        scaled = src.resize((nw, nh), Image.LANCZOS)
        box = content_box(src)
        cut_h, cut_w = nh - TARGET_H, nw - TARGET_W

        # Margins measured on the source, expressed in scaled pixels.
        av_top = round(box["top"] * scale) if box else cut_h
        av_bot = round(box["bottom"] * scale) if box else cut_h
        av_left = round(box["left"] * scale) if box else cut_w
        av_right = round(box["right"] * scale) if box else cut_w

        vert = split_crop(cut_h, av_top, av_bot)
        horiz = split_crop(cut_w, av_left, av_right)
        if vert is None or horiz is None:
            sys.exit(
                f"--fit cover refused: needs {cut_h}px of height and {cut_w}px "
                f"of width, but the artwork only clears {av_top}+{av_bot}px "
                f"vertically and {av_left}+{av_right}px horizontally. Cropping "
                f"would clip the map. Use --fit contain, or --force.")
        top, bottom = vert
        left, _ = horiz
        out = scaled.crop((left, top, left + TARGET_W, top + TARGET_H))
        print(f"fit=cover: scaled to {nw}x{nh}, cropped {cut_h}px of height "
              f"({top}px top, {bottom}px bottom) and {cut_w}px of width. "
              f"Artwork clearance was {av_top}px top / {av_bot}px bottom, so "
              f"the crop went where the room was. Nothing clipped.")

    elif fit == "extend":
        stats = edge_stats(src)
        rough = [k for k, v in stats.items() if v > EDGE_FLAT_MAX]
        if rough and not force:
            sys.exit(f"--fit extend refused: the {' and '.join(rough)} edge "
                     f"column(s) are not a flat wash "
                     f"({', '.join(f'{k} spread {stats[k]}' for k in rough)}), "
                     f"so replicating them would smear visible structure "
                     f"sideways. Use --fit cover or --fit contain, or --force.")
        scale = min(TARGET_W / sw, TARGET_H / sh)
        nw, nh = round(sw * scale), round(sh * scale)
        scaled = src.resize((nw, nh), Image.LANCZOS)
        out = Image.new("RGB", (TARGET_W, TARGET_H))
        left = (TARGET_W - nw) // 2
        right = TARGET_W - nw - left
        out.paste(scaled, (left, 0))
        if left:
            out.paste(scaled.crop((0, 0, 1, nh)).resize((left, nh), Image.NEAREST),
                      (0, 0))
        if right:
            out.paste(scaled.crop((nw - 1, 0, nw, nh)).resize((right, nh), Image.NEAREST),
                      (left + nw, 0))
        print(f"fit=extend: scaled to {nw}x{nh}, edge columns replicated "
              f"{left}px left and {right}px right. Nothing cropped, no bars.")

    else:
        scale = min(TARGET_W / sw, TARGET_H / sh)
        nw, nh = round(sw * scale), round(sh * scale)
        scaled = src.resize((nw, nh), Image.LANCZOS)
        bg = sample_background(src)
        out = Image.new("RGB", (TARGET_W, TARGET_H), bg)
        left, top = (TARGET_W - nw) // 2, (TARGET_H - nh) // 2
        out.paste(scaled, (left, top))
        print(f"fit=contain: scaled to {nw}x{nh}, padded to {TARGET_W}x{TARGET_H} "
              f"with rgb{bg} ({left}px left, {TARGET_W - nw - left}px right, "
              f"{top}px top, {TARGET_H - nh - top}px bottom). Nothing cropped.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT, "PNG", optimize=True)
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT.relative_to(ROOT)}  {out.size[0]}x{out.size[1]}  {kb:.0f} KB")


def report(src_path):
    src = Image.open(src_path)
    sw, sh = src.size
    print(f"{src_path}: {sw}x{sh} {src.format}  ratio {sw/sh:.3f} "
          f"(card is {TARGET_W/TARGET_H:.3f})")

    print("\nedge columns (can --fit extend replicate them?)")
    for name, spread in edge_stats(src).items():
        ok = spread <= EDGE_FLAT_MAX
        print(f"  {name:5s} spread {spread:3d} -> {'flat' if ok else 'NOT flat'}")

    box = content_box(src)
    print("\nartwork clearance (can --fit cover crop into it?)")
    if not box:
        print("  no content detected")
        return
    scale = max(TARGET_W / sw, TARGET_H / sh)
    cut_h = round(sh * scale) - TARGET_H
    for side in ("top", "bottom", "left", "right"):
        print(f"  {side:6s} {box[side]:3d}px source = "
              f"{round(box[side]*scale):3d}px scaled")
    av_top, av_bot = round(box["top"]*scale), round(box["bottom"]*scale)
    split = split_crop(cut_h, av_top, av_bot)
    print(f"\n  cover needs {cut_h}px of height; available {av_top}+{av_bot}px")
    print(f"  -> {'REFUSED, would clip' if split is None else f'{split[0]}px off the top, {split[1]}px off the bottom'}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="source image (the 800x451 WebP)")
    ap.add_argument("--fit", choices=("cover", "extend", "contain"),
                    default="cover",
                    help="cover = fill the card, crop where the clearance is "
                         "(default); extend = replicate edge columns; "
                         "contain = flat pad")
    ap.add_argument("--force", action="store_true",
                    help="override a refusal from extend or cover")
    ap.add_argument("--report", action="store_true",
                    help="measure the source and exit, writing nothing")
    args = ap.parse_args()
    if args.report:
        return report(Path(args.source))
    build(Path(args.source), args.fit, args.force)


if __name__ == "__main__":
    main()

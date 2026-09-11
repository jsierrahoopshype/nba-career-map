#!/usr/bin/env python3
"""Build assets/og-career-map.png (1200x630) from the career-paths map source.

The source art is 800x450 (16:9, ratio 1.778). The Open Graph standard card is
1200x630 (ratio 1.905) -- WIDER than 16:9. Something has to give, and the one
option ruled out is stretching: a non-uniform scale would visibly squash the
US map and the team logos. So pick a side:

  --fit contain  (default)  Scale to 1200 wide is impossible without losing
                            height, so scale to fit INSIDE the frame
                            (1120x630) and pad 40px on the left and right with
                            a background colour sampled from the source's own
                            corners. Nothing is cropped; the pad is invisible
                            when the art has a flat background.

  --fit extend              Same 1120x630 scale as contain, but instead of a
                            flat pad the outermost pixel column is replicated
                            outward 40px on each side. On this artwork both
                            side edges are open ocean, so the extension is
                            invisible -- the map just reaches further out to
                            sea. Only safe when those columns are near-uniform;
                            --report says whether they are, and the build
                            refuses if they are not (override with --force).

  --fit cover               Scale to 1200x675 (uniform, width-driven) and
                            centre-crop 45px of height: 22.5px off the top and
                            22.5px off the bottom, 3.3% at each edge. Fills the
                            card edge to edge, but clips anything sitting in
                            those top/bottom strips (a title, a legend, the
                            northern/southern edge of the map).

Every mode scales uniformly in x and y, so none of them distorts.

Usage:
    pip install Pillow
    python scripts/make_og_image.py path/to/source.webp [--fit contain|cover]
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


def edge_stats(img):
    """Per-channel spread down the outermost pixel column on each side.

    A low spread means that column is a flat wash (open ocean here), so
    replicating it outward reads as more of the same. A high spread means
    something structural touches the edge -- a coastline, a logo, an arrow
    head -- and replicating would smear it into a 40px streak.
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    out = {}
    for name, x in (("left", 0), ("right", w - 1)):
        px = [rgb.getpixel((x, y)) for y in range(h)]
        spread = max(max(p[i] for p in px) - min(p[i] for p in px)
                     for i in range(3))
        out[name] = spread
    return out


# Above this per-channel spread, an edge column is not a flat wash and
# replicating it would smear visible structure sideways.
EDGE_FLAT_MAX = 40


def sample_background(img):
    """Average the four corner pixels -- the pad colour for --fit contain."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in
               ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    return tuple(sum(c[i] for c in corners) // 4 for i in range(3))


def build(src_path, fit, force=False):
    src = Image.open(src_path)
    print(f"source: {src_path}  {src.size[0]}x{src.size[1]}  {src.format}")
    src = src.convert("RGB")
    sw, sh = src.size

    stats = edge_stats(src)
    print(f"edge columns: left spread {stats['left']}, "
          f"right spread {stats['right']} (flat if <= {EDGE_FLAT_MAX})")

    if fit == "cover":
        # Uniform scale driven by whichever axis needs more, then centre-crop.
        scale = max(TARGET_W / sw, TARGET_H / sh)
        nw, nh = round(sw * scale), round(sh * scale)
        scaled = src.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - TARGET_W) // 2, (nh - TARGET_H) // 2
        out = scaled.crop((left, top, left + TARGET_W, top + TARGET_H))
        print(f"fit=cover: scaled to {nw}x{nh}, centre-cropped "
              f"{nh - TARGET_H}px of height ({top}px top, "
              f"{nh - TARGET_H - top}px bottom) and "
              f"{nw - TARGET_W}px of width")
    elif fit == "extend":
        rough = [k for k, v in stats.items() if v > EDGE_FLAT_MAX]
        if rough and not force:
            sys.exit(f"--fit extend refused: the {' and '.join(rough)} edge "
                     f"column(s) are not a flat wash, so replicating them "
                     f"would smear visible structure. Use --fit contain, or "
                     f"--force if you have looked and it reads fine.")
        scale = min(TARGET_W / sw, TARGET_H / sh)
        nw, nh = round(sw * scale), round(sh * scale)
        scaled = src.resize((nw, nh), Image.LANCZOS)
        out = Image.new("RGB", (TARGET_W, TARGET_H))
        left = (TARGET_W - nw) // 2
        right = TARGET_W - nw - left
        out.paste(scaled, (left, 0))
        # Stretch the 1px outer columns out to the frame edges.
        if left:
            out.paste(scaled.crop((0, 0, 1, nh)).resize((left, nh), Image.NEAREST),
                      (0, 0))
        if right:
            out.paste(scaled.crop((nw - 1, 0, nw, nh)).resize((right, nh), Image.NEAREST),
                      (left + nw, 0))
        print(f"fit=extend: scaled to {nw}x{nh}, edge columns replicated "
              f"{left}px left and {right}px right. Nothing cropped, no bars.")
    else:
        # Uniform scale to fit fully inside, then pad the leftover axis.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="source image (the 800x450 WebP)")
    ap.add_argument("--fit", choices=("contain", "extend", "cover"),
                    default="extend",
                    help="extend = replicate the edge columns outward, nothing "
                         "lost, no bars (default); contain = flat pad; "
                         "cover = fill the card, crop top/bottom")
    ap.add_argument("--force", action="store_true",
                    help="allow --fit extend even on non-flat edge columns")
    ap.add_argument("--report", action="store_true",
                    help="print the source's edge stats and exit, writing nothing")
    args = ap.parse_args()
    if args.report:
        src = Image.open(args.source)
        print(f"{args.source}: {src.size[0]}x{src.size[1]} {src.format}")
        for name, spread in edge_stats(src).items():
            verdict = "flat" if spread <= EDGE_FLAT_MAX else "NOT flat"
            print(f"  {name} edge column: spread {spread} -> {verdict}")
        return
    build(Path(args.source), args.fit, args.force)


if __name__ == "__main__":
    main()

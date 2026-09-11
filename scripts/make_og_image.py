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

  --fit cover               Scale to 1200x675 (uniform, width-driven) and
                            centre-crop 45px of height: 22.5px off the top and
                            22.5px off the bottom, 3.3% at each edge. Fills the
                            card edge to edge, but clips anything sitting in
                            those top/bottom strips (a title, a legend, the
                            northern/southern edge of the map).

Either way the scale is uniform in x and y, so no distortion.

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


def sample_background(img):
    """Average the four corner pixels -- the pad colour for --fit contain."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in
               ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    return tuple(sum(c[i] for c in corners) // 4 for i in range(3))


def build(src_path, fit):
    src = Image.open(src_path)
    print(f"source: {src_path}  {src.size[0]}x{src.size[1]}  {src.format}")
    src = src.convert("RGB")
    sw, sh = src.size

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
    ap.add_argument("--fit", choices=("contain", "cover"), default="contain",
                    help="contain = pad the sides, nothing lost (default); "
                         "cover = fill the card, crop top/bottom")
    args = ap.parse_args()
    build(Path(args.source), args.fit)


if __name__ == "__main__":
    main()

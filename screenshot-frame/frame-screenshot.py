#!/usr/bin/env python3
"""Composite iOS Simulator screenshots into an iPhone-style device body.

Draws the phone around the screen — rounded titanium rail, action button,
volume rocker, side button, camera control, drop shadow — on a transparent
background, so shots can be dropped straight into a pitch deck or a README.

    python3 frame-screenshot.py shots/                 # portrait
    python3 frame-screenshot.py shots/ --landscape     # landscape
    python3 frame-screenshot.py one.png -o framed.png  # single file

⚠️ LANDSCAPE NEEDS THE FLAG. `simctl` always writes the raw PORTRAIT
framebuffer, so a landscape capture arrives sideways and there is nothing in
the file to say so. `--landscape` rotates it 90° counter-clockwise (correct for
a "Rotate Left" device) and moves the buttons to the long edges.

Requires Pillow: `pip3 install Pillow`
"""
import argparse
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFilter

BEZEL = 38        # black border between screen edge and body edge
RIM = 7           # bright titanium rail thickness
BODY_R = 205      # body corner radius
SCREEN_R = 168    # screen corner radius
BTN_OUT = 13      # how far buttons protrude
PAD = 130         # canvas margin (shadow + buttons)

BODY_DARK = (28, 28, 30, 255)
RAIL_LIGHT = (176, 178, 184, 255)
RAIL_MID = (122, 124, 130, 255)

# (fraction along the edge, length) — action, volume up, volume down | power, camera
NEAR_EDGE = [(0.150, 78), (0.218, 132), (0.290, 132)]
FAR_EDGE = [(0.232, 196), (0.430, 104)]


def _rounded(size, radius, fill):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=fill
    )
    return img


def frame(src_path, out_path, landscape=False):
    """Frame one file on disk and save it. Thin wrapper over `build_body`."""
    canvas, _ = build_body(Image.open(src_path), landscape=landscape)
    canvas.save(out_path)
    return canvas.size


def build_body(shot, landscape=False):
    """Draw the phone around `shot` and return `(RGBA canvas, body_width)`.

    ⚠️ SPLIT OUT OF `frame()` SO THERE IS ONE DEVICE BODY IN THE REPO.
    `store-kit/make_panels.py` needs the same phone composited onto a gradient
    rather than saved to disk, and a second copy of a rail-and-buttons drawing
    is exactly the duplicated-logic shape that keeps biting this project — the
    two would drift the first time a radius changed.

    `body_width` is the phone itself, excluding the transparent shadow/button
    margin, so a caller can scale the device to an exact width.
    """
    shot = shot.convert("RGBA")
    if landscape:
        shot = shot.transpose(Image.ROTATE_90)

    sw, sh = shot.size
    bw, bh = sw + 2 * BEZEL, sh + 2 * BEZEL
    cw, ch = bw + 2 * PAD, bh + 2 * PAD
    bx, by = PAD, PAD

    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

    # Drop shadow.
    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [bx, by + 26, bx + bw, by + bh + 26], radius=BODY_R, fill=(0, 0, 0, 120)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(38)))

    # Buttons, drawn UNDER the body so they read as attached to it.
    d = ImageDraw.Draw(canvas)

    def button(side, frac, length, radius=9):
        if landscape:
            x0 = bx + int(bw * frac)
            x1 = x0 + length
            if side == "near":          # top edge
                y0, y1 = by - BTN_OUT, by + 22
                hi = [x0 + 3, y0, x1 - 3, y0 + 7]
            else:                        # bottom edge
                y0, y1 = by + bh - 22, by + bh + BTN_OUT
                hi = [x0 + 3, y1 - 7, x1 - 3, y1]
        else:
            y0 = by + int(bh * frac)
            y1 = y0 + length
            if side == "near":          # left edge
                x0, x1 = bx - BTN_OUT, bx + 22
                hi = [x0, y0 + 3, x0 + 7, y1 - 3]
            else:                        # right edge
                x0, x1 = bx + bw - 22, bx + bw + BTN_OUT
                hi = [x1 - 7, y0 + 3, x1, y1 - 3]
        d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=RAIL_MID)
        d.rounded_rectangle(hi, radius=4, fill=RAIL_LIGHT)

    for frac, length in NEAR_EDGE:
        button("near", frac, length)
    for frac, length in FAR_EDGE:
        button("far", frac, length)

    # Body + titanium rail.
    body = _rounded((bw, bh), BODY_R, BODY_DARK)
    bd = ImageDraw.Draw(body)
    bd.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=BODY_R,
                         outline=RAIL_LIGHT, width=RIM)
    bd.rounded_rectangle([RIM, RIM, bw - 1 - RIM, bh - 1 - RIM],
                         radius=BODY_R - RIM, outline=(70, 71, 76, 255), width=3)
    canvas.alpha_composite(body, (bx, by))

    # Screen, masked to the corner radius.
    mask = _rounded((sw, sh), SCREEN_R, (255, 255, 255, 255)).split()[3]
    # ⚠️ Multiply in the shot's OWN alpha, don't replace it. A caller that fades
    # the bottom of the screen (store-kit) passes a partly transparent shot; a
    # bare corner mask would paste it back at full opacity and silently undo the
    # fade.
    if shot.mode == 'RGBA':
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, shot.split()[3])
    canvas.paste(shot, (bx + BEZEL, by + BEZEL), mask)

    return canvas, bw


def main():
    ap = argparse.ArgumentParser(description="Frame simulator screenshots in an iPhone body.")
    ap.add_argument("src", help="a .png file, or a directory of them")
    ap.add_argument("-o", "--out", help="output file (single-file mode only)")
    ap.add_argument("--landscape", action="store_true",
                    help="rotate 90° CCW and put the buttons on the long edges")
    args = ap.parse_args()

    src = pathlib.Path(args.src)
    if not src.exists():
        sys.exit(f"not found: {src}")

    if src.is_file():
        out = pathlib.Path(args.out) if args.out else src.with_name(src.stem + "-framed.png")
        print(f"{src.name} -> {out} {frame(src, out, args.landscape)}")
        return

    out_dir = src / "framed"
    out_dir.mkdir(exist_ok=True)
    pngs = sorted(p for p in src.glob("*.png"))
    if not pngs:
        sys.exit(f"no .png files in {src}")
    for p in pngs:
        print(f"{p.name:36} -> {frame(p, out_dir / p.name, args.landscape)}")
    print(f"\n{len(pngs)} framed into {out_dir}")


if __name__ == "__main__":
    main()

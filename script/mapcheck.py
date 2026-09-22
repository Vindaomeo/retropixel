#!/usr/bin/env python3
"""mapcheck -- measure how a panel is actually wired.

How a store-bought panel is soldered internally is rarely in the datasheet:
row by row or column by column, serpentine or not, first pixel top left
or bottom right. Guessing costs more time than measuring.

This script uses CMD_RAW: the colors go straight into the LED chain WITHOUT
any pixel mapping. So what the panel shows is the physical wiring --
regardless of what FW/mx8_config.h currently says.

Usage:
    python mapcheck.py                  four questions -> finished config block
    python mapcheck.py --chase          walk slowly along the chain, just watch
    python mapcheck.py --verify 16x32   test image using a known preset

At the end you get a block to copy 1:1 into FW/mx8_config.h. Before that, the
result is checked with a test image -- also via CMD_RAW, with the PC doing the
mapping. Only once the image is right is it worth flashing.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from mx8.font5x7 import GLYPH_H, GLYPH_W, glyph
from mx8.link import MatrixLink
from mx8.mapping import (
    ORIGIN_BOTTOM_LEFT,
    ORIGIN_BOTTOM_RIGHT,
    ORIGIN_NAMES,
    ORIGIN_TOP_LEFT,
    ORIGIN_TOP_RIGHT,
    PRESETS,
    TileMap,
)

WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (60, 120, 255)


# ---------------------------------------------------------------------------
# Input / output
# ---------------------------------------------------------------------------
def ask(question: str, options: list[tuple[str, object]]) -> object:
    """A question with numbered answers. Returns the value of the chosen answer."""
    print()
    print(question)
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}) {label}")
    while True:
        raw = input("  > ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][1]
        print(f"  Please enter 1 to {len(options)}.")


def rgb_bytes(*colors: tuple[int, int, int]) -> bytes:
    return bytes(v for c in colors for v in c)


# ---------------------------------------------------------------------------
# Test image
# ---------------------------------------------------------------------------
def test_image(tm: TileMap) -> list[list[tuple[int, int, int]]]:
    """An image that gives away any twist at a glance.

    - green border around the whole area  -> is the area complete?
    - red corner top left, short arm to the right, long arm downwards
      -> reveals every rotation, mirroring AND axis swap
    - each tile shows its number as a digit  -> are the panels the right way up?
    """
    w, h = tm.width, tm.height
    img = [[(0, 0, 0) for _ in range(w)] for _ in range(h)]

    # Border
    for x in range(w):
        img[0][x] = img[h - 1][x] = (0, 40, 0)
    for y in range(h):
        img[y][0] = img[y][w - 1] = (0, 40, 0)

    # Tile numbers -- only meaningful if there is more than one tile.
    if tm.num_tiles > 1:
        for ty in range(tm.tiles_y):
            for tx in range(tm.tiles_x):
                n = ty * tm.tiles_x + tx
                rows = glyph(str(n)) if n < 10 else glyph("?")
                # centered in the tile, but never on the border
                ox = min(max(tx * tm.tile_w + (tm.tile_w - GLYPH_W) // 2, 1),
                         max(1, w - 1 - GLYPH_W))
                oy = min(max(ty * tm.tile_h + (tm.tile_h - GLYPH_H) // 2, 1),
                         max(1, h - 1 - GLYPH_H))
                for gy, row in enumerate(rows):
                    for gx, ch in enumerate(row):
                        if ch != "#":
                            continue
                        x, y = ox + gx, oy + gy
                        if 0 <= x < w and 0 <= y < h:
                            img[y][x] = (120, 120, 120)

    # Asymmetric corner: short arm to the right, long arm downwards.
    # Only this way does a swapped x/y axis show up too.
    for x in range(min(3, w)):
        img[0][x] = RED
    for y in range(min(5, h)):
        img[y][0] = RED
    return img


def show_image(link: MatrixLink, tm: TileMap, img) -> None:
    """Show an image via CMD_RAW -- here the PC does the mapping.

    This lets you check a configuration without reflashing the firmware:
    if the image is right, the config block is right.
    """
    chain = [(0, 0, 0)] * tm.num_leds
    for y in range(tm.height):
        for x in range(tm.width):
            chain[tm.index(x, y)] = img[y][x]
    link.send_raw(0, rgb_bytes(*chain))


def ansi_preview(img) -> str:
    out = []
    for row in img:
        out.append("".join(f"\033[48;2;{r};{g};{b}m  " for r, g, b in row) + "\033[0m")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The wizard
# ---------------------------------------------------------------------------
def wizard(link: MatrixLink, tile_w: int, tile_h: int,
           tiles_x: int, tiles_y: int, strands: int) -> TileMap:
    tile_leds = tile_w * tile_h
    print(f"\nAssuming: {tiles_x}x{tiles_y} tiles of {tile_w}x{tile_h} pixels each "
          f"= {tile_w * tiles_x}x{tile_h * tiles_y}, {strands} data pin(s).")
    print("If that is wrong, correct it with --tile / --tiles / --strands.")
    print("\nLook at the panel the way it will be mounted later.")

    # -- 1) Where is chain index 0? ------------------------------------------
    link.send_raw(0, rgb_bytes(WHITE))
    origin = ask(
        "Exactly one pixel is lit white. Which corner of the FIRST tile is it in?",
        [
            ("top left", ORIGIN_TOP_LEFT),
            ("top right", ORIGIN_TOP_RIGHT),
            ("bottom left", ORIGIN_BOTTOM_LEFT),
            ("bottom right", ORIGIN_BOTTOM_RIGHT),
        ],
    )

    # -- 2) Does the chain run in rows or in columns? ------------------------
    run = min(tile_w, tile_h, 4)
    link.send_raw(0, rgb_bytes(*[RED] * run))
    vertical = ask(
        f"Now {run} red pixels are lit. How are they arranged?",
        [("horizontally, side by side", False), ("vertically, one above the other", True)],
    )

    # -- 3) Serpentine? ------------------------------------------------------
    # First pass red, second green. Where green starts gives everything away.
    # Deliberately without "top/bottom": that already depends on question 1.
    run_len = tile_h if vertical else tile_w
    link.send_raw(0, rgb_bytes(*([RED] * run_len + [GREEN] * run_len)))
    serpentine = ask(
        "A red and a green line lie next to each other.\n"
        "Where does green start?",
        [
            ("right next to the LAST red pixel (serpentine)", True),
            ("right next to the FIRST red pixel (every line same direction)", False),
        ],
    )

    # -- 4) How are the other tiles arranged? --------------------------------
    flip_alt_rows = False
    tile_order_serpentine = False

    if tiles_x * tiles_y > 1:
        # White = chain index 0, blue = first pixel of the second tile.
        # Both in ONE RAW frame, because RAW always clears the rest.
        span = [(0, 0, 0)] * (tile_leds + 1)
        span[0] = WHITE
        span[tile_leds] = BLUE
        link.send_raw(0, rgb_bytes(*span))

        where = ask(
            "Additionally, a blue pixel is lit -- the first pixel of the\n"
            "SECOND tile. Where is it?",
            [
                ("in a different tile than the white one", "other"),
                ("in the same tile as the white one", "wrong"),
            ],
        )
        if where == "wrong":
            print("\n  Then both tiles hang off the same data pin, or --tile")
            print("  is wrong. Check the wiring and the tile size,")
            print("  then start again.")
            sys.exit(1)

        if tiles_y > 1:
            flip_alt_rows = bool(ask(
                "Compare the corners: is the blue pixel in the same corner of ITS\n"
                "tile as the white one in its own?",
                [
                    ("yes, the same corner", False),
                    ("no, diagonally opposite (tile is rotated 180 degrees)", True),
                ],
            ))

        if tiles_x > 1:
            tile_order_serpentine = bool(ask(
                "And the chaining of the tiles: does each tile row start again\n"
                "on the left, or does it continue in serpentine fashion?",
                [
                    ("each tile row starts again on the left", False),
                    ("serpentine -- the next row runs backwards", True),
                ],
            ))

    link.clear()
    return TileMap(
        tile_w=tile_w, tile_h=tile_h, tiles_x=tiles_x, tiles_y=tiles_y,
        vertical=bool(vertical), serpentine=bool(serpentine), origin=int(origin),
        flip_alt_rows=flip_alt_rows, tile_order_serpentine=tile_order_serpentine,
        strands=strands,
    )


def chase(link: MatrixLink, num_leds: int, delay: float) -> None:
    print(f"Walking the chain, {num_leds} LEDs, {delay:g} s per pixel. Ctrl-C quits.")
    i = 0
    while True:
        link.send_raw(i, rgb_bytes(WHITE))
        print(f"\r  chain index {i:>5}", end="", flush=True)
        time.sleep(delay)
        i = (i + 1) % num_leds


# ---------------------------------------------------------------------------
def parse_pair(text: str, what: str) -> tuple[int, int]:
    try:
        a, b = text.lower().split("x")
        return int(a), int(b)
    except ValueError:
        raise SystemExit(f"--{what} needs the form WIDTHxHEIGHT, e.g. 32x8")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Measure the panel wiring and generate a config block.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--port", help="e.g. COM4 (otherwise MX8_PORT / auto-detect)")
    ap.add_argument("-b", "--baud", type=int, default=None)
    ap.add_argument("--tile", default="32x8", help="size of ONE tile (default 32x8)")
    ap.add_argument("--tiles", default="1x2", help="tiles side by side x stacked")
    ap.add_argument("--strands", type=int, default=2, help="number of data pins")
    ap.add_argument("--brightness", type=int, default=24,
                    help="brightness for measuring (default 24 -- enough, and doesn't dazzle)")
    ap.add_argument("--chase", action="store_true",
                    help="just walk the chain, no questions")
    ap.add_argument("--delay", type=float, default=0.25, help="seconds per pixel with --chase")
    ap.add_argument("--verify", metavar="PRESET",
                    help=f"only show the test image for a preset: {', '.join(PRESETS)}")
    ap.add_argument("--release", action="store_true",
                    help="send RELEASE at the end (panel shows its test pattern again)")
    args = ap.parse_args(argv)

    tile_w, tile_h = parse_pair(args.tile, "tile")
    tiles_x, tiles_y = parse_pair(args.tiles, "tiles")

    link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
    link.open()
    geo = link.wait_ready(timeout=8.0)
    print(f"Port     {link.port} @ {link.baud} Baud")
    print(f"READY    {geo}   (this is the CURRENTLY flashed configuration)")
    link.set_brightness(args.brightness)

    try:
        if args.chase:
            chase(link, geo.width * geo.height, args.delay)
            return 0

        if args.verify:
            if args.verify not in PRESETS:
                raise SystemExit(f"Unknown preset. Known: {', '.join(PRESETS)}")
            tm = PRESETS[args.verify]
        else:
            tm = wizard(link, tile_w, tile_h, tiles_x, tiles_y, args.strands)

        if not tm.is_bijective():
            print("\nERROR: That does not give a valid mapping -- please measure again.")
            return 1

        img = test_image(tm)
        show_image(link, tm, img)

        print("\n" + "=" * 68)
        print("This is what the panel should look like now:")
        print("=" * 68)
        print(ansi_preview(img))
        print("=" * 68)
        print("Red corner top left (short arm to the right, long arm downwards),")
        print("green border all around, each tile showing its number -- upright")
        print("and not mirrored.\n")
        print("If the image is right, copy this block into FW/mx8_config.h:\n")
        print(tm.to_defines())
        print("If it isn't, run the script again and look more closely.")
        input("\n[Enter] to quit ")
        return 0
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        try:
            if args.release:
                link.release()
            else:
                link.clear()
        except Exception:
            pass
        link.close()


if __name__ == "__main__":
    sys.exit(main())

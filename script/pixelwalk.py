#!/usr/bin/env python3
"""pixelwalk -- drive every pixel individually, one per second.

Exactly one pixel is lit, then the next. Order is row by row:

    (0,0) (1,0) (2,0) ... (xmax,0)
    (0,1) (1,1) ...       (xmax,1)
    ...                   (xmax,ymax)   -> and back to the start

What it's good for: it is the only test that touches every single LED.
Dead pixels, cold solder joints and mapping errors all show up, and because
each step prints the coordinate AND the chain index, you see immediately
whether the image appears where it should.

    python pixelwalk.py -p COM4                 first pass fast, then 1 pixel/s
    python pixelwalk.py --interval 0.1          fast afterwards too
    python pixelwalk.py --first-interval 1      first pass at one per second too
    python pixelwalk.py --column                column by column instead of row by row
    python pixelwalk.py --trail                 already checked pixels stay dimly lit
    python pixelwalk.py --start 5,3             resume at this position
    python pixelwalk.py --raw                   follow the LED CHAIN instead of the image
    python pixelwalk.py --preview               in the terminal, no hardware

The FIRST pass runs fast (--first-interval, default 0.05 s): for a video it is
done in a good 25 s instead of 8.5 min. From the second pass on, --interval
applies, i.e. one per second for a close look.

``--raw`` is the counter-test to the normal case: instead of (x,y) the dot
follows chain index 0,1,2,... If it jumps all over the panel, the wiring
differs from the configuration -- continue with mapcheck.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from mx8 import Framebuffer, MatrixLink
from mx8.mapping import PRESETS, TileMap

# White makes every color channel visible: if one fails, the dot turns colored
# instead of white. A single-color test dot would hide exactly that.
COLOR = (255, 255, 255)
TRAIL = (0, 26, 12)          # dim green for already checked pixels


def walk_order(width: int, height: int, column_major: bool):
    """The order in which the pixels get their turn."""
    if column_major:
        for x in range(width):
            for y in range(height):
                yield x, y
    else:
        for y in range(height):
            for x in range(width):
                yield x, y


def guess_tilemap(width: int, height: int, name: Optional[str] = None) -> Optional[TileMap]:
    """Matching preset for the reported geometry.

    Only needed for displaying the chain index and for ``--raw`` --
    the actual mapping is done by the firmware. Several presets can have
    the same size (16x32 with and without serpentine); then the first one
    wins, or you pick one with --preset.
    """
    if name:
        if name not in PRESETS:
            raise SystemExit(f"Unknown preset. Known: {', '.join(PRESETS)}")
        return PRESETS[name]
    for tm in PRESETS.values():
        if tm.width == width and tm.height == height:
            return tm
    return None


def parse_start(text: str, width: int, height: int) -> tuple[int, int]:
    """--start as a coordinate. Where it falls in the order is decided
    later by the list itself -- otherwise the start point with --column or
    --raw would differ from the one given."""
    try:
        x, y = (int(v) for v in text.replace(" ", "").split(","))
    except ValueError:
        raise SystemExit("--start needs the form X,Y -- e.g. --start 5,3")
    if not (0 <= x < width and 0 <= y < height):
        raise SystemExit(f"--start {x},{y} is outside {width}x{height}")
    return x, y


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Drive every pixel individually, one per interval.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--port", help="e.g. COM4 (otherwise MX8_PORT / auto-detect)")
    ap.add_argument("-b", "--baud", type=int, default=None)
    ap.add_argument("--interval", type=float, default=1.0,
                    help="seconds per pixel from the second pass on (default 1.0)")
    ap.add_argument("--first-interval", type=float, default=0.05,
                    help="seconds per pixel in the FIRST pass (default 0.05 -- "
                         "fast enough for a video)")
    ap.add_argument("--column", action="store_true",
                    help="walk column by column instead of row by row")
    ap.add_argument("--trail", action="store_true",
                    help="leave already checked pixels dimly lit")
    ap.add_argument("--raw", action="store_true",
                    help="follow the chain index instead of the image coordinate")
    ap.add_argument("--start", metavar="X,Y", help="start at this coordinate")
    ap.add_argument("--loops", type=int, default=0, help="passes, 0 = endless")
    ap.add_argument("--brightness", type=int, default=None, help="0..255")
    ap.add_argument("--preset", help=f"preset for chain index/--raw: {', '.join(PRESETS)}")
    ap.add_argument("--size", default="32x16",
                    help="only with --preview: area WIDTHxHEIGHT (default 32x16)")
    ap.add_argument("--preview", action="store_true", help="terminal instead of hardware")
    ap.add_argument("--release", action="store_true",
                    help="send RELEASE instead of CLEAR at the end")
    args = ap.parse_args(argv)

    if args.interval <= 0 or args.first_interval <= 0:
        raise SystemExit("--interval and --first-interval must be greater than 0")

    # -- Connection / geometry -------------------------------------------
    link = None
    if args.preview:
        try:
            width, height = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            raise SystemExit("--size needs the form WIDTHxHEIGHT, e.g. 32x16")
    else:
        link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
        link.open()
        geo = link.wait_ready(timeout=8.0)
        width, height = geo.width, geo.height
        print(f"Port      {link.port} @ {link.baud} Baud")
        print(f"READY     {geo}")
        if args.brightness is not None:
            link.set_brightness(args.brightness)

    total = width * height
    tm = guess_tilemap(width, height, args.preset)
    if args.raw and tm is None:
        print("  --raw needs a known preset for this size "
              f"({width}x{height}) -- available: {', '.join(PRESETS)}")
        if link:
            link.close()
        return 1

    start_px = parse_start(args.start, width, height) if args.start else None

    order = "chain index 0,1,2,..." if args.raw else \
            ("column by column" if args.column else "row by row")
    print(f"Area      {width}x{height} = {total} pixels")
    print(f"Order     {order}")
    def dauer(sec: float) -> str:
        t = total * sec
        return f"{t:.0f} s" if t < 90 else f"{t / 60:.1f} min"

    print(f"Timing    1st pass    {args.first_interval:g} s per pixel "
          f"= {dauer(args.first_interval)}")
    print(f"          from 2nd    {args.interval:g} s per pixel "
          f"= {dauer(args.interval)}")
    if tm is None and not args.raw:
        print("Note      no preset for this size -- chain index is not shown")
    print("          Ctrl-C quits\n")

    fb = Framebuffer(width, height)
    seen: list[tuple[int, int]] = []
    loops = 0

    if args.preview:
        print("\033[?25l", end="")

    try:
        while args.loops == 0 or loops < args.loops:
            steps = list(walk_order(width, height, args.column))
            if args.raw:
                # Sort by chain index: the dot follows the wiring.
                steps.sort(key=lambda p: tm.index(*p))
            start_at = steps.index(start_px) if start_px else 0
            # First pass fast (video), then slow for a close look.
            interval = args.first_interval if loops == 0 else args.interval

            for i, (x, y) in enumerate(steps):
                if loops == 0 and i < start_at:
                    continue

                fb.clear()
                if args.trail:
                    for sx, sy in seen:
                        fb.set(sx, sy, *TRAIL)
                fb.set(x, y, *COLOR)

                idx = tm.index(x, y) if tm else None
                label = (f"  {i + 1:>5}/{total}   x={x:>3} y={y:>3}"
                         + (f"   chain {idx:>5}" if idx is not None else "")
                         + (f"   strand {tm.strand_of(idx) + 1}" if tm and tm.strands > 1 else ""))

                if args.preview:
                    print(f"\033[H\033[2J{label}\n")
                    print(fb.to_ansi())
                else:
                    print(label, flush=True)
                    link.send_frame(fb.to_bytes())

                if args.trail:
                    seen.append((x, y))
                time.sleep(interval)

            loops += 1
            seen.clear()
            print(f"  -- Pass {loops} done, {total} pixels checked"
                  + (f", continuing at {args.interval:g} s per pixel --"
                     if loops == 1 and args.interval != args.first_interval else " --"))

        return 0
    except KeyboardInterrupt:
        print()
        return 130
    except (TimeoutError, OSError) as e:
        print(f"\nConnection lost: {e}")
        return 1
    finally:
        if args.preview:
            print("\033[?25h", end="")
        if link:
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

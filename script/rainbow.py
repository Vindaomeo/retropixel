#!/usr/bin/env python3
"""rainbow -- a rainbow across the whole area, in motion.

Six patterns, all using the same mechanism: every pixel gets a hue from a
field ``hue(x, y, t)``, fully saturated and turned all the way up. The only
difference between the patterns is what that field looks like.

    python rainbow.py                     horizontal sweep, 40 fps
    python rainbow.py --mode radial       rings from the center
    python rainbow.py --mode spiral       spiral
    python rainbow.py --cycles 2          two full rainbows side by side
    python rainbow.py --speed 0.15        move more slowly
    python rainbow.py --seconds 30 --release
    python rainbow.py --preview           in the terminal, no hardware

Patterns:
    linear     gradient across the width, moves to the right
    vertical   across the height, moves downwards
    diagonal   45 degrees -- emphasizes that both panels are one area
    radial     concentric rings from the center, round despite the 2:1 area
    spiral     angle plus distance
    wave       horizontal, but with a sinusoidal offset per row

``--cycles 1`` always means "exactly one full rainbow across the area" --
edge to edge for the gradients, center to corner for radial/spiral.

On brightness: 512 WS2812B at full white would be 30.7 A. A rainbow is
never white, but it is bright across the whole area -- which is why the
default brightness is deliberately low. FastLED additionally caps at
MX_MAX_MILLIAMPS and throttles itself if necessary.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Callable, NamedTuple, Optional

from mx8 import Framebuffer, MatrixLink

#: hue(nx, ny, t) -> hue in turns. t is the elapsed phase, also in turns.
#: What nx/ny mean depends on ``aspect``:
#:
#:   aspect = False  ("gradient"): each axis stretched to -1..+1 on its own.
#:                   That way exactly one full rainbow fits on the area,
#:                   whatever the aspect ratio.
#:   aspect = True   ("geometry"): both axes on THE SAME scale, so that
#:                   the corners lie at distance 1.0. Only this way do the
#:                   rings of ``radial`` stay round on a 32x16 area instead
#:                   of oval -- and the rainbow still reaches exactly from
#:                   the center to the corner.
HueField = Callable[[float, float, float], float]


class Mode(NamedTuple):
    field: HueField
    aspect: bool          # True = same scale on both axes
    doc: str


def _linear(nx: float, ny: float, t: float) -> float:
    return nx * 0.5 - t


def _vertical(nx: float, ny: float, t: float) -> float:
    return ny * 0.5 - t


def _diagonal(nx: float, ny: float, t: float) -> float:
    return (nx + ny) * 0.25 - t


def _radial(nx: float, ny: float, t: float) -> float:
    return math.hypot(nx, ny) - t


def _spiral(nx: float, ny: float, t: float) -> float:
    # Angle in turns plus distance -- gives a moving snail shell.
    return math.atan2(ny, nx) / (2 * math.pi) + math.hypot(nx, ny) - t


def _wave(nx: float, ny: float, t: float) -> float:
    return (nx + 0.35 * math.sin(2 * math.pi * (ny * 0.5 + t))) * 0.5 - t


MODES: dict[str, Mode] = {
    "linear":   Mode(_linear,   False, "gradient across the width, moves to the right"),
    "vertical": Mode(_vertical, False, "gradient across the height, moves downwards"),
    "diagonal": Mode(_diagonal, False, "45 degrees across both panels"),
    "radial":   Mode(_radial,   True,  "concentric rings from the center"),
    "spiral":   Mode(_spiral,   True,  "angle plus distance"),
    "wave":     Mode(_wave,     False, "horizontal with a sinusoidal offset per row"),
}


def render(fb: Framebuffer, mode: Mode, t: float,
           cycles: float = 1.0, sat: float = 1.0, val: float = 1.0) -> None:
    """Draw one frame into the framebuffer."""
    w, h = fb.width, fb.height
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0

    if mode.aspect:
        # Corners at distance 1.0 -- same scale for both axes.
        r = math.hypot(cx, cy) or 1.0
        sx = sy = r
    else:
        sx, sy = (cx or 1.0), (cy or 1.0)

    field = mode.field
    for y in range(h):
        ny = (y - cy) / sy
        for x in range(w):
            nx = (x - cx) / sx
            fb.set_hsv(x, y, field(nx, ny, t) * cycles, sat, val)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Rainbow across the whole area.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--port", help="e.g. COM4 (otherwise MX8_PORT / auto-detect)")
    ap.add_argument("-b", "--baud", type=int, default=None)
    ap.add_argument("--mode", default="linear", choices=sorted(MODES),
                    help="pattern (default linear)")
    ap.add_argument("--all", action="store_true",
                    help="show all patterns in turn, --seconds each")
    ap.add_argument("--fps", type=float, default=40.0, help="target frame rate (default 40)")
    ap.add_argument("--speed", type=float, default=0.25,
                    help="rainbow turns per second (default 0.25)")
    ap.add_argument("--cycles", type=float, default=1.0,
                    help="full rainbows across the area (default 1)")
    ap.add_argument("--sat", type=float, default=1.0, help="saturation 0..1")
    ap.add_argument("--brightness", type=int, default=48,
                    help="panel brightness 0..255 (default 48 -- 512 LEDs is a lot)")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="run time per pattern, 0 = endless")
    ap.add_argument("--size", default="32x16",
                    help="only with --preview: area WIDTHxHEIGHT")
    ap.add_argument("--preview", action="store_true", help="terminal instead of hardware")
    ap.add_argument("--release", action="store_true",
                    help="send RELEASE instead of CLEAR at the end")
    args = ap.parse_args(argv)

    if args.fps <= 0:
        raise SystemExit("--fps must be greater than 0")

    link = None
    if args.preview:
        try:
            width, height = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            raise SystemExit("--size needs the form WIDTHxHEIGHT, e.g. 32x16")
    else:
        link = MatrixLink(args.port, args.baud, ack_timeout=1.0,
                          on_log=lambda s: print(f"  LOG  {s}"))
        link.open()
        geo = link.wait_ready(timeout=8.0)
        width, height = geo.width, geo.height
        print(f"Port      {link.port} @ {link.baud} Baud")
        print(f"READY     {geo}")
        link.set_brightness(max(0, min(255, args.brightness)))

    modes = sorted(MODES) if args.all else [args.mode]
    seconds = args.seconds if args.seconds > 0 else (8.0 if args.all else 0.0)

    fb = Framebuffer(width, height)
    period = 1.0 / args.fps
    print(f"Area      {width}x{height} = {width * height} pixels")
    print(f"Patterns  {', '.join(modes)}")
    print(f"Motion    {args.speed:g} turns/s, {args.cycles:g} rainbow(s) "
          f"across the area")
    print(f"Target    {args.fps:g} fps"
          + (f", {seconds:g} s per pattern" if seconds else ", endless")
          + "   Ctrl-C quits\n")

    if args.preview:
        print("\033[?25l", end="")

    try:
        for name in modes:
            mode = MODES[name]
            t0 = time.monotonic()
            frames = 0
            next_frame = t0

            while True:
                now = time.monotonic()
                elapsed = now - t0
                if seconds and elapsed >= seconds:
                    break

                render(fb, mode, elapsed * args.speed, args.cycles, args.sat)
                if args.preview:
                    print(f"\033[H\033[2J  {name}   t={elapsed:5.1f} s"
                          f"   {frames / max(elapsed, 1e-9):5.1f} fps\n")
                    print(fb.to_ansi())
                else:
                    link.send_frame(fb.to_bytes())
                frames += 1

                # Wait for the next tick; do not catch up on missed frames.
                next_frame += period
                sleep = next_frame - time.monotonic()
                if sleep > 0:
                    time.sleep(sleep)
                else:
                    next_frame = time.monotonic()

            dt = time.monotonic() - t0
            print(f"  {name:<9} {frames:>5} Frames in {dt:5.1f} s "
                  f"= {frames / dt:5.1f} fps")
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

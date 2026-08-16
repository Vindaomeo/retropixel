#!/usr/bin/env python3
"""Lauftext in Regenbogenfarben auf der 8x8-Matrix.

    python hello_rainbow.py                          # "Hello World" aufs Panel
    python hello_rainbow.py --preview                # im Terminal, ohne Hardware
    python hello_rainbow.py --text "Grüße!" --speed 12
    python hello_rainbow.py --loops 3 --release

Die Farbe haengt an der *Textspalte*, nicht an der Displayspalte. Der Regenbogen
wandert also mit den Buchstaben mit, statt dass die Buchstaben durch einen
stehenden Farbverlauf laufen -- das sieht deutlich lebendiger aus.
"""

from __future__ import annotations

import argparse
import colorsys
import math
import sys
import time

from mx8 import Framebuffer, MatrixLink
from mx8.font5x7 import GLYPH_H, TextBitmap
from mx8.link import LinkError


# ---------------------------------------------------------------------------
def render(
    fb: Framebuffer,
    tb: TextBitmap,
    pos: float,
    t: float,
    *,
    hue_scale: float,
    hue_speed: float,
    smooth: bool,
    y_off: int,
) -> None:
    """Zeichnet den Text an Scroll-Position ``pos`` (Textspalten) in den Puffer.

    ``pos`` darf gebrochen sein: dann wird zwischen zwei Textspalten
    ueberblendet. Auf einer 8x8-Matrix wirkt das erstaunlich fluessig --
    der Text gleitet, statt in Pixelschritten zu springen.
    """
    fb.clear()
    base = math.floor(pos)
    frac = pos - base

    for x in range(fb.width):
        cx = base + x
        col_a = tb.column(cx)
        col_b = tb.column(cx + 1) if smooth else col_a

        for y in range(GLYPH_H):
            if y + y_off >= fb.height:
                break
            a = 1.0 if col_a[y] else 0.0
            b = 1.0 if col_b[y] else 0.0
            v = a * (1.0 - frac) + b * frac if smooth else a
            if v <= 0.01:
                continue

            # Farbton aus der absoluten Textspalte -> Regenbogen wandert mit
            hue = (cx * hue_scale + t * hue_speed) % 1.0
            r, g, bl = colorsys.hsv_to_rgb(hue, 1.0, v)
            fb.set(x, y + y_off, int(r * 255), int(g * 255), int(bl * 255))


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--text", default="Hello World", help='Standard: "Hello World"')
    ap.add_argument("-p", "--port", help="z. B. COM4 oder /dev/ttyACM0 (sonst MX8_PORT bzw. Auto)")
    ap.add_argument("-b", "--baud", type=int, default=None,
                   help="Standard: MX8_BAUD, sonst 115200")
    ap.add_argument("--brightness", type=int, default=None, help="0..255")
    ap.add_argument("--speed", type=float, default=8.0, help="Textspalten pro Sekunde")
    ap.add_argument("--fps", type=float, default=40.0, help="Bildrate")
    ap.add_argument("--hue-scale", type=float, default=0.035,
                    help="Farbaenderung pro Textspalte (0 = einfarbig)")
    ap.add_argument("--hue-speed", type=float, default=0.15,
                    help="Wanderung des Regenbogens pro Sekunde")
    ap.add_argument("--loops", type=int, default=0, help="Durchlaeufe, 0 = endlos")
    ap.add_argument("--hard", action="store_true",
                    help="ohne Subpixel-Ueberblendung, Text springt in Pixelschritten")
    ap.add_argument("--preview", action="store_true", help="Terminal statt Hardware")
    ap.add_argument("--release", action="store_true",
                    help="am Ende RELEASE senden (Nano zeigt wieder sein Testpattern)")
    args = ap.parse_args(argv)

    tb = TextBitmap(args.text)
    if not len(tb):
        print("Leerer Text.", file=sys.stderr)
        return 2

    fb = Framebuffer(8, 8)
    y_off = max(0, (8 - GLYPH_H) // 2)
    smooth = not args.hard

    # Startposition: Text steht rechts ausserhalb; Ende: komplett links raus.
    start, end = -fb.width, len(tb)
    span = end - start

    link = None
    if not args.preview:
        link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
        link.open()
        geo = link.wait_ready(timeout=8.0)
        print(f"Port     {link.port}")
        print(f"READY    {geo}")
        fb = Framebuffer(geo.width, geo.height)
        y_off = max(0, (geo.height - GLYPH_H) // 2)
        start, end = -fb.width, len(tb)
        span = end - start
        if args.brightness is not None:
            link.set_brightness(args.brightness)

    print(f'Text     "{args.text}"  ({len(tb)} Spalten)')
    print(f"Lauf     {args.speed:g} Spalten/s, {args.fps:g} fps, "
          f"{'weich' if smooth else 'hart'}  —  Strg-C beendet\n")

    if args.preview:
        print("\033[?25l", end="")

    t0 = time.monotonic()
    period = 1.0 / args.fps
    next_tick = t0
    loops_done = 0

    try:
        while True:
            t = time.monotonic() - t0
            travelled = t * args.speed
            if args.loops:
                if travelled >= span * args.loops:
                    break
                loops_done = int(travelled // span)
            pos = start + (travelled % span)

            render(fb, tb, pos, t, hue_scale=args.hue_scale, hue_speed=args.hue_speed,
                   smooth=smooth, y_off=y_off)

            if link:
                link.send_frame(fb.to_bytes())
            else:
                print("\033[H\033[J" + fb.to_ansi())

            next_tick += period
            slack = next_tick - time.monotonic()
            if slack > 0:
                time.sleep(slack)
            else:
                next_tick = time.monotonic()
    except KeyboardInterrupt:
        print()
    finally:
        if args.preview:
            print("\033[?25h", end="")
        if link:
            dur = time.monotonic() - t0
            s = link.stats
            print(f"\n{s['frames_sent']} Frames in {dur:.1f} s = "
                  f"{s['frames_sent'] / dur:.1f} fps"
                  f"  ({s['nacks']} NACK, {s['crc_errors']} CRC-Fehler)")
            try:
                link.release() if args.release else link.clear()
            except Exception:
                pass
            link.close()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except LinkError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError as e:
        print(f"Timeout: {e}", file=sys.stderr)
        sys.exit(1)

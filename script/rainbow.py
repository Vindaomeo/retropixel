#!/usr/bin/env python3
"""rainbow -- ein Regenbogen ueber die ganze Flaeche, in Bewegung.

Sechs Muster, alle ueber dieselbe Mechanik: jedes Pixel bekommt einen Farbton
aus einem Feld ``hue(x, y, t)``, gesaettigt und voll aufgedreht. Der Unterschied
zwischen den Mustern ist nur, wie dieses Feld aussieht.

    python rainbow.py                     waagrechter Lauf, 40 fps
    python rainbow.py --mode radial       Ringe aus der Mitte
    python rainbow.py --mode spiral       Spirale
    python rainbow.py --cycles 2          zwei volle Regenboegen nebeneinander
    python rainbow.py --speed 0.15        langsamer wandern
    python rainbow.py --seconds 30 --release
    python rainbow.py --preview           im Terminal, ohne Hardware

Muster:
    linear     Farbverlauf ueber die Breite, wandert nach rechts
    vertical   ueber die Hoehe, wandert nach unten
    diagonal   45 Grad -- betont, dass beide Panels eine Flaeche sind
    radial     konzentrische Ringe aus der Mitte, rund trotz 2:1-Flaeche
    spiral     Winkel plus Abstand
    wave       waagrecht, aber mit sinusfoermigem Versatz je Zeile

``--cycles 1`` heisst immer "genau ein voller Regenbogen ueber die Flaeche" --
bei den Verlaeufen von Rand zu Rand, bei radial/spiral von der Mitte bis in
die Ecke.

Zur Helligkeit: 512 WS2812B auf Vollweiss waeren 30,7 A. Ein Regenbogen ist
zwar nie weiss, aber flaechendeckend hell -- deshalb ist die Standardhelligkeit
bewusst niedrig. FastLED deckelt zusaetzlich auf MX_MAX_MILLIAMPS und regelt
notfalls selbst herunter.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Callable, NamedTuple, Optional

from mx8 import Framebuffer, MatrixLink

#: hue(nx, ny, t) -> Farbton in Umdrehungen. t ist die verstrichene Phase,
#: ebenfalls in Umdrehungen. Was nx/ny bedeuten, haengt an ``aspect``:
#:
#:   aspect = False  ("Verlauf"):  jede Achse einzeln auf -1..+1 gestreckt.
#:                   Damit passt genau ein voller Regenbogen auf die Flaeche,
#:                   egal wie die Seiten stehen.
#:   aspect = True   ("Geometrie"): beide Achsen mit DEMSELBEN Massstab, so
#:                   dass die Ecken bei Abstand 1,0 liegen. Nur so bleiben die
#:                   Ringe von ``radial`` auf einer 32x16-Flaeche rund und
#:                   nicht oval -- und der Regenbogen reicht trotzdem genau
#:                   von der Mitte bis in die Ecke.
HueField = Callable[[float, float, float], float]


class Mode(NamedTuple):
    field: HueField
    aspect: bool          # True = Massstab beider Achsen gleich
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
    # Winkel in Umdrehungen plus Abstand -- ergibt eine wandernde Schnecke.
    return math.atan2(ny, nx) / (2 * math.pi) + math.hypot(nx, ny) - t


def _wave(nx: float, ny: float, t: float) -> float:
    return (nx + 0.35 * math.sin(2 * math.pi * (ny * 0.5 + t))) * 0.5 - t


MODES: dict[str, Mode] = {
    "linear":   Mode(_linear,   False, "Verlauf ueber die Breite, wandert nach rechts"),
    "vertical": Mode(_vertical, False, "Verlauf ueber die Hoehe, wandert nach unten"),
    "diagonal": Mode(_diagonal, False, "45 Grad ueber beide Panels"),
    "radial":   Mode(_radial,   True,  "konzentrische Ringe aus der Mitte"),
    "spiral":   Mode(_spiral,   True,  "Winkel plus Abstand"),
    "wave":     Mode(_wave,     False, "waagrecht mit sinusfoermigem Versatz je Zeile"),
}


def render(fb: Framebuffer, mode: Mode, t: float,
           cycles: float = 1.0, sat: float = 1.0, val: float = 1.0) -> None:
    """Ein Bild in den Framebuffer zeichnen."""
    w, h = fb.width, fb.height
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0

    if mode.aspect:
        # Ecken auf Abstand 1,0 -- gleicher Massstab fuer beide Achsen.
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
        description="Regenbogen ueber die ganze Flaeche.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--port", help="z. B. COM4 (sonst MX8_PORT / Autoerkennung)")
    ap.add_argument("-b", "--baud", type=int, default=None)
    ap.add_argument("--mode", default="linear", choices=sorted(MODES),
                    help="Muster (Standard linear)")
    ap.add_argument("--all", action="store_true",
                    help="alle Muster nacheinander zeigen, je --seconds lang")
    ap.add_argument("--fps", type=float, default=40.0, help="Zielbildrate (Standard 40)")
    ap.add_argument("--speed", type=float, default=0.25,
                    help="Regenbogen-Umdrehungen je Sekunde (Standard 0,25)")
    ap.add_argument("--cycles", type=float, default=1.0,
                    help="volle Regenboegen ueber die Flaeche (Standard 1)")
    ap.add_argument("--sat", type=float, default=1.0, help="Saettigung 0..1")
    ap.add_argument("--brightness", type=int, default=48,
                    help="Panel-Helligkeit 0..255 (Standard 48 -- 512 LEDs sind viel)")
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="Laufzeit je Muster, 0 = endlos")
    ap.add_argument("--size", default="32x16",
                    help="nur mit --preview: Flaeche BREITExHOEHE")
    ap.add_argument("--preview", action="store_true", help="Terminal statt Hardware")
    ap.add_argument("--release", action="store_true",
                    help="am Ende RELEASE senden statt CLEAR")
    args = ap.parse_args(argv)

    if args.fps <= 0:
        raise SystemExit("--fps muss groesser als 0 sein")

    link = None
    if args.preview:
        try:
            width, height = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            raise SystemExit("--size braucht die Form BREITExHOEHE, z. B. 32x16")
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
    print(f"Flaeche   {width}x{height} = {width * height} Pixel")
    print(f"Muster    {', '.join(modes)}")
    print(f"Bewegung  {args.speed:g} Umdrehungen/s, {args.cycles:g} Regenbogen "
          f"ueber die Flaeche")
    print(f"Ziel      {args.fps:g} fps"
          + (f", {seconds:g} s je Muster" if seconds else ", endlos")
          + "   Strg-C beendet\n")

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

                # Auf das naechste Raster warten; verpasste Frames nicht nachholen.
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
        print(f"\nVerbindung verloren: {e}")
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

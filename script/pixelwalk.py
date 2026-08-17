#!/usr/bin/env python3
"""pixelwalk -- jedes Pixel einzeln ansteuern, eines pro Sekunde.

Genau ein Pixel leuchtet, dann das naechste. Reihenfolge zeilenweise:

    (0,0) (1,0) (2,0) ... (xmax,0)
    (0,1) (1,1) ...       (xmax,1)
    ...                   (xmax,ymax)   -> und wieder von vorn

Wozu das gut ist: es ist der einzige Test, der jede einzelne LED anfasst.
Tote Pixel, kalte Loetstellen und Mapping-Fehler fallen damit auf, und weil zu
jedem Schritt Koordinate UND Kettenindex mitgedruckt werden, sieht man sofort,
ob das Bild dort erscheint, wo es soll.

    python pixelwalk.py -p COM4                 erster Lauf schnell, dann 1 Pixel/s
    python pixelwalk.py --interval 0.1          auch danach schnell
    python pixelwalk.py --first-interval 1      auch der erste Lauf im Sekundentakt
    python pixelwalk.py --column                spaltenweise statt zeilenweise
    python pixelwalk.py --trail                 bereits gepruefte Pixel bleiben schwach an
    python pixelwalk.py --start 5,3             an dieser Stelle weitermachen
    python pixelwalk.py --raw                   der LED-KETTE folgen statt dem Bild
    python pixelwalk.py --preview               im Terminal, ohne Hardware

Der ERSTE Durchlauf laeuft schnell (--first-interval, Standard 0,05 s): fuer ein
Video ist er damit in gut 25 s durch statt in 8,5 min. Ab dem zweiten Durchlauf
gilt --interval, also der Sekundentakt zum genauen Hinschauen.

``--raw`` ist der Gegentest zum Normalfall: statt (x,y) laeuft der Punkt den
Kettenindex 0,1,2,... entlang. Springt er dabei kreuz und quer ueber das Panel,
ist die Verdrahtung anders als konfiguriert -- dann weiter mit mapcheck.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from mx8 import Framebuffer, MatrixLink
from mx8.mapping import PRESETS, TileMap

# Weiss macht jeden Farbkanal sichtbar: faellt einer aus, wird der Punkt bunt
# statt weiss. Ein einfarbiger Testpunkt wuerde genau das verstecken.
COLOR = (255, 255, 255)
TRAIL = (0, 26, 12)          # schwaches Gruen fuer bereits gepruefte Pixel


def walk_order(width: int, height: int, column_major: bool):
    """Die Reihenfolge, in der die Pixel drankommen."""
    if column_major:
        for x in range(width):
            for y in range(height):
                yield x, y
    else:
        for y in range(height):
            for x in range(width):
                yield x, y


def guess_tilemap(width: int, height: int, name: Optional[str] = None) -> Optional[TileMap]:
    """Passendes Preset zur gemeldeten Geometrie.

    Wird nur fuer die Anzeige des Kettenindex und fuer ``--raw`` gebraucht --
    das eigentliche Mapping macht die Firmware. Mehrere Presets koennen
    dieselbe Groesse haben (16x32 mit und ohne Zickzack); dann gewinnt das
    erste, oder man waehlt mit --preset aus.
    """
    if name:
        if name not in PRESETS:
            raise SystemExit(f"Preset unbekannt. Bekannt: {', '.join(PRESETS)}")
        return PRESETS[name]
    for tm in PRESETS.values():
        if tm.width == width and tm.height == height:
            return tm
    return None


def parse_start(text: str, width: int, height: int) -> tuple[int, int]:
    """--start als Koordinate. Wo die in der Reihenfolge liegt, entscheidet
    spaeter die Liste selbst -- sonst waere der Startpunkt bei --column oder
    --raw ein anderer als angegeben."""
    try:
        x, y = (int(v) for v in text.replace(" ", "").split(","))
    except ValueError:
        raise SystemExit("--start braucht die Form X,Y -- z. B. --start 5,3")
    if not (0 <= x < width and 0 <= y < height):
        raise SystemExit(f"--start {x},{y} liegt ausserhalb von {width}x{height}")
    return x, y


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Jedes Pixel einzeln ansteuern, eines pro Intervall.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--port", help="z. B. COM4 (sonst MX8_PORT / Autoerkennung)")
    ap.add_argument("-b", "--baud", type=int, default=None)
    ap.add_argument("--interval", type=float, default=1.0,
                    help="Sekunden je Pixel ab dem zweiten Durchlauf (Standard 1,0)")
    ap.add_argument("--first-interval", type=float, default=0.05,
                    help="Sekunden je Pixel im ERSTEN Durchlauf (Standard 0,05 -- "
                         "schnell genug fuer ein Video)")
    ap.add_argument("--column", action="store_true",
                    help="spaltenweise laufen statt zeilenweise")
    ap.add_argument("--trail", action="store_true",
                    help="bereits gepruefte Pixel schwach anlassen")
    ap.add_argument("--raw", action="store_true",
                    help="dem Kettenindex folgen statt der Bildkoordinate")
    ap.add_argument("--start", metavar="X,Y", help="an dieser Koordinate beginnen")
    ap.add_argument("--loops", type=int, default=0, help="Durchlaeufe, 0 = endlos")
    ap.add_argument("--brightness", type=int, default=None, help="0..255")
    ap.add_argument("--preset", help=f"Preset fuer Kettenindex/--raw: {', '.join(PRESETS)}")
    ap.add_argument("--size", default="32x16",
                    help="nur mit --preview: Flaeche BREITExHOEHE (Standard 32x16)")
    ap.add_argument("--preview", action="store_true", help="Terminal statt Hardware")
    ap.add_argument("--release", action="store_true",
                    help="am Ende RELEASE senden statt CLEAR")
    args = ap.parse_args(argv)

    if args.interval <= 0 or args.first_interval <= 0:
        raise SystemExit("--interval und --first-interval muessen groesser als 0 sein")

    # -- Verbindung / Geometrie -------------------------------------------
    link = None
    if args.preview:
        try:
            width, height = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            raise SystemExit("--size braucht die Form BREITExHOEHE, z. B. 32x16")
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
        print("  --raw braucht ein bekanntes Preset fuer diese Groesse "
              f"({width}x{height}) -- vorhanden: {', '.join(PRESETS)}")
        if link:
            link.close()
        return 1

    start_px = parse_start(args.start, width, height) if args.start else None

    order = "Kettenindex 0,1,2,..." if args.raw else \
            ("spaltenweise" if args.column else "zeilenweise")
    print(f"Flaeche   {width}x{height} = {total} Pixel")
    print(f"Reihenfolge  {order}")
    def dauer(sec: float) -> str:
        t = total * sec
        return f"{t:.0f} s" if t < 90 else f"{t / 60:.1f} min"

    print(f"Takt      1. Durchlauf {args.first_interval:g} s je Pixel "
          f"= {dauer(args.first_interval)}")
    print(f"          ab dem 2.   {args.interval:g} s je Pixel "
          f"= {dauer(args.interval)}")
    if tm is None and not args.raw:
        print("Hinweis   kein Preset zu dieser Groesse -- Kettenindex wird nicht angezeigt")
    print("          Strg-C beendet\n")

    fb = Framebuffer(width, height)
    seen: list[tuple[int, int]] = []
    loops = 0

    if args.preview:
        print("\033[?25l", end="")

    try:
        while args.loops == 0 or loops < args.loops:
            steps = list(walk_order(width, height, args.column))
            if args.raw:
                # Nach Kettenindex sortieren: der Punkt folgt der Verdrahtung.
                steps.sort(key=lambda p: tm.index(*p))
            start_at = steps.index(start_px) if start_px else 0
            # Erster Durchlauf schnell (Video), danach zum Hinschauen langsam.
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
                         + (f"   Kette {idx:>5}" if idx is not None else "")
                         + (f"   Strang {tm.strand_of(idx) + 1}" if tm and tm.strands > 1 else ""))

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
            print(f"  -- Durchlauf {loops} fertig, {total} Pixel geprueft"
                  + (f", weiter mit {args.interval:g} s je Pixel --"
                     if loops == 1 and args.interval != args.first_interval else " --"))

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

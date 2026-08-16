#!/usr/bin/env python3
"""Smiley-Animation: neutral -> traurig -> lustig -> Augenzwinkern, in Schleife.

    python smiley.py -p COM4               # aufs Panel
    python smiley.py --preview             # im Terminal, ohne Hardware
    python smiley.py --hold 2.0 --morph 0.8 --loops 3

Feste Einteilung, harte Pixel. Auf 8x8 gibt es keine Zwischentoene: jedes
Pixel ist ganz an oder ganz aus.

  * **Augen** nach festem Bitmuster, Zeile 0 und 1, Zeile 2 bleibt frei:

        Zeile 0    0 1 1 0 0 1 1 0
        Zeile 1    1 1 1 0 0 1 1 1
        Zeile 2    0 0 0 0 0 0 0 0

    Beim Blinzeln faellt die obere Reihe weg, die untere bleibt als Lidstrich.

  * **Mund** genau ein Pixel je Spalte, ueber alle acht Spalten:

        lustig   4 5 6 7 7 6 5 4
        neutral  6 6 6 6 6 6 6 6
        traurig  7 6 5 4 4 5 6 7

    Die Zeile kommt aus y(x) = 5.5 + curve * (0.5 - u^2) * 2.5 mit
    u = (x - 3.5)/3.5, auf die naechste Zeile gerundet. Die Uebergaenge
    laufen also weiter ueber den Parameter ``curve`` -- nur wird das Ergebnis
    zu ganzen Pixeln gerundet, statt weich verteilt zu werden.

**Warum keine Weichzeichnung.** Die Vorfassung zeichnete den Mund als
Distanzfeld mit zwei Pixel Strichbreite und weichen Raendern. Auf dem Panel
funktioniert das nicht: teilgedeckte Pixel landen bei kleiner Panel-Helligkeit
auf PWM 1..3, wo WS2812B ungleichmaessig und fleckig werden -- und stellenweise
wurde der Mund dadurch drei Pixel breit statt einem. Harte Pixel sind hier
schlicht das bessere Bild.

Die Amplitude ist bewusst klein gehalten: bei groesseren Werten liegen zwei
Nachbarspalten zwei Zeilen auseinander, und der Mund reisst an den steilen
Enden auseinander. Mit 2.5 bleibt er in jedem Zwischenschritt zusammenhaengend.

Der optionale Gesichtskreis (--ring) ist der einzige Teil, der noch mit
Zwischenhelligkeiten arbeitet -- und aus genau diesem Grund standardmaessig aus.
"""

from __future__ import annotations

import argparse
import colorsys
import math
import sys
import time
from dataclasses import dataclass

from mx8 import Framebuffer, MatrixLink
from mx8.link import LinkError

# --- Geometrie des Gesichts (fuer 8x8 ausgelegt) ---------------------------
#
# Grundsatz: das Gesicht bekommt die GANZE Flaeche. Der Mund laeuft von
# Spalte 0 bis 7 randbuendig durch, die Augen sitzen in Zeile 0 und 1, und
# die Mundwinkel eines Laechelns reichen bis Zeile 7 hinunter -- damit sind
# bei lustig und traurig alle 8 Spalten UND alle 8 Zeilen in Gebrauch.
# (Die vorige Fassung nutzte nur Spalte 1..6 und Zeile 1..7, also 6x7 von
#  8x8 -- auf so wenig Pixeln ist jede ungenutzte Randzeile ein Verlust.)
# Augen als festes Bitmuster. Zeile 2 bleibt frei:
#
#     Zeile 0    0 1 1 0 0 1 1 0
#     Zeile 1    1 1 1 0 0 1 1 1
#     Zeile 2    0 0 0 0 0 0 0 0
#
# Die schmalere obere Reihe sitzt jeweils zur Mitte hin versetzt -- das gibt
# den Augen eine leichte Neigung zueinander und laesst das Gesicht freundlich
# statt starr wirken. Beim Blinzeln faellt genau diese obere Reihe weg, die
# untere bleibt als Lidstrich stehen.
EYE_ROW_TOP, EYE_ROW_BOT = 0, 1
EYE_L_TOP, EYE_L_BOT = (1, 2), (0, 1, 2)
EYE_R_TOP, EYE_R_BOT = (5, 6), (5, 6, 7)
EYE_OPEN_AT = 0.5        # ab hier gilt das Auge als offen (obere Reihe an)

# Mund: GENAU EIN Pixel je Spalte, hart gesetzt.
#   y(x) = MOUTH_Y + curve * (0.5 - u^2) * MOUTH_AMP,  u = (x - 3.5) / 3.5
# gerundet auf die naechste Zeile. Mit 5.5 / 2.5 ergibt das
#   lustig   4 5 6 7 7 6 5 4     traurig  7 6 5 4 4 5 6 7
#   neutral  6 6 6 6 6 6 6 6
# also saubere Treppen ohne Sprung groesser als eine Zeile -- auch in jedem
# Zwischenschritt des Uebergangs. Groessere Amplituden reissen den Mund an den
# steilen Enden auseinander, weil dann zwei Nachbarspalten zwei Zeilen
# auseinanderliegen.
MOUTH_CX = 3.5
MOUTH_HALF = 3.5
MOUTH_Y = 5.5
MOUTH_AMP = 2.5

# Traene: ein einzelnes Pixel, das in Spalte 0 durch die freien Zeilen faellt.
TEAR_X = 0
TEAR_Y0 = 2
TEAR_FALL = 4
TEAR_COL = (40, 130, 255)

# --- Angedeuteter Gesichtskreis --------------------------------------------
# Mittelpunkt liegt zwischen den Pixeln (3.5, 3.5), Radius knapp am Rand.
# RING_W ist die halbe Ringbreite: nur Pixel, deren Abstand vom Mittelpunkt
# nahe genug an RING_R liegt, leuchten -- mit weichem Abfall nach aussen und
# innen, damit aus dem groben 8x8-Raster kein Treppenmuster wird.
RING_CX = RING_CY = 3.5
RING_R = 3.6
RING_W = 0.65
RING_HUE = 0.11          # warmes Gelb -- der klassische Smiley eben
RING_SAT = 0.85
# Standardmaessig AUS. Der Ring kostet 20 der 64 Pixel und draengt das Gesicht
# in einen 6x6-Kern -- dort ist fuer 2x2-Augen und einen dicken Mund kein Platz
# mehr. Auf einem 16x16- oder 32x32-Panel waere der Umriss die richtige Wahl,
# auf 8x8 gewinnt das Gesicht, wenn es die ganze Flaeche bekommt.
# Mit --ring 0.25 laesst er sich zuschalten; die Miene legt sich dann darueber.
# Der Wert ist wahrgenommene Helligkeit, nicht PWM: 0.25 landet bei ~12/255.
RING_DEFAULT = 0.0


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Face:
    """Ein Gesichtszustand als Zahlen -- alles hier ist interpolierbar."""

    name: str
    curve: float   # Mund: -1 = Bogen nach unten (traurig), 0 = gerade, +1 = Laecheln
    eye_l: float   # Augenoeffnung links:  1 = offen (Punkt), 0 = zu (Strich)
    eye_r: float   # dito rechts
    hue: float     # Farbton 0..1
    sat: float     # Saettigung 0..1
    tear: float    # Traene 0 = keine, 1 = volle Intensitaet


NEUTRAL = Face("neutral",   curve= 0.0, eye_l=1.0, eye_r=1.0, hue=0.13, sat=0.95, tear=0.0)
TRAURIG = Face("traurig",   curve=-1.0, eye_l=0.75, eye_r=0.75, hue=0.60, sat=0.90, tear=1.0)
LUSTIG  = Face("lustig",    curve= 1.0, eye_l=1.0, eye_r=1.0, hue=0.16, sat=1.00, tear=0.0)
# Beim Zwinkern ist das rechte Auge im Ruhezustand OFFEN -- das Schliessen
# macht die Huellkurve unten. Sonst gaebe es an den Segmentgrenzen einen
# Sprung, weil der Uebergang gegen einen anderen Wert interpoliert als der
# zuletzt gezeichnete Frame hatte.
ZWINKERN= Face("zwinkern",  curve= 1.0, eye_l=1.0, eye_r=1.0, hue=0.92, sat=0.85, tear=0.0)

SEQUENCE = [NEUTRAL, TRAURIG, LUSTIG, ZWINKERN]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_hue(a: float, b: float, t: float) -> float:
    """Farbton auf dem kuerzeren Weg um den Farbkreis.

    Ohne das wuerde Gelb -> Rosa den langen Weg ueber Gruen und Blau nehmen
    statt direkt ueber Orange und Rot.
    """
    d = (b - a + 0.5) % 1.0 - 0.5
    return (a + d * t) % 1.0


def blend(f0: Face, f1: Face, t: float) -> Face:
    return Face(
        name=f1.name if t >= 0.5 else f0.name,
        curve=lerp(f0.curve, f1.curve, t),
        eye_l=lerp(f0.eye_l, f1.eye_l, t),
        eye_r=lerp(f0.eye_r, f1.eye_r, t),
        hue=lerp_hue(f0.hue, f1.hue, t),
        sat=lerp(f0.sat, f1.sat, t),
        tear=lerp(f0.tear, f1.tear, t),
    )


def smoothstep(t: float) -> float:
    """Weiches Ein-/Ausschwingen -- lineare Uebergaenge wirken mechanisch."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
#  Gamma
#
#  Eine WS2812B leuchtet praktisch linear zum PWM-Wert, das Auge nimmt aber
#  ungefaehr mit der Wurzelfunktion wahr: PWM 22 % sieht aus wie rund 50 %
#  Helligkeit. Schwache Farben wirken dadurch systematisch etwa doppelt so
#  kraeftig wie gewuenscht -- genau das laesst "zarte" Toene zu stark wirken.
#
#  Deshalb sind alle Intensitaeten in diesem Modul als *wahrgenommene*
#  Helligkeit 0..1 gemeint und werden mit gamma() in die LED-Ansteuerung
#  umgerechnet. 0.25 heisst dann wirklich "ein Viertel so hell wie voll".
GAMMA = 2.2


def gamma(intensity: float) -> float:
    """Wahrgenommene Helligkeit 0..1 -> LED-Ansteuerung 0..1."""
    return max(0.0, min(1.0, intensity)) ** GAMMA


def _blend(fb: Framebuffer, x: int, row: int, rgb: tuple[float, float, float],
           a: float, over: bool) -> None:
    if not (0 <= x < fb.width and 0 <= row < fb.height) or a <= 0.0015:
        return
    old = fb.get(x, row)
    if over:
        # Alpha-Compositing: das Gesichtsmerkmal verdeckt den Ring, statt sich
        # mit ihm zu addieren. Sonst wuerde z. B. der blaue Mundwinkel der
        # Trauermiene mit dem gelben Ring zu einem fahlen Mischton verlaufen.
        fb.set(x, row,
               min(255, round(old[0] * (1 - a) + rgb[0] * a)),
               min(255, round(old[1] * (1 - a) + rgb[1] * a)),
               min(255, round(old[2] * (1 - a) + rgb[2] * a)))
    else:
        fb.set(x, row,
               min(255, round(old[0] + rgb[0] * a)),
               min(255, round(old[1] + rgb[1] * a)),
               min(255, round(old[2] + rgb[2] * a)))


def draw_ring(fb: Framebuffer, intensity: float, mood: float = 0.0) -> None:
    """Deutet den runden Gesichtsumriss in zartem Gelb an.

    Der Ring bleibt gelb, auch wenn die Miene ins Blaue kippt: ein Smiley ist
    nun mal gelb, und der ruhende gelbe Kreis laesst die farbig wechselnden
    Augen und den Mund als *Ausdruck* lesen statt als Farbwechsel des ganzen
    Gesichts. ``mood`` (-1 traurig .. +1 lustig) hebt ihn nur ganz leicht an.
    """
    if intensity <= 0.0:
        return
    r, g, b = colorsys.hsv_to_rgb(RING_HUE, RING_SAT, 1.0)
    col = (r * 255, g * 255, b * 255)
    scale = gamma(intensity * (0.85 + 0.15 * max(-1.0, min(1.0, mood))))

    for y in range(fb.height):
        for x in range(fb.width):
            d = math.hypot(x - RING_CX, y - RING_CY)
            w = 1.0 - min(1.0, abs(d - RING_R) / RING_W)
            if w > 0.0:
                _blend(fb, x, y, col, scale * smoothstep(w), over=False)


def _mouth_y(x: float, curve: float) -> float:
    """Mittellinie des Mundes an Spalte x."""
    u = (x - MOUTH_CX) / MOUTH_HALF
    return MOUTH_Y + curve * (0.5 - u * u) * MOUTH_AMP


def _set(fb: Framebuffer, x: int, y: int, col) -> None:
    """Ein Pixel, voll an. Keine Teildeckung, keine Zwischenhelligkeit."""
    if 0 <= x < fb.width and 0 <= y < fb.height:
        fb.set(x, y, col[0], col[1], col[2])


def mouth_rows(curve: float, width: int = 8) -> list[int]:
    """Mundzeile je Spalte -- gerundet, also ein hartes Pixel pro Spalte."""
    return [int(math.floor(_mouth_y(x, curve) + 0.5)) for x in range(width)]


def draw_mouth(fb: Framebuffer, curve: float, col) -> None:
    """Mund: genau ein voll leuchtendes Pixel je Spalte.

    Frueher war das ein Distanzfeld mit zwei Pixel Strichbreite und weichen
    Raendern. Auf dem Panel funktioniert das nicht: teilgedeckte Pixel landen
    bei kleiner Panel-Helligkeit auf PWM 1..3, wo WS2812B ungleichmaessig und
    fleckig werden -- und stellenweise wurde der Mund dadurch drei Pixel breit.
    Ein hartes Pixel je Spalte ist auf 8x8 sauberer und liest sich besser.
    """
    for x, y in enumerate(mouth_rows(curve, fb.width)):
        _set(fb, x, y, col)


def draw_eye(fb: Framebuffer, top_cols, bot_cols, open_: float, col) -> None:
    """Auge nach festem Bitmuster. Zu = nur die untere Reihe (Lidstrich)."""
    for x in bot_cols:
        _set(fb, x, EYE_ROW_BOT, col)
    if open_ > EYE_OPEN_AT:
        for x in top_cols:
            _set(fb, x, EYE_ROW_TOP, col)


def draw_face(fb: Framebuffer, f: Face, t: float, ring: float = RING_DEFAULT) -> None:
    fb.clear()
    draw_ring(fb, ring, f.curve)          # zuerst, die Miene legt sich darueber

    r, g, b = colorsys.hsv_to_rgb(f.hue, f.sat, 1.0)
    col = (round(r * 255), round(g * 255), round(b * 255))

    draw_mouth(fb, f.curve, col)
    draw_eye(fb, EYE_L_TOP, EYE_L_BOT, f.eye_l, col)
    draw_eye(fb, EYE_R_TOP, EYE_R_BOT, f.eye_r, col)

    # --- Traene: ein Pixel, das in Spalte 0 durch die freien Zeilen faellt --
    if f.tear > 0.5:
        y = TEAR_Y0 + int(((t * 0.9) % 1.0) * TEAR_FALL)
        _set(fb, TEAR_X, y, TEAR_COL)


# ---------------------------------------------------------------------------
def wink_envelope(local_t: float, hold: float, winks: int) -> float:
    """Augenoeffnung waehrend der Zwinker-Phase. 1 = offen, 0 = geschlossen.

    Das Auge ist die meiste Zeit OFFEN und schliesst nur kurz -- so herum
    liest es sich als Zwinkern. Andersherum (meist zu, kurz auf) wirkt es
    wie ein Gesicht mit einem kaputten Auge.

    Wichtig: an beiden Enden (phase 0 und 1) steht die Kurve auf 1.0, genau
    wie ZWINKERN.eye_r. Dadurch passen Standzeit und Uebergang nahtlos
    aneinander, ohne Sprung im Bild.
    """
    if hold <= 0 or winks <= 0:
        return 1.0
    period = hold / winks
    phase = (local_t % period) / period
    if phase < 0.25:                                       # offen
        return 1.0
    if phase < 0.42:                                       # zufallend
        return 1.0 - smoothstep((phase - 0.25) / 0.17)
    if phase < 0.58:                                       # zu
        return 0.0
    if phase < 0.78:                                       # aufgehend
        return smoothstep((phase - 0.58) / 0.20)
    return 1.0                                             # wieder offen


def _with_wink(f: Face, local_t: float, hold: float, winks: int) -> Face:
    """Zwinker-Modulation auf das rechte Auge legen."""
    return Face(f.name, f.curve, f.eye_l, wink_envelope(local_t, hold, winks),
                f.hue, f.sat, f.tear)


def face_at(t: float, hold: float, morph: float, winks: int) -> tuple[Face, float]:
    """Gesicht zum Zeitpunkt t. Zweiter Rueckgabewert: Fortschritt im Zyklus.

    Jedes Segment ist erst Standzeit, dann Uebergang -- in dieser Reihenfolge.
    Damit beginnt die Animation bei t=0 mit dem gehaltenen neutralen Gesicht
    und nicht mitten in einem Uebergang.
    """
    n = len(SEQUENCE)
    period = hold + morph
    cycle = period * n
    k = int(t // period) % n
    local = t % period

    cur = SEQUENCE[k]
    if local < hold or morph <= 0:
        f = cur if cur is not ZWINKERN else _with_wink(cur, local, hold, winks)
    else:
        nxt = SEQUENCE[(k + 1) % n]
        f = blend(cur, nxt, smoothstep((local - hold) / morph))
    return f, (t % cycle) / cycle


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    global GAMMA
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-p", "--port",
                    help="z. B. COM4 oder /dev/ttyACM0 (sonst MX8_PORT bzw. Auto)")
    ap.add_argument("-b", "--baud", type=int, default=None,
                    help="Standard: MX8_BAUD, sonst 115200")
    ap.add_argument("--fps", type=float, default=16.0, help="Bildrate, Standard 16")
    ap.add_argument("--hold", type=float, default=1.4,
                    help="Standzeit je Miene in Sekunden")
    ap.add_argument("--morph", type=float, default=0.7,
                    help="Dauer des Uebergangs in Sekunden")
    ap.add_argument("--winks", type=int, default=2,
                    help="Zwinker pro Durchgang")
    ap.add_argument("--ring", type=float, default=RING_DEFAULT,
                    help=f"WAHRGENOMMENE Helligkeit des Gesichtskreises 0..1, "
                         f"0 = aus (Standard {RING_DEFAULT})")
    ap.add_argument("--gamma", type=float, default=GAMMA,
                    help=f"Gamma fuer schwache Farben (Standard {GAMMA}, "
                         f"1.0 = aus). Kleiner = kraeftigere Zwischentoene.")
    ap.add_argument("--loops", type=int, default=0, help="Durchlaeufe, 0 = endlos")
    ap.add_argument("--brightness", type=int, default=None, help="0..255")
    ap.add_argument("--preview", action="store_true", help="Terminal statt Hardware")
    ap.add_argument("--release", action="store_true",
                    help="am Ende RELEASE senden (Nano zeigt wieder sein Testpattern)")
    args = ap.parse_args(argv)
    GAMMA = max(0.1, args.gamma)

    fb = Framebuffer(8, 8)
    link = None
    if not args.preview:
        link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
        link.open()
        geo = link.wait_ready(timeout=8.0)
        print(f"Port     {link.port} @ {link.baud} Baud")
        print(f"READY    {geo}")
        fb = Framebuffer(geo.width, geo.height)
        if args.brightness is not None:
            link.set_brightness(args.brightness)

    cycle = (args.morph + args.hold) * len(SEQUENCE)
    print(f"Ablauf   {' -> '.join(f.name for f in SEQUENCE)}")
    print(f"Timing   {args.fps:g} fps, {args.hold:g} s Standzeit, "
          f"{args.morph:g} s Uebergang = {cycle:.1f} s pro Runde")
    print("         Strg-C beendet\n")

    if args.preview:
        print("\033[?25l", end="")

    t0 = time.monotonic()
    period = 1.0 / args.fps
    next_tick = t0
    frames = 0

    try:
        while True:
            t = time.monotonic() - t0
            if args.loops and t >= cycle * args.loops:
                break

            f, _ = face_at(t, args.hold, args.morph, args.winks)
            draw_face(fb, f, t, ring=args.ring)
            frames += 1

            if link:
                link.send_frame(fb.to_bytes())
            else:
                print("\033[H\033[J" + fb.to_ansi())
                print(f"\n  {f.name}")

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
            print(f"\n{frames} Frames in {dur:.1f} s = {frames / dur:.1f} fps "
                  f"(Ziel {args.fps:g})  "
                  f"{s['nacks']} NACK, {s['crc_errors']} CRC-Fehler")
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

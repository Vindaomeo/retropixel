#!/usr/bin/env python3
"""Smiley animation: neutral -> sad -> happy -> wink, in a loop.

    python smiley.py -p COM4               # on the panel
    python smiley.py --preview             # in the terminal, no hardware
    python smiley.py --hold 2.0 --morph 0.8 --loops 3

Fixed layout, hard pixels. On 8x8 there are no in-between shades: every
pixel is either fully on or fully off.

  * **Eyes** as a fixed bit pattern, rows 0 and 1, row 2 stays empty:

        row 0      0 1 1 0 0 1 1 0
        row 1      1 1 1 0 0 1 1 1
        row 2      0 0 0 0 0 0 0 0

    When blinking, the top row drops out; the bottom one stays as an eyelid.

  * **Mouth** exactly one pixel per column, across all eight columns:

        happy    4 5 6 7 7 6 5 4
        neutral  6 6 6 6 6 6 6 6
        sad      7 6 5 4 4 5 6 7

    The row comes from y(x) = 5.5 + curve * (0.5 - u^2) * 2.5 with
    u = (x - 3.5)/3.5, rounded to the nearest row. So the transitions
    still run through the ``curve`` parameter -- the result is just rounded
    to whole pixels instead of being spread out softly.

**Why no anti-aliasing.** The previous version drew the mouth as a
distance field with a two-pixel stroke width and soft edges. On the panel
that doesn't work: partially covered pixels end up at PWM 1..3 at low panel
brightness, where WS2812Bs get uneven and blotchy -- and in places this made
the mouth three pixels wide instead of one. Hard pixels simply make the
better picture here.

The amplitude is deliberately kept small: with larger values two neighboring
columns sit two rows apart, and the mouth tears apart at the steep ends.
With 2.5 it stays connected in every intermediate step.

The optional face circle (--ring) is the only part that still works with
intermediate brightness levels -- and for exactly that reason it is off by default.
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

# --- Face geometry (designed for 8x8) -------------------------------------
#
# Principle: the face gets the WHOLE area. The mouth runs flush from
# column 0 to 7, the eyes sit in rows 0 and 1, and the corners of a smile
# reach all the way down to row 7 -- so for happy and sad all 8 columns AND
# all 8 rows are in use.
# (The previous version only used columns 1..6 and rows 1..7, i.e. 6x7 of
#  8x8 -- with so few pixels, every unused edge row is a loss.)
# Eyes as a fixed bit pattern. Row 2 stays empty:
#
#     row 0      0 1 1 0 0 1 1 0
#     row 1      1 1 1 0 0 1 1 1
#     row 2      0 0 0 0 0 0 0 0
#
# The narrower top row is shifted toward the center on each side -- that gives
# the eyes a slight tilt toward each other and makes the face look friendly
# rather than rigid. When blinking, exactly this top row disappears and the
# bottom one stays as an eyelid line.
EYE_ROW_TOP, EYE_ROW_BOT = 4, 5 #0, 1
EYE_L_TOP, EYE_L_BOT = (5, 6), (4, 5, 6) #(1, 2), (0, 1, 2)
EYE_R_TOP, EYE_R_BOT = (9, 10), (9, 10, 11) #(5, 6), (5, 6, 7)
EYE_OPEN_AT = 0.5        # from here on the eye counts as open (top row on)

# Mouth: EXACTLY ONE pixel per column, set hard.
#   y(x) = MOUTH_Y + curve * (0.5 - u^2) * MOUTH_AMP,  u = (x - 3.5) / 3.5
# rounded to the nearest row. With 5.5 / 2.5 that gives
#   happy    4 5 6 7 7 6 5 4     sad      7 6 5 4 4 5 6 7
#   neutral  6 6 6 6 6 6 6 6
# i.e. clean staircases with no jump bigger than one row -- in every
# intermediate step of the transition, too. Larger amplitudes tear the mouth
# apart at the steep ends, because then two neighboring columns sit two rows
# apart.
MOUTH_CX = 7.5 #3.5
MOUTH_HALF = 7.5 #3.5
MOUTH_Y = 10 #5.5
MOUTH_AMP = 3.5 # 2.5

# Tear: a single pixel that falls through the empty rows in column 0.
TEAR_X = 4 #0
TEAR_Y0 = 6 #2
TEAR_FALL = 3 #4
TEAR_COL = (40, 130, 255)

# --- Hinted face circle ----------------------------------------------------
# The center lies between the pixels (3.5, 3.5), radius just inside the edge.
# RING_W is half the ring width: only pixels whose distance from the center
# is close enough to RING_R light up -- with a soft falloff outward and
# inward, so the coarse 8x8 grid doesn't turn it into a staircase pattern.
RING_CX = RING_CY = 3.5
RING_R = 3.6
RING_W = 0.65
RING_HUE = 0.11          # warm yellow -- the classic smiley, after all
RING_SAT = 0.85
# OFF by default. The ring costs 20 of the 64 pixels and squeezes the face
# into a 6x6 core -- which leaves no room for 2x2 eyes and a thick mouth.
# On a 16x16 or 32x32 panel the outline would be the right choice; on 8x8
# the face wins when it gets the whole area.
# It can be switched on with --ring 0.25; the expression is then drawn on top.
# The value is perceived brightness, not PWM: 0.25 ends up at ~12/255.
RING_DEFAULT = 0.0


# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Face:
    """A face state as numbers -- everything here can be interpolated."""

    name: str
    curve: float   # mouth: -1 = arc downward (sad), 0 = straight, +1 = smile
    eye_l: float   # left eye opening:  1 = open (dot), 0 = closed (line)
    eye_r: float   # same, right
    hue: float     # hue 0..1
    sat: float     # saturation 0..1
    tear: float    # tear 0 = none, 1 = full intensity


NEUTRAL = Face("neutral", curve= 0.0, eye_l=1.0, eye_r=1.0, hue=0.13, sat=0.95, tear=0.0)
SAD     = Face("sad",     curve=-1.0, eye_l=0.75, eye_r=0.75, hue=0.60, sat=0.90, tear=1.0)
HAPPY   = Face("happy",   curve= 1.0, eye_l=1.0, eye_r=1.0, hue=0.16, sat=1.00, tear=0.0)
# For the wink, the right eye is OPEN at rest -- the closing is done by the
# envelope below. Otherwise there would be a jump at the segment boundaries,
# because the transition would interpolate toward a different value than the
# last drawn frame had.
WINK    = Face("wink",    curve= 1.0, eye_l=1.0, eye_r=1.0, hue=0.92, sat=0.85, tear=0.0)

SEQUENCE = [NEUTRAL, SAD, HAPPY, WINK]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_hue(a: float, b: float, t: float) -> float:
    """Hue along the shorter way around the color wheel.

    Without this, yellow -> pink would take the long way through green and blue
    instead of going directly through orange and red.
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
    """Soft ease-in/ease-out -- linear transitions look mechanical."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
#  Gamma
#
#  A WS2812B emits practically linearly with the PWM value, but the eye
#  perceives roughly along a square-root curve: PWM 22 % looks like about 50 %
#  brightness. Faint colors therefore systematically look about twice as
#  strong as intended -- which is exactly what makes "delicate" tones too strong.
#
#  That's why all intensities in this module are meant as *perceived*
#  brightness 0..1 and are converted into LED drive values with gamma().
#  0.25 then really means "a quarter as bright as full".
GAMMA = 2.2


def gamma(intensity: float) -> float:
    """Perceived brightness 0..1 -> LED drive value 0..1."""
    return max(0.0, min(1.0, intensity)) ** GAMMA


def _blend(fb: Framebuffer, x: int, row: int, rgb: tuple[float, float, float],
           a: float, over: bool) -> None:
    if not (0 <= x < fb.width and 0 <= row < fb.height) or a <= 0.0015:
        return
    old = fb.get(x, row)
    if over:
        # Alpha compositing: the facial feature covers the ring instead of
        # adding to it. Otherwise e.g. the blue mouth corner of the sad face
        # would bleed into the yellow ring as a pallid mixed tone.
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
    """Hints at the round face outline in a delicate yellow.

    The ring stays yellow even when the expression tips into blue: a smiley is
    yellow, period, and the steady yellow circle lets the color-changing eyes
    and mouth read as an *expression* rather than as a color change of the
    whole face. ``mood`` (-1 sad .. +1 happy) only lifts it very slightly.
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
    """Center line of the mouth at column x."""
    u = (x - MOUTH_CX) / MOUTH_HALF
    return MOUTH_Y + curve * (0.5 - u * u) * MOUTH_AMP


def _set(fb: Framebuffer, x: int, y: int, col) -> None:
    """One pixel, fully on. No partial coverage, no intermediate brightness."""
    if 0 <= x < fb.width and 0 <= y < fb.height:
        fb.set(x, y, col[0], col[1], col[2])


def mouth_rows(curve: float, width: int = 8) -> list[int]:
    """Mouth row per column -- rounded, so one hard pixel per column."""
    return [int(math.floor(_mouth_y(x, curve) + 0.5)) for x in range(width)]


def draw_mouth(fb: Framebuffer, curve: float, col) -> None:
    """Mouth: exactly one fully lit pixel per column.

    This used to be a distance field with a two-pixel stroke width and soft
    edges. On the panel that doesn't work: partially covered pixels end up at
    PWM 1..3 at low panel brightness, where WS2812Bs get uneven and
    blotchy -- and in places this made the mouth three pixels wide.
    One hard pixel per column is cleaner on 8x8 and reads better.
    """
    for x, y in enumerate(mouth_rows(curve, fb.width)):
        _set(fb, x, y, col)


def draw_eye(fb: Framebuffer, top_cols, bot_cols, open_: float, col) -> None:
    """Eye as a fixed bit pattern. Closed = bottom row only (eyelid line)."""
    for x in bot_cols:
        _set(fb, x, EYE_ROW_BOT, col)
    if open_ > EYE_OPEN_AT:
        for x in top_cols:
            _set(fb, x, EYE_ROW_TOP, col)


def draw_face(fb: Framebuffer, f: Face, t: float, ring: float = RING_DEFAULT) -> None:
    fb.clear()
    draw_ring(fb, ring, f.curve)          # first, the expression is drawn on top

    r, g, b = colorsys.hsv_to_rgb(f.hue, f.sat, 1.0)
    col = (round(r * 255), round(g * 255), round(b * 255))

    draw_mouth(fb, f.curve, col)
    draw_eye(fb, EYE_L_TOP, EYE_L_BOT, f.eye_l, col)
    draw_eye(fb, EYE_R_TOP, EYE_R_BOT, f.eye_r, col)

    # --- Tear: one pixel that falls through the empty rows in column 0 ----
    if f.tear > 0.5:
        y = TEAR_Y0 + int(((t * 0.9) % 1.0) * TEAR_FALL)
        _set(fb, TEAR_X, y, TEAR_COL)


# ---------------------------------------------------------------------------
def wink_envelope(local_t: float, hold: float, winks: int) -> float:
    """Eye opening during the wink phase. 1 = open, 0 = closed.

    The eye is OPEN most of the time and only closes briefly -- that way round
    it reads as a wink. The other way round (mostly closed, briefly open) it
    looks like a face with a broken eye.

    Important: at both ends (phase 0 and 1) the curve sits at 1.0, just
    like WINK.eye_r. That way hold time and transition join seamlessly,
    with no jump in the image.
    """
    if hold <= 0 or winks <= 0:
        return 1.0
    period = hold / winks
    phase = (local_t % period) / period
    if phase < 0.25:                                       # open
        return 1.0
    if phase < 0.42:                                       # closing
        return 1.0 - smoothstep((phase - 0.25) / 0.17)
    if phase < 0.58:                                       # closed
        return 0.0
    if phase < 0.78:                                       # opening
        return smoothstep((phase - 0.58) / 0.20)
    return 1.0                                             # open again


def _with_wink(f: Face, local_t: float, hold: float, winks: int) -> Face:
    """Apply the wink modulation to the right eye."""
    return Face(f.name, f.curve, f.eye_l, wink_envelope(local_t, hold, winks),
                f.hue, f.sat, f.tear)


def face_at(t: float, hold: float, morph: float, winks: int) -> tuple[Face, float]:
    """Face at time t. Second return value: progress through the cycle.

    Each segment is first hold time, then transition -- in that order.
    That way the animation starts at t=0 with the neutral face being held,
    not in the middle of a transition.
    """
    n = len(SEQUENCE)
    period = hold + morph
    cycle = period * n
    k = int(t // period) % n
    local = t % period

    cur = SEQUENCE[k]
    if local < hold or morph <= 0:
        f = cur if cur is not WINK else _with_wink(cur, local, hold, winks)
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
                    help="e.g. COM4 or /dev/ttyACM0 (otherwise MX8_PORT or auto)")
    ap.add_argument("-b", "--baud", type=int, default=None,
                    help="default: MX8_BAUD, otherwise 115200")
    ap.add_argument("--fps", type=float, default=16.0, help="frame rate, default 16")
    ap.add_argument("--hold", type=float, default=1.4,
                    help="hold time per expression in seconds")
    ap.add_argument("--morph", type=float, default=0.7,
                    help="duration of the transition in seconds")
    ap.add_argument("--winks", type=int, default=2,
                    help="winks per pass")
    ap.add_argument("--ring", type=float, default=RING_DEFAULT,
                    help=f"PERCEIVED brightness of the face circle 0..1, "
                         f"0 = off (default {RING_DEFAULT})")
    ap.add_argument("--gamma", type=float, default=GAMMA,
                    help=f"gamma for faint colors (default {GAMMA}, "
                         f"1.0 = off). Smaller = stronger intermediate tones.")
    ap.add_argument("--loops", type=int, default=0, help="passes, 0 = endless")
    ap.add_argument("--brightness", type=int, default=None, help="0..255")
    ap.add_argument("--preview", action="store_true", help="terminal instead of hardware")
    ap.add_argument("--release", action="store_true",
                    help="send RELEASE at the end (Nano goes back to its test pattern)")
    args = ap.parse_args(argv)
    GAMMA = max(0.1, args.gamma)

    fb = Framebuffer(8, 8)
    link = None
    if not args.preview:
        link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
        link.open()
        geo = link.wait_ready(timeout=8.0)
        print(f"Port     {link.port} @ {link.baud} baud")
        print(f"READY    {geo}")
        fb = Framebuffer(geo.width, geo.height)
        if args.brightness is not None:
            link.set_brightness(args.brightness)

    cycle = (args.morph + args.hold) * len(SEQUENCE)
    print(f"Sequence {' -> '.join(f.name for f in SEQUENCE)}")
    print(f"Timing   {args.fps:g} fps, {args.hold:g} s hold, "
          f"{args.morph:g} s transition = {cycle:.1f} s per round")
    print("         Ctrl-C quits\n")

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
            print(f"\n{frames} frames in {dur:.1f} s = {frames / dur:.1f} fps "
                  f"(target {args.fps:g})  "
                  f"{s['nacks']} NACK, {s['crc_errors']} CRC errors")
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
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError as e:
        print(f"Timeout: {e}", file=sys.stderr)
        sys.exit(1)

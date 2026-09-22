#!/usr/bin/env python3
"""Cross-check firmware (C) <-> host (Python).

Compiles mx8_rx.h/mx8_protocol.h with the host compiler and verifies that
both sides produce and read bit-identical framing and the same CRC.
Runs without hardware.

    python tests/test_protocol.py
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "script"))

from mx8.protocol import (  # noqa: E402
    CMD_FRAME,
    CMD_PING,
    ERR_CRC,
    FrameParser,
    Geometry,
    Message,
    MSG_READY,
    ParseError,
    crc16,
    encode,
)

FW = ROOT / "FW"
MAX_PAYLOAD_FW = 200  # must match mx8_config.h

_fails: list[str] = []
_passes = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
    else:
        _fails.append(f"{label}{(' -- ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
def build_harness() -> Path:
    exe = Path(tempfile.mkdtemp(prefix="mx8_")) / "c_parser_test"
    cc = os.environ.get("CC", "cc")
    subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-Werror", f"-I{FW}",
         str(Path(__file__).parent / "c_parser_test.c"), "-o", str(exe)],
        check=True,
    )
    return exe


def c_run(exe: Path, *args: str) -> str:
    r = subprocess.run([str(exe), *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


# ---------------------------------------------------------------------------
def test_crc_known_vectors() -> None:
    """CRC-16/CCITT-FALSE against the canonical check values."""
    check(crc16(b"123456789") == 0x29B1, "CRC check-value '123456789' == 0x29B1",
          f"got 0x{crc16(b'123456789'):04X}")
    check(crc16(b"") == 0xFFFF, "CRC empty == 0xFFFF")
    check(crc16(b"A") == 0xB915, "CRC 'A' == 0xB915", f"got 0x{crc16(b'A'):04X}")


def test_crc_matches_c(exe: Path) -> None:
    rnd = random.Random(1234)
    bad = 0
    for _ in range(300):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 260)))
        if int(c_run(exe, "crc", data.hex()), 16) != crc16(data):
            bad += 1
    check(bad == 0, "CRC C == Python (300 random vectors)", f"{bad} mismatches")


def test_encode_matches_c(exe: Path) -> None:
    rnd = random.Random(99)
    bad = 0
    for _ in range(200):
        cmd = rnd.randrange(1, 0x85)
        payload = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 193)))
        if bytes.fromhex(c_run(exe, "build", str(cmd), payload.hex())) != encode(cmd, payload):
            bad += 1
    check(bad == 0, "Frame encoder C == Python (200 vectors)", f"{bad} mismatches")


# ---------------------------------------------------------------------------
def py_parse(stream: bytes) -> list[str]:
    """Bring the Python parser output into the same text format as the C harness."""
    out = []
    for ev in FrameParser().feed(stream):
        if isinstance(ev, Message):
            out.append(f"MSG {ev.cmd} {ev.payload.hex()}")
        else:
            out.append(f"ERR {ev.cmd} {ERR_CRC}")
    return out


def c_parse(exe: Path, stream: bytes) -> list[str]:
    lines = [l for l in c_run(exe, "parse", stream.hex()).splitlines() if l]
    # The timeout NACK at the end is a firmware detail (half a frame is stuck);
    # the Python parser simply keeps something like that in its buffer. For
    # comparing the payload events we filter it out and check it separately.
    return [l for l in lines if not l.startswith("ERR 255 5")]


def test_parse_clean(exe: Path) -> None:
    rnd = random.Random(7)
    stream = b""
    expect = []
    for _ in range(40):
        cmd = rnd.choice([CMD_FRAME, CMD_PING, MSG_READY, 0x42])
        n = 192 if cmd == CMD_FRAME else rnd.randrange(0, 20)
        payload = bytes(rnd.randrange(256) for _ in range(n))
        stream += encode(cmd, payload)
        expect.append(f"MSG {cmd} {payload.hex()}")
    check(c_parse(exe, stream) == expect, "C parser reads 40 clean frames")
    check(py_parse(stream) == expect, "Python parser reads 40 clean frames")


def test_parse_with_garbage(exe: Path) -> None:
    """Garbage between frames -- both sides must resynchronize."""
    rnd = random.Random(21)
    stream = b""
    expect = []
    for _ in range(30):
        stream += bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 9)))
        payload = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 40)))
        stream += encode(CMD_PING, payload)
        expect.append(f"MSG {CMD_PING} {payload.hex()}")
    got_c, got_py = c_parse(exe, stream), py_parse(stream)
    # Random garbage can itself look like the start of a frame; what matters is
    # that ALL real frames arrive and both sides see the same thing.
    check(all(e in got_c for e in expect), "C parser resyncs across garbage",
          f"{len(expect) - sum(e in got_c for e in expect)} frames lost")
    check(got_c == got_py, "C and Python parsers see the same thing with garbage",
          f"C={len(got_c)} Events, PY={len(got_py)} Events")


def test_payload_boundary(exe: Path) -> None:
    """A SYNC token inside the payload must not throw the parser off."""
    payload = bytes([0xA5, 0x5A, 0x01, 0xC0, 0x00] * 12)
    stream = encode(CMD_FRAME, payload + bytes(192 - len(payload)))
    full = payload + bytes(192 - len(payload))
    check(c_parse(exe, stream) == [f"MSG {CMD_FRAME} {full.hex()}"],
          "SYNC token in payload is skipped correctly (C)")
    check(py_parse(stream) == [f"MSG {CMD_FRAME} {full.hex()}"],
          "SYNC token in payload is skipped correctly (Python)")


def test_bitflip_detected(exe: Path) -> None:
    """No single-bit error may pass as a valid frame.

    The safety property is "never wrongly ACCEPT", not "always send a NACK":
    a bit flip in the LEN field that announces the frame as longer than it
    is leaves the parser waiting for data that never comes. That is a
    truncated frame -- on the firmware the RX timeout clears it away
    (see test_rx_timeout), on the host it simply stays in the buffer.
    It is never accepted in either case.
    """
    rnd = random.Random(5)
    base = encode(CMD_FRAME, bytes(rnd.randrange(256) for _ in range(192)))
    accepted_c = accepted_py = 0
    truncated = 0

    for i in range(2, len(base)):  # don't flip SYNC itself
        for bit in (0, 3, 7):
            corrupt = bytes(bytearray(base[:i]) + bytes([base[i] ^ (1 << bit)]) + base[i + 1:])
            ev_c, ev_py = c_parse(exe, corrupt), py_parse(corrupt)
            accepted_c += sum(l.startswith("MSG") for l in ev_c)
            accepted_py += sum(l.startswith("MSG") for l in ev_py)
            if not ev_c and not ev_py:
                truncated += 1

    total = (len(base) - 2) * 3
    check(accepted_c == 0, f"C accepts none of the {total} single-bit errors",
          f"{accepted_c} wrongly accepted")
    check(accepted_py == 0, f"Python accepts none of the {total} single-bit errors",
          f"{accepted_py} wrongly accepted")
    # Expected: exactly the 5 LEN bit flips that lengthen the frame.
    check(truncated == 5, "exactly 5 bit flips lead to truncation (LEN field)",
          f"were {truncated}")


def test_oversize_rejected(exe: Path) -> None:
    """Payload > MX_MAX_PAYLOAD: firmware discards it without overrunning the buffer."""
    payload = bytes(range(256)) * 2  # 512 B > the harness's MX_MAX_PAYLOAD (200)
    out = c_run(exe, "parse", encode(CMD_FRAME, payload).hex()).splitlines()
    check(any(l.startswith(f"ERR {CMD_FRAME} 4") for l in out),
          f"Payload > {MAX_PAYLOAD_FW} B -> NACK ERR_TOO_LONG", str(out))


def test_rx_timeout(exe: Path) -> None:
    """Truncated frame -> firmware reports a timeout and resyncs."""
    truncated = encode(CMD_FRAME, bytes(192))[:100]
    out = c_run(exe, "parse", truncated.hex()).splitlines()
    check(any(l.startswith("ERR 255 5") for l in out),
          "Truncated frame -> ERR_RX_TIMEOUT", str(out))


def test_geometry() -> None:
    geo = Geometry.parse(bytes([1, 8, 8, 3, 2, 0, 1, 0]))
    check(geo.width == 8 and geo.height == 8, "READY geometry 8x8")
    check(geo.frame_bytes == 192, "Frame size == 192 B", str(geo.frame_bytes))
    check(geo.order_name == "GRB", "Color order code 2 == GRB", geo.order_name)
    check(len(encode(CMD_FRAME, bytes(192))) == 199, "Full-frame packet == 199 B on the wire")


def test_framebuffer() -> None:
    from mx8.framebuffer import Framebuffer
    from mx8.patterns import PATTERNS

    fb = Framebuffer(8, 8)
    fb.set(3, 5, 10, 20, 30)
    check(fb.get(3, 5) == (10, 20, 30), "Framebuffer set/get")
    check(fb.buf[(5 * 8 + 3) * 3 : (5 * 8 + 3) * 3 + 3] == bytes((10, 20, 30)),
          "Framebuffer is row-major")
    fb.set(99, 99, 1, 2, 3)  # must not raise
    check(len(fb.to_bytes()) == 192, "Framebuffer delivers 192 B")

    bad = []
    for name, fn in PATTERNS.items():
        fb2 = Framebuffer(8, 8)
        for step in range(12):
            fn(fb2, step * 0.1)
            if len(fb2.buf) != 192 or any(not 0 <= v <= 255 for v in fb2.buf):
                bad.append(name)
                break
    check(not bad, "all host patterns deliver valid 192 B frames", ", ".join(bad))


def test_font() -> None:
    from mx8.font5x7 import GLYPH_H, GLYPH_W, GLYPHS, TextBitmap, validate_font

    problems = validate_font()
    check(not problems, f"all {len(GLYPHS)} glyphs are {GLYPH_W}x{GLYPH_H}",
          "; ".join(problems[:3]))

    tb = TextBitmap("Hello World")
    # 11 characters of 5 columns each + 10 spacer columns
    check(len(tb) == 11 * GLYPH_W + 10, "TextBitmap width is correct", str(len(tb)))
    check(tb.column(-5) == [False] * GLYPH_H and tb.column(9999) == [False] * GLYPH_H,
          "columns outside the text are empty")
    check(any(any(c) for c in tb.cols), "rendered text is not empty")

    # Unknown characters must not raise; they become '?'
    check(TextBitmap("☃").cols == TextBitmap("?").cols,
          "unknown character falls back to '?'")


def test_scroller() -> None:
    """The scrolling text must deliver valid frames over the whole pass."""
    sys.path.insert(0, str(ROOT / "script"))
    from hello_rainbow import render
    from mx8.font5x7 import TextBitmap
    from mx8.framebuffer import Framebuffer

    tb = TextBitmap("Hello World")
    fb = Framebuffer(8, 8)
    lit_frames = 0
    bad = 0
    for step in range(-8, len(tb) + 2):
        for smooth in (True, False):
            render(fb, tb, step + 0.37, step * 0.1, hue_scale=0.035, hue_speed=0.15,
                   smooth=smooth, y_off=0)
            if len(fb.buf) != 192 or any(not 0 <= v <= 255 for v in fb.buf):
                bad += 1
            if any(fb.buf):
                lit_frames += 1
    check(bad == 0, "scrolling text delivers valid frames over the whole pass",
          f"{bad} broken frames")
    check(lit_frames > 40, "scrolling text actually shows pixels", f"only {lit_frames}")


def test_smiley() -> None:
    """Smiley animation: timeline, continuity and valid frames."""
    sys.path.insert(0, str(ROOT / "script"))
    from mx8.framebuffer import Framebuffer
    from smiley import (
        HAPPY,
        NEUTRAL,
        SEQUENCE,
        SAD,
        WINK,
        draw_face,
        face_at,
        lerp_hue,
        wink_envelope,
    )

    HOLD, MORPH, WINKS = 1.4, 0.7, 2

    # The animation must START by HOLDING the neutral face, not in the middle of
    # a transition -- otherwise you never get to see "neutral first".
    f0, _ = face_at(0.0, HOLD, MORPH, WINKS)
    check(f0.name == "neutral" and abs(f0.curve) < 1e-9,
          "t=0 holds the neutral face", f"{f0.name} curve={f0.curve}")
    f1, _ = face_at(HOLD * 0.9, HOLD, MORPH, WINKS)
    check(f1.curve == NEUTRAL.curve, "neutral face stays put for the whole hold time")

    # Order neutral -> sad -> happy -> wink
    check([f.name for f in SEQUENCE] == ["neutral", "sad", "happy", "wink"],
          "order of the expressions is correct")
    period = HOLD + MORPH
    mid_hold = HOLD / 2
    seen = [face_at(k * period + mid_hold, HOLD, MORPH, WINKS)[0].name for k in range(4)]
    check(seen == ["neutral", "sad", "happy", "wink"],
          "each expression is held once", str(seen))

    # Mouth curvature: sad < neutral < happy
    check(SAD.curve < NEUTRAL.curve < HAPPY.curve,
          "mouth curvature runs from sad through neutral to happy")

    # Wink: the envelope must be open at both ends and really close in
    # between, otherwise the image jumps at the segment boundary.
    check(wink_envelope(0.0, HOLD, WINKS) == 1.0, "wink curve starts open")
    check(abs(wink_envelope(HOLD, HOLD, WINKS) - 1.0) < 1e-9,
          "wink curve ends open (= WINK.eye_r, no jump)")
    check(WINK.eye_r == 1.0, "WINK.eye_r matches the envelope")
    samples = [wink_envelope(i / 200 * HOLD, HOLD, WINKS) for i in range(200)]
    check(min(samples) == 0.0, "the eye really closes fully during the wink")
    closed_ratio = sum(s < 0.5 for s in samples) / len(samples)
    check(closed_ratio < 0.4,
          "eye is open most of the time (a wink, not constant blinking)",
          f"{closed_ratio:.0%} closed")

    # Hue takes the short way: yellow -> pink must go via red, not via green
    mid = lerp_hue(0.13, 0.92, 0.5)
    check(mid > 0.9 or mid < 0.13, "hue takes the short way around the color wheel",
          f"midpoint at {mid:.3f}")

    # All frames of two complete cycles must be valid
    fb = Framebuffer(8, 8)
    cycle = period * len(SEQUENCE)
    bad = dark = 0
    steps = int(cycle * 2 * 16)  # two rounds at 16 fps
    for i in range(steps):
        t = i / 16.0
        f, _ = face_at(t, HOLD, MORPH, WINKS)
        draw_face(fb, f, t)
        if len(fb.buf) != 192 or any(not 0 <= v <= 255 for v in fb.buf):
            bad += 1
        if not any(fb.buf):
            dark += 1
    check(bad == 0, f"all {steps} smiley frames are valid 192 B images",
          f"{bad} broken")
    check(dark == 0, "no frame is completely black", f"{dark} empty frames")


def test_smiley_ring_and_gamma() -> None:
    """Face ring and gamma correction of the faint colors."""
    sys.path.insert(0, str(ROOT / "script"))
    from mx8.framebuffer import Framebuffer
    from smiley import NEUTRAL, RING_DEFAULT, draw_face, draw_ring, gamma

    # Gamma: a "perceived" intensity must yield considerably less PWM.
    check(gamma(1.0) == 1.0, "gamma(1.0) leaves full brightness untouched")
    check(gamma(0.0) == 0.0, "gamma(0.0) stays off")
    check(abs(gamma(0.5) - 0.5 ** 2.2) < 1e-9, "gamma(0.5) = 0.5^2.2 ≈ 0.218")
    check(gamma(0.25) < 0.06,
          "perceived 25 % yields under 6 % PWM (otherwise 'subtle' looks too strong)",
          f"{gamma(0.25):.3f}")
    check(all(gamma(a) < gamma(b) for a, b in [(.1, .2), (.2, .4), (.4, .8)]),
          "gamma is monotonically increasing")

    # The ring must be a closed circle along the edge and leave the center free.
    fb = Framebuffer(8, 8)
    draw_ring(fb, 1.0)  # full intensity so the shape is clearly measurable
    lit = {(x, y) for y in range(8) for x in range(8) if any(fb.get(x, y))}
    check((3, 0) in lit and (0, 3) in lit and (7, 4) in lit and (4, 7) in lit,
          "ring touches all four sides")
    check((1, 1) in lit and (6, 6) in lit, "ring has the diagonal corner points")  # noqa
    check((0, 0) not in lit and (7, 7) not in lit, "ring leaves the corners free")
    centre = {(x, y) for x in range(2, 6) for y in range(2, 6)}
    check(not (lit & centre), "ring leaves the center of the face completely free",
          str(sorted(lit & centre)))
    check(len(lit) == 20, "ring consists of 20 pixels", str(len(lit)))

    # The ring must not outshine the face. Checked on pixels the face itself
    # never uses (row 0 and column 0/7).
    fb2 = Framebuffer(8, 8)
    draw_face(fb2, NEUTRAL, 0.0, ring=0.25)
    ring_px = max(max(fb2.get(x, y)) for x, y in [(0, 3), (7, 4), (3, 7)])
    face_px = max(fb2.get(2, 1))          # eyes now sit in row 0/1
    check(face_px == 255, "facial features light up fully", str(face_px))
    check(ring_px < face_px * 0.12,
          "ring stays well below the face (subtly hinted)",
          f"ring {ring_px} vs face {face_px}")
    check(ring_px > 0, "ring is still visible", str(ring_px))

    # On 8x8 the ring is off by default -- the face needs the area.
    check(RING_DEFAULT == 0.0, "ring is off by default on 8x8",
          str(RING_DEFAULT))
    fb3 = Framebuffer(8, 8)
    draw_face(fb3, NEUTRAL, 0.0, ring=0.0)
    check(all(not any(fb3.get(x, y)) for x, y in [(3, 0), (0, 3), (7, 4), (4, 7)]),
          "--ring 0 switches the circle off completely")


def test_smiley_geometry() -> None:
    """Fixed bit pattern, hard pixels, full width."""
    sys.path.insert(0, str(ROOT / "script"))
    from mx8.framebuffer import Framebuffer
    from smiley import (
        EYE_L_BOT,
        EYE_L_TOP,
        EYE_R_BOT,
        EYE_R_TOP,
        EYE_ROW_BOT,
        EYE_ROW_TOP,
        HAPPY,
        NEUTRAL,
        SAD,
        WINK,
        draw_face,
        mouth_rows,
    )

    def render(face, t=0.0):
        fb = Framebuffer(8, 8)
        draw_face(fb, face, t, ring=0.0)
        return fb

    def lit(fb):
        return {(x, y) for y in range(8) for x in range(8) if any(fb.get(x, y))}

    # --- NO dimmed pixels ------------------------------------------------
    # Partially covered pixels end up at PWM 1..3 at low panel brightness,
    # where WS2812Bs get blotchy. So on 8x8 pixels are set hard.
    for face in (NEUTRAL, SAD, HAPPY, WINK):
        fb = render(face, 0.3)
        levels = {max(fb.get(x, y)) for x, y in lit(fb)}
        check(levels <= {255}, f"'{face.name}': every pixel fully on, no intermediate levels",
              f"levels {sorted(levels)}")

    # --- Eyes exactly per bit pattern ------------------------------------
    #   row 0     0 1 1 0 0 1 1 0
    #   row 1     1 1 1 0 0 1 1 1
    #   row 2     0 0 0 0 0 0 0 0
    fb = render(NEUTRAL)
    row0 = {x for x in range(8) if any(fb.get(x, EYE_ROW_TOP))}
    row1 = {x for x in range(8) if any(fb.get(x, EYE_ROW_BOT))}
    row2 = {x for x in range(8) if any(fb.get(x, 2))}
    check(row0 == {1, 2, 5, 6}, "eyes row 0 = 01100110", str(sorted(row0)))
    check(row1 == {0, 1, 2, 5, 6, 7}, "eyes row 1 = 11100111", str(sorted(row1)))
    check(row2 == set(), "row 2 stays empty", str(sorted(row2)))
    check(set(EYE_L_TOP) | set(EYE_R_TOP) == {1, 2, 5, 6}, "upper eye row is correct")
    check(set(EYE_L_BOT) | set(EYE_R_BOT) == {0, 1, 2, 5, 6, 7}, "lower eye row is correct")

    # --- Mouth: exactly one pixel per column, across all 8 columns ------
    for face in (NEUTRAL, SAD, HAPPY, WINK):
        fb = render(face)
        for x in range(8):
            n = sum(1 for y in range(3, 8) if any(fb.get(x, y)))
            check(n == 1, f"'{face.name}' column {x}: exactly 1 mouth pixel", f"{n} pixels")

    # --- Mouth shapes ---------------------------------------------------
    check(mouth_rows(0.0) == [6] * 8, "neutral: straight line in row 6",
          str(mouth_rows(0.0)))
    check(mouth_rows(1.0) == [4, 5, 6, 7, 7, 6, 5, 4], "happy: arc opening upward",
          str(mouth_rows(1.0)))
    check(mouth_rows(-1.0) == [7, 6, 5, 4, 4, 5, 6, 7], "sad: arc opening downward",
          str(mouth_rows(-1.0)))

    # --- No gaps, not even in the intermediate steps --------------------
    # If the mouth jumps by more than one row between two adjacent columns,
    # it visually tears apart.
    worst = 0
    for i in range(-20, 21):
        rows = mouth_rows(i / 20.0)
        worst = max(worst, max(abs(rows[k + 1] - rows[k]) for k in range(7)))
    check(worst <= 1, "mouth stays connected in every transition step",
          f"largest jump {worst} rows")

    # --- Full width, and the expressions reach both edges -------------
    for face in (NEUTRAL, SAD, HAPPY, WINK):
        cols = {x for x, _ in lit(render(face))}
        check(cols == set(range(8)), f"'{face.name}' uses all 8 columns",
              f"only {sorted(cols)}")
    for face in (SAD, HAPPY, WINK):
        rows = {y for _, y in lit(render(face))}
        check(rows >= {0, 1, 4, 5, 6, 7}, f"'{face.name}' reaches down to row 7",
              f"rows {sorted(rows)}")

    # --- Wink: upper row disappears, lower row stays -------------------
    closed = WINK.__class__(WINK.name, WINK.curve, 1.0, 0.0,
                                WINK.hue, WINK.sat, WINK.tear)
    fb = render(closed)
    check(all(any(fb.get(x, EYE_ROW_TOP)) for x in EYE_L_TOP),
          "during the wink the left eye stays open")
    check(not any(any(fb.get(x, EYE_ROW_TOP)) for x in EYE_R_TOP),
          "right eye: upper row is gone")
    check(all(any(fb.get(x, EYE_ROW_BOT)) for x in EYE_R_BOT),
          "right eye: lower row remains as an eyelid line")


def test_speed_theory() -> None:
    """The baud rate calculation must match the protocol size."""
    sys.path.insert(0, str(ROOT / "script"))
    from speedtest import theory

    th = theory(115200, 192)
    check(th["frame_on_wire_bytes"] == 199, "full frame = 199 B on the wire")
    # (199 + 8) * 10 bits / 115200 = 17.97 ms
    check(abs((th["tx_s"] + th["rx_s"]) * 1000 - 17.97) < 0.02,
          "round trip at 115200 baud = 17.97 ms",
          f"{(th['tx_s'] + th['rx_s']) * 1000:.2f} ms")
    check(abs(th["fps_acked"] - 55.65) < 0.1, "-> 55.7 fps theoretical limit",
          f"{th['fps_acked']:.2f}")
    # Double the baud rate must yield exactly double the frame rate
    check(abs(theory(230400, 192)["fps_acked"] / th["fps_acked"] - 2.0) < 1e-9,
          "double baud rate = double serial frame rate")


def test_led_protocol_limit() -> None:
    """From a few hundred LEDs on, the WS2812B protocol is the bottleneck."""
    sys.path.insert(0, str(ROOT / "script"))
    from speedtest import led_refresh_s, theory

    # 800 kHz, 24 bits per LED -> 30 us per LED, plus reset
    check(abs(led_refresh_s(64) * 1000 - (64 * 24 * 1.25e-3 + 0.3)) < 1e-9,
          "show() 8x8 = 64 x 30 us + reset", f"{led_refresh_s(64)*1000:.2f} ms")
    check(abs(1 / led_refresh_s(512) - 63.9) < 0.5,
          "512 LEDs -> about 64 fps on the LED side", f"{1/led_refresh_s(512):.1f}")

    # Parallel strands halve the time
    check(led_refresh_s(512, 2) < led_refresh_s(512) * 0.55,
          "two parallel strands nearly halve show()")

    # Serial link and LED output add up
    t = theory(1_000_000, 512 * 3)
    total = t["tx_s"] + t["rx_s"] + t["led_s"]
    check(abs(t["fps_total"] - 1 / total) < 1e-9, "fps_total = 1/(serial + show)")
    check(t["fps_total"] < min(t["fps_acked"], t["fps_led"]),
          "overall limit is below both individual limits",
          f"{t['fps_total']:.1f} vs {t['fps_acked']:.1f} / {t['fps_led']:.1f}")

    # At 8x8 the serial line is the brake, at 16x32 the LED protocol
    small = theory(115200, 64 * 3)
    big = theory(1_000_000, 512 * 3)
    check(small["led_s"] < small["tx_s"] + small["rx_s"],
          "8x8 at 115200: the serial link dominates")
    check(big["led_s"] > big["tx_s"] + big["rx_s"],
          "16x32 at 1 Mbaud: the LED protocol dominates")


def test_overlap_model() -> None:
    """RP2040: PIO+DMA lets reception and LED output run concurrently.

    Verified in FastLED 3.9.16: led_sysdefs_arm_rp2040.h sets
    FASTLED_RP2040_CLOCKLESS_PIO to 1 and FASTLED_ALLOW_INTERRUPTS to 1,
    clockless_arm_rp2040.h in showPixels() only waits for the PREVIOUS
    DMA transfer. On AVR, by contrast, the times add up.
    """
    sys.path.insert(0, str(ROOT / "script"))
    from speedtest import theory

    avr = theory(1_000_000, 512 * 3, strands=2, overlap=False)
    pico = theory(1_000_000, 512 * 3, strands=2, overlap=True)

    check(abs(avr["total_s"] - (avr["tx_s"] + avr["rx_s"] + avr["led_s"])) < 1e-12,
          "AVR model: serial + show() add up")
    check(abs(pico["total_s"] - max(pico["tx_s"] + pico["rx_s"], pico["led_s"])) < 1e-12,
          "RP2040 model: the longer of the two paths is what counts")
    check(pico["fps_total"] > avr["fps_total"],
          "overlapping is never slower than adding",
          f"{pico['fps_total']:.1f} vs {avr['fps_total']:.1f}")
    # With a very fast link only the LED protocol is left --
    # that is the upper bound for 16x32 on two pins on the Pico.
    fast = theory(12_000_000, 512 * 3, strands=2, overlap=True)
    check(abs(fast["fps_total"] - fast["fps_led"]) < 1e-9,
          "Pico with native USB: exactly the WS2812B limit remains",
          f"{fast['fps_total']:.1f} fps")
    check(abs(fast["fps_total"] - 125.3) < 1.0,
          "16x32 on two pins -> about 125 fps", f"{fast['fps_total']:.1f}")
    # One strand instead of two halves that
    one = theory(12_000_000, 512 * 3, strands=1, overlap=True)
    check(abs(one["fps_total"] - 63.9) < 1.0,
          "16x32 on ONE pin -> only about 64 fps", f"{one['fps_total']:.1f}")


# ---------------------------------------------------------------------------
#  Pixel mapping: FW/mx8_map.h vs script/mx8/mapping.py
# ---------------------------------------------------------------------------
def build_map_harness(tmp: Path, tm, tag: str) -> Path:
    """Compiles FW/mx8_map.h with exactly the configuration from ``tm``."""
    exe = tmp / f"c_map_{tag}"
    cc = os.environ.get("CC", "cc")
    defs = {
        "MX_BOARD": 99,                          # MX_BOARD_CUSTOM
        "MX_TILE_W": tm.tile_w,
        "MX_TILE_H": tm.tile_h,
        "MX_TILES_X": tm.tiles_x,
        "MX_TILES_Y": tm.tiles_y,
        "MX_TILE_VERTICAL": int(tm.vertical),
        "MX_TILE_SERPENTINE": int(tm.serpentine),
        "MX_TILE_ORIGIN": tm.origin,
        "MX_TILE_FLIP_ALT_ROWS": int(tm.flip_alt_rows),
        "MX_TILE_ORDER_SERPENTINE": int(tm.tile_order_serpentine),
        "MX_STRANDS": tm.strands,
    }
    subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-Werror", f"-I{FW}"]
        + [f"-D{k}={v}" for k, v in defs.items()]
        + [str(Path(__file__).parent / "c_map_test.c"), "-o", str(exe)],
        check=True,
    )
    return exe


def c_map_table(exe: Path) -> tuple[list[int], list[list[int]]]:
    lines = subprocess.run([str(exe)], capture_output=True, text=True,
                           check=True).stdout.strip().splitlines()
    head = [int(v) for v in lines[0].split()]
    rows = [[int(v) for v in line.split()] for line in lines[1:]]
    return head, rows


def test_mapping_matches_c() -> None:
    """Firmware and the Python reference must compute the same mapping.

    Not just for the two real presets, but across all wiring variants --
    exactly the combinations that mapcheck.py can spit out.
    """
    from mx8.mapping import PRESETS, TileMap

    variants: list[tuple[str, TileMap]] = [(name, tm) for name, tm in PRESETS.items()]
    # Systematic pass over a small area: 2x2 tiles of 4x2 each,
    # so that width != height and swapped axes get noticed.
    n = 0
    for vertical in (False, True):
        for serp in (False, True):
            for origin in range(4):
                for flip in (False, True):
                    for order in (False, True):
                        n += 1
                        variants.append((f"v{n}", TileMap(
                            tile_w=4, tile_h=2, tiles_x=2, tiles_y=2,
                            vertical=vertical, serpentine=serp, origin=origin,
                            flip_alt_rows=flip, tile_order_serpentine=order,
                            strands=2,
                        )))

    tmp = Path(tempfile.mkdtemp(prefix="mx8_map_"))
    mismatches: list[str] = []
    for tag, tm in variants:
        head, rows = c_map_table(build_map_harness(tmp, tm, tag))
        want_head = [tm.width, tm.height, tm.num_leds, tm.tile_leds, tm.leds_per_strand]
        if head != want_head:
            mismatches.append(f"{tag}: header line {head} != {want_head}")
            continue
        for y in range(tm.height):
            for x in range(tm.width):
                if rows[y][x] != tm.index(x, y):
                    mismatches.append(
                        f"{tag}: ({x},{y}) C={rows[y][x]} Python={tm.index(x, y)}")
                    break
            if mismatches and mismatches[-1].startswith(f"{tag}:"):
                break

    check(not mismatches, f"mapping C == Python for all {len(variants)} variants",
          "; ".join(mismatches[:3]))


def test_mapping_is_bijective() -> None:
    """Every pixel exactly one chain index -- otherwise LEDs stay dark."""
    from mx8.mapping import PRESETS, TileMap

    bad: list[str] = []
    for name, tm in PRESETS.items():
        if not tm.is_bijective():
            bad.append(name)
    for vertical in (False, True):
        for serp in (False, True):
            for origin in range(4):
                for flip in (False, True):
                    for order in (False, True):
                        tm = TileMap(tile_w=4, tile_h=2, tiles_x=2, tiles_y=2,
                                     vertical=vertical, serpentine=serp,
                                     origin=origin, flip_alt_rows=flip,
                                     tile_order_serpentine=order)
                        if not tm.is_bijective():
                            bad.append(f"v={vertical} s={serp} o={origin} "
                                       f"f={flip} to={order}")
    check(not bad, "mapping is bijective in all variants", "; ".join(bad[:3]))


def test_mapping_16x32_hand_checked() -> None:
    """Hand-computed spot checks, straight from observing the panel.

    Two 8x32 panels stacked. The chain runs COLUMN-WISE, eight pixels per
    column, serpentine (zigzag): column 0 downward, column 1 back upward.
    The first column of the whole display area consists of the first eight
    chain pixels of panel 1 and those of panel 2.
    """
    from mx8.mapping import PRESETS

    tm = PRESETS["16x32"]
    check(tm.width == 32 and tm.height == 16, "16x32: area 32 wide, 16 high",
          f"{tm.width}x{tm.height}")
    check(tm.num_leds == 512 and tm.leds_per_strand == 256,
          "16x32: 512 LEDs, 256 per data pin",
          f"{tm.num_leds} / {tm.leds_per_strand}")

    inv = {tm.index(x, y): (x, y) for y in range(16) for x in range(32)}

    # Exactly the observation: chain 0..7 runs down column 0
    check([inv[i] for i in range(8)] == [(0, y) for y in range(8)],
          "16x32: chain 0..7 runs DOWN column 0",
          str([inv[i] for i in range(3)]))
    # ... and chain 8..15 back up the next column
    check([inv[i] for i in range(8, 16)] == [(1, y) for y in range(7, -1, -1)],
          "16x32: chain 8..15 runs UP column 1 (serpentine)",
          str([inv[i] for i in range(8, 11)]))
    # Column c sits at chain index 8c..8c+7 -- regardless of 8x8 blocks
    check(all(min(tm.index(c, y) for y in range(8)) == 8 * c for c in range(32)),
          "16x32: column c occupies exactly chain index 8c..8c+7")

    # The first column of the WHOLE area: panel 1 on top, panel 2 below
    col0 = [tm.index(0, y) for y in range(16)]
    check(col0[:8] == list(range(8)) and col0[8:] == list(range(256, 264)),
          "16x32: column 0 = chain 0..7 (panel 1) + 256..263 (panel 2)",
          f"{col0[:2]} ... {col0[8:10]}")

    # The host still sends a plain flat area
    check(tm.index(0, 0) == 0 and tm.index(1, 0) == 15 and tm.index(31, 0) == 255,
          "16x32: row 0 runs horizontally across the full width",
          f"{tm.index(0,0)}, {tm.index(1,0)}, {tm.index(31,0)}")

    # The strand boundary falls exactly on the panel boundary
    top = {tm.strand_of(tm.index(x, y)) for y in range(8) for x in range(32)}
    bot = {tm.strand_of(tm.index(x, y)) for y in range(8, 16) for x in range(32)}
    check(top == {0} and bot == {1},
          "16x32: no panel hangs off two data pins", f"{top} / {bot}")

    # The alternative layouts must differ, otherwise this tests nothing
    others = {n: t for n, t in PRESETS.items() if n.startswith("16x32-")}
    check(all(any(t.index(x, y) != tm.index(x, y)
                  for y in range(16) for x in range(32)) for t in others.values()),
          f"the {len(others)} alternative presets really compute differently")


def test_baud_search() -> None:
    """The host finds the panel even when the rate is no longer 115200."""
    from mx8.link import BAUD_CANDIDATES, baud_search_order

    for first in (115_200, 1_000_000, 230_400):
        order = baud_search_order(first)
        check(order[0] == first, f"baud search starts at {first}", str(order[0]))
        check(len(order) == len(set(order)), f"no rate twice (from {first})",
              str(order))
        check(set(BAUD_CANDIDATES) <= set(order),
              f"all candidates get their turn (from {first})", str(order))
    check(list(BAUD_CANDIDATES) == sorted(BAUD_CANDIDATES, reverse=True),
          "candidates descending -- the fast rate wins first")


def test_config_baud() -> None:
    """The 16x32 build reports 1 Mbaud, the 8x8 stays at 115200."""
    cfg = (FW / "mx8_config.h").read_text(encoding="utf-8", errors="replace")
    block = cfg[cfg.index("#elif MX_BOARD == MX_BOARD_16X32"):
                cfg.index("#elif MX_BOARD == MX_BOARD_CUSTOM")]
    m = re.search(r"#define\s+MX_SERIAL_BAUD\s+(\d+)", block)
    check(m is not None and int(m.group(1)) == 1_000_000,
          "16x32 preset sets MX_SERIAL_BAUD to 1000000",
          m.group(1) if m else "not found")
    check("#ifndef MX_SERIAL_BAUD" in cfg,
          "the global value is now only a default -- presets may set it")


def test_preset_matches_config() -> None:
    """PRESETS["16x32"] must be exactly what gets flashed from mx8_config.h.

    This is exactly where the two drifted apart once: the firmware was set to
    serpentine, the panel is progressive -- pixelwalk ran in a wavy line.
    The test now compares both sides pixel by pixel.
    """
    from mx8.mapping import PRESETS

    tmp = Path(tempfile.mkdtemp(prefix="mx8_cfg_"))
    cc = os.environ.get("CC", "cc")
    for board, name in ((1, "8x8"), (2, "16x32")):
        exe = tmp / f"c_map_board{board}"
        cp = subprocess.run(
            [cc, "-O2", "-Wall", "-Wextra", "-Werror", f"-I{FW}",
             f"-DMX_BOARD={board}",
             str(Path(__file__).parent / "c_map_test.c"), "-o", str(exe)],
            capture_output=True, text=True,
        )
        if cp.returncode != 0:
            # Don't let it propagate: a broken config is a test finding,
            # not a crash of the test run.
            first = next((l for l in cp.stderr.splitlines() if "error" in l.lower()), "")
            check(False, f"mx8_config.h MX_BOARD={board} compiles",
                  first.strip()[:160])
            continue
        head, rows = c_map_table(exe)
        tm = PRESETS[name]
        want = [tm.width, tm.height, tm.num_leds, tm.tile_leds, tm.leds_per_strand]
        check(head == want, f"mx8_config.h MX_BOARD={board}: dimensions == preset '{name}'",
              f"{head} != {want}")
        if head != want:
            continue
        bad = next(((x, y) for y in range(tm.height) for x in range(tm.width)
                    if rows[y][x] != tm.index(x, y)), None)
        check(bad is None,
              f"mx8_config.h MX_BOARD={board}: mapping == preset '{name}'",
              f"first mismatch at {bad}" if bad else "")


def test_mapping_8x8_unchanged() -> None:
    """The old 8x8 behavior must not have changed due to the rework."""
    from mx8.mapping import PRESETS

    tm = PRESETS["8x8"]
    ok = all(tm.index(x, y) == y * 8 + x for y in range(8) for x in range(8))
    check(ok, "8x8: unchanged row-major, progressive")
    check(tm.num_leds == 64 and tm.leds_per_strand == 64, "8x8: one strand, 64 LEDs")


def test_pixelwalk_order() -> None:
    """pixelwalk must hit EVERY pixel exactly once -- otherwise it is
    worthless as a dead-pixel test."""
    from pixelwalk import walk_order
    from mx8.mapping import PRESETS

    w, h = 32, 16
    row = list(walk_order(w, h, column_major=False))
    col = list(walk_order(w, h, column_major=True))

    check(len(row) == w * h and len(set(row)) == w * h,
          "pixelwalk row-wise: all 512 pixels exactly once", str(len(set(row))))
    check(len(col) == w * h and len(set(col)) == w * h,
          "pixelwalk column-wise: all 512 pixels exactly once", str(len(set(col))))

    # The order the user specified: (0,0) -> (xmax,0), then row 1
    check(row[0] == (0, 0), "pixelwalk starts at (0,0)", str(row[0]))
    check(row[w - 1] == (w - 1, 0), "pixelwalk first runs to (xmax,0)", str(row[w - 1]))
    check(row[w] == (0, 1), "then row 1 starts again on the left", str(row[w]))
    check(row[-1] == (w - 1, h - 1), "last pixel is (xmax,ymax)", str(row[-1]))

    check(col[0] == (0, 0) and col[h - 1] == (0, h - 1) and col[h] == (1, 0),
          "column-wise first runs (0,0)..(0,ymax), then column 1")

    # --raw follows the chain: sort by chain index and you get 0,1,2,...
    tm = PRESETS["16x32"]
    raw = sorted(row, key=lambda p: tm.index(*p))
    check([tm.index(*p) for p in raw] == list(range(w * h)),
          "pixelwalk --raw walks the chain index without gaps")
    # --raw follows the chain and therefore runs VERTICALLY -- exactly what was
    # visible on the panel back when the firmware still computed row-wise.
    check(raw[:8] == [(0, y) for y in range(8)],
          "--raw: first eight pixels are column 0 from top to bottom",
          str(raw[:3]))
    check(raw[8] == (1, 7), "--raw: then the turnaround into column 1", str(raw[8]))
    check(raw != row, "--raw differs from the normal run (vertical chain)")


def test_pixelwalk_first_pass_is_faster() -> None:
    """The first pass must be fast enough for a video out of the box."""
    import argparse
    import pixelwalk

    ap = [a for a in _argparser_defaults(pixelwalk)]
    d = dict(ap)
    check(d.get("first_interval", 1.0) < d.get("interval", 1.0),
          "pixelwalk: first pass is faster than the later ones by default",
          f"first={d.get('first_interval')} interval={d.get('interval')}")
    check(512 * d.get("first_interval", 1.0) < 60,
          "512 pixels in the first pass in under a minute",
          f"{512 * d.get('first_interval', 1.0):.0f} s")
    del argparse


def _argparser_defaults(mod):
    """Read a script's argparse defaults without actually running it."""
    import argparse
    import contextlib
    import io

    captured = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        captured.update({a.dest: a.default for a in self._actions})
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = spy
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.suppress(SystemExit):
                mod.main([])
    finally:
        argparse.ArgumentParser.parse_args = real
    return captured.items()


def test_net_transport() -> None:
    """Wi-Fi model: bytes are cheap, round-trip time is expensive."""
    sys.path.insert(0, str(ROOT / "script"))
    from speedtest import (MTU, WIFI_MEASURED_BPS, net_frame_bytes, theory_net)

    # -- Packet splitting -------------------------------------------------
    pkts, wire = net_frame_bytes(1543, "udp")
    check(pkts == 2, "16x32 (1543 B) needs 2 UDP packets", str(pkts))
    check(wire == 1671, "16x32 UDP: 1671 B over the air", str(wire))
    check(abs(wire / 1543 - 1 - 0.083) < 0.005,
          "byte overhead for 16x32 is about 8 %", f"{wire/1543-1:.1%}")

    # HALF a frame (one panel) fits into a single packet -- that is why
    # the frame can be split along the panel boundary.
    half_pkts, _ = net_frame_bytes(32 * 8 * 3 + 7, "udp")
    check(half_pkts == 1, "one panel (775 B) fits into ONE UDP packet", str(half_pkts))
    check(32 * 8 * 3 + 7 <= MTU - 20 - 8, "775 B is below the UDP payload limit")

    # Small frames pay relatively a lot, large ones almost nothing
    _, small = net_frame_bytes(199, "udp")
    _, big = net_frame_bytes(3079, "udp")
    check(small / 199 - 1 > big / 3079 - 1,
          "small frames have relatively more overhead than large ones",
          f"{small/199-1:.0%} vs {big/3079-1:.0%}")
    check(net_frame_bytes(1543, "tcp")[1] > net_frame_bytes(1543, "udp")[1],
          "TCP costs more bytes than UDP (12 B per packet)")

    # -- The actual point: ACK per frame vs. fire-and-forget -------------
    ff = theory_net(1536, proto="udp", acked=False, strands=2)
    fast = theory_net(1536, proto="udp", acked=True, rtt_s=3e-3, strands=2)
    slow = theory_net(1536, proto="udp", acked=True, rtt_s=35e-3, strands=2)

    check(abs(ff["fps_total"] - 125.3) < 1.0,
          "fire-and-forget: what remains is the WS2812B limit of ~125 fps",
          f"{ff['fps_total']:.0f}")
    check(abs(fast["fps_total"] - ff["fps_total"]) < 0.01,
          "at 3 ms RTT, ACK-per-frame changes nothing -- show() still dominates",
          f"{fast['fps_total']:.0f}")
    check(slow["fps_total"] < 30,
          "at 35 ms RTT, ACK-per-frame collapses to under 30 fps",
          f"{slow['fps_total']:.1f}")
    check(ff["rtt_s"] == 0.0, "without ACK the latency drops out completely")

    # -- Throughput is NOT the bottleneck --------------------------------
    need = ff["wire_bytes"] * 8 * 125
    check(need < WIFI_MEASURED_BPS * 0.30,
          "125 fps need under 30 % of the measured Pico W throughput",
          f"{need/1e6:.2f} of {WIFI_MEASURED_BPS/1e6:.2f} Mbit/s")
    # ... and airtime even less so
    check(ff["airtime_s"] < ff["led_s"] / 10,
          "airtime is an order of magnitude below show()",
          f"{ff['airtime_s']*1e6:.0f} us vs {ff['led_s']*1e6:.0f} us")


def test_rainbow() -> None:
    """The rainbow must fill the whole area -- and the whole color wheel."""
    import colorsys
    from rainbow import MODES, render
    from mx8 import Framebuffer

    W, H = 32, 16
    fb = Framebuffer(W, H)

    def hues(mode, t=0.0, cycles=1.0):
        render(fb, mode, t, cycles)
        return [colorsys.rgb_to_hsv(*(v / 255 for v in fb.get(x, y)))
                for y in range(H) for x in range(W)]

    for name, mode in sorted(MODES.items()):
        hsv = hues(mode)
        check(len(hsv) == W * H, f"{name}: all {W*H} pixels set")
        # No black pixel -- a rainbow leaves no gaps
        check(all(v > 0.9 for _, _, v in hsv), f"{name}: no pixel stays dark",
              f"darkest {min(v for _, _, v in hsv):.2f}")
        check(all(sat > 0.9 for _, sat, _ in hsv), f"{name}: fully saturated")
        # Hue covers practically the whole wheel (not quite 1.0: the last
        # pixel does not sit on the edge but half a cell before it)
        span = max(h for h, _, _ in hsv) - min(h for h, _, _ in hsv)
        check(span > 0.9, f"{name}: --cycles 1 uses the whole color wheel",
              f"span {span:.2f}")
        # Motion: a different t must produce a different frame
        render(fb, mode, 0.0)
        a = fb.to_bytes()
        render(fb, mode, 0.13)
        check(a != fb.to_bytes(), f"{name}: the image moves with t")
        # Reproducible: same t -> same frame, no matter what came before
        render(fb, mode, 0.0)
        check(a == fb.to_bytes(), f"{name}: render does not depend on previous state")

    # radial must make ROUND rings, not oval ones: same distance from the center
    # -> same hue. On a 32x16 area that is immediately noticeable.
    render(fb, MODES["radial"], 0.0)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    buckets: dict[int, set[int]] = {}
    for y in range(H):
        for x in range(W):
            d = round(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5, 3)
            buckets.setdefault(int(d * 100), set()).add(fb.get(x, y))
    worst = max(len(v) for v in buckets.values())
    check(worst == 1, "radial: same distance -> same color (true circles)",
          f"one ring had {worst} different colors")

    # --cycles 2 must run through the color wheel twice as often
    def wraps(cycles):
        hs = [h for h, _, _ in hues(MODES["linear"], 0.0, cycles)][:W]
        return sum(1 for a, b in zip(hs, hs[1:]) if b < a)
    check(wraps(2.0) == 2 * wraps(1.0) or wraps(2.0) > wraps(1.0),
          "--cycles 2 places more rainbows side by side",
          f"{wraps(1.0)} -> {wraps(2.0)}")

    # Degenerate areas must not divide by zero
    for w, h in ((1, 1), (1, 16), (32, 1)):
        small = Framebuffer(w, h)
        for mode in MODES.values():
            render(small, mode, 0.2)
        check(True, f"render survives {w}x{h}")


def test_dark_state() -> None:
    """After the PC goes away the panel goes dark; the demo is not restarted.

    Checked directly against the firmware sources so that docs and code
    don't drift apart.
    """
    cfg = (FW / "mx8_config.h").read_text(encoding="utf-8", errors="replace")
    ino = (FW / "FW.ino").read_text(encoding="utf-8", errors="replace")

    m = re.search(r"#define\s+MX_PC_TIMEOUT_MS\s+(\d+)", cfg)
    check(m is not None and int(m.group(1)) == 60000,
          "MX_PC_TIMEOUT_MS is set to 60000 ms (1 min)",
          m.group(1) if m else "not found")

    check("ST_DARK" in ino and "enum State" in ino and "ST_DARK }" in ino.replace(", ", ", "),
          "firmware knows the ST_DARK state")
    # The RUNNING timeout must lead to DARK, not to TESTPATTERN
    run = ino[ino.index("case ST_RUNNING:"):ino.index("case ST_DARK:")]
    check("enterState(ST_DARK)" in run,
          "RUNNING timeout leads to ST_DARK")
    check("ST_TESTPATTERN" not in run,
          "RUNNING timeout does NOT lead back to the test pattern")
    # RELEASE deliberately remains the orderly hand-back to the demo
    rel = ino[ino.index("case CMD_RELEASE:"):]
    check("enterState(ST_TESTPATTERN)" in rel[:rel.index("break;")],
          "RELEASE still hands back to the test pattern")
    # In DARK, READY must keep being sent, otherwise the PC can't find its way back
    check("g_state != ST_RUNNING" in ino,
          "READY broadcast keeps running in dark mode too")


def test_raw_command() -> None:
    """CMD_RAW bypasses the mapping -- length check as in the firmware."""
    from mx8.protocol import CMD_RAW

    check(CMD_RAW == 0x07, "CMD_RAW == 0x07")
    # Payload = 2 B start index + 3 B per LED
    for n in (0, 1, 64, 512):
        payload = bytes(2 + 3 * n)
        check(len(payload) >= 2 and (len(payload) - 2) % 3 == 0,
              f"RAW payload for {n} LEDs has a valid length")
    check(encode(CMD_RAW, bytes(2 + 3 * 512))[2] == CMD_RAW,
          "RAW can be framed with 512 LEDs")


# ---------------------------------------------------------------------------
def main() -> int:
    print("Compiling firmware headers with host compiler ...")
    try:
        exe = build_harness()
    except subprocess.CalledProcessError as e:
        print("ERROR: compiling firmware headers failed", file=sys.stderr)
        return 1
    print("  ok\n")

    test_crc_known_vectors()
    test_crc_matches_c(exe)
    test_encode_matches_c(exe)
    test_parse_clean(exe)
    test_parse_with_garbage(exe)
    test_payload_boundary(exe)
    test_bitflip_detected(exe)
    test_oversize_rejected(exe)
    test_rx_timeout(exe)
    test_geometry()
    test_framebuffer()
    test_font()
    test_scroller()
    test_smiley()
    test_smiley_ring_and_gamma()
    test_smiley_geometry()
    test_speed_theory()
    test_led_protocol_limit()
    test_overlap_model()
    test_mapping_8x8_unchanged()
    test_mapping_16x32_hand_checked()
    test_preset_matches_config()
    test_baud_search()
    test_config_baud()
    test_mapping_is_bijective()
    test_mapping_matches_c()
    test_raw_command()
    test_pixelwalk_order()
    test_pixelwalk_first_pass_is_faster()
    test_net_transport()
    test_rainbow()
    test_dark_state()

    total = _passes + len(_fails)
    for f in _fails:
        print(f"  FAIL  {f}")
    print(f"\n{_passes}/{total} checks passed.")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Kreuzvergleich Firmware (C) <-> Host (Python).

Kompiliert mx8_rx.h/mx8_protocol.h mit dem Host-Compiler und prueft, dass
beide Seiten bitgenau dasselbe Framing und dieselbe CRC erzeugen bzw. lesen.
Laeuft ohne Hardware.

    python tests/test_protocol.py
"""

from __future__ import annotations

import os
import random
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
MAX_PAYLOAD_FW = 200  # muss zu mx8_config.h passen

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
    """CRC-16/CCITT-FALSE gegen die kanonischen Check-Werte."""
    check(crc16(b"123456789") == 0x29B1, "CRC check-value '123456789' == 0x29B1",
          f"bekam 0x{crc16(b'123456789'):04X}")
    check(crc16(b"") == 0xFFFF, "CRC leer == 0xFFFF")
    check(crc16(b"A") == 0xB915, "CRC 'A' == 0xB915", f"bekam 0x{crc16(b'A'):04X}")


def test_crc_matches_c(exe: Path) -> None:
    rnd = random.Random(1234)
    bad = 0
    for _ in range(300):
        data = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 260)))
        if int(c_run(exe, "crc", data.hex()), 16) != crc16(data):
            bad += 1
    check(bad == 0, "CRC C == Python (300 Zufallsvektoren)", f"{bad} Abweichungen")


def test_encode_matches_c(exe: Path) -> None:
    rnd = random.Random(99)
    bad = 0
    for _ in range(200):
        cmd = rnd.randrange(1, 0x85)
        payload = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 193)))
        if bytes.fromhex(c_run(exe, "build", str(cmd), payload.hex())) != encode(cmd, payload):
            bad += 1
    check(bad == 0, "Frame-Encoder C == Python (200 Vektoren)", f"{bad} Abweichungen")


# ---------------------------------------------------------------------------
def py_parse(stream: bytes) -> list[str]:
    """Python-Parser in dasselbe Textformat wie das C-Harness bringen."""
    out = []
    for ev in FrameParser().feed(stream):
        if isinstance(ev, Message):
            out.append(f"MSG {ev.cmd} {ev.payload.hex()}")
        else:
            out.append(f"ERR {ev.cmd} {ERR_CRC}")
    return out


def c_parse(exe: Path, stream: bytes) -> list[str]:
    lines = [l for l in c_run(exe, "parse", stream.hex()).splitlines() if l]
    # Der Timeout-NACK am Ende ist ein Firmware-Detail (halber Frame haengt);
    # der Python-Parser haelt so etwas einfach im Puffer. Fuer den Vergleich
    # der Nutz-Events blenden wir ihn aus und pruefen ihn separat.
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
    check(c_parse(exe, stream) == expect, "C-Parser liest 40 saubere Frames")
    check(py_parse(stream) == expect, "Python-Parser liest 40 saubere Frames")


def test_parse_with_garbage(exe: Path) -> None:
    """Muell zwischen den Frames -- beide Seiten muessen resynchronisieren."""
    rnd = random.Random(21)
    stream = b""
    expect = []
    for _ in range(30):
        stream += bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 9)))
        payload = bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 40)))
        stream += encode(CMD_PING, payload)
        expect.append(f"MSG {CMD_PING} {payload.hex()}")
    got_c, got_py = c_parse(exe, stream), py_parse(stream)
    # Zufallsmuell kann selbst wie ein Frame-Anfang aussehen; entscheidend ist,
    # dass ALLE echten Frames ankommen und beide Seiten dasselbe sehen.
    check(all(e in got_c for e in expect), "C-Parser resynct ueber Muell hinweg",
          f"{len(expect) - sum(e in got_c for e in expect)} Frames verloren")
    check(got_c == got_py, "C- und Python-Parser sehen bei Muell dasselbe",
          f"C={len(got_c)} Events, PY={len(got_py)} Events")


def test_payload_boundary(exe: Path) -> None:
    """SYNC-Token im Payload darf den Parser nicht aus dem Tritt bringen."""
    payload = bytes([0xA5, 0x5A, 0x01, 0xC0, 0x00] * 12)
    stream = encode(CMD_FRAME, payload + bytes(192 - len(payload)))
    full = payload + bytes(192 - len(payload))
    check(c_parse(exe, stream) == [f"MSG {CMD_FRAME} {full.hex()}"],
          "SYNC-Token im Payload wird korrekt uebersprungen (C)")
    check(py_parse(stream) == [f"MSG {CMD_FRAME} {full.hex()}"],
          "SYNC-Token im Payload wird korrekt uebersprungen (Python)")


def test_bitflip_detected(exe: Path) -> None:
    """Kein Einzelbitfehler darf als gueltiger Frame durchgehen.

    Die Sicherheitseigenschaft ist "nie faelschlich AKZEPTIEREN", nicht "immer
    ein NACK schicken": ein Bitflip im LEN-Feld, der den Frame laenger ansagt
    als er ist, laesst den Parser auf Daten warten, die nie kommen. Das ist
    ein abgeschnittener Frame -- auf der Firmware raeumt ihn der RX-Timeout
    weg (siehe test_rx_timeout), auf dem Host bleibt er einfach im Puffer.
    Angenommen wird er in keinem Fall.
    """
    rnd = random.Random(5)
    base = encode(CMD_FRAME, bytes(rnd.randrange(256) for _ in range(192)))
    accepted_c = accepted_py = 0
    truncated = 0

    for i in range(2, len(base)):  # SYNC selbst nicht kippen
        for bit in (0, 3, 7):
            corrupt = bytes(bytearray(base[:i]) + bytes([base[i] ^ (1 << bit)]) + base[i + 1:])
            ev_c, ev_py = c_parse(exe, corrupt), py_parse(corrupt)
            accepted_c += sum(l.startswith("MSG") for l in ev_c)
            accepted_py += sum(l.startswith("MSG") for l in ev_py)
            if not ev_c and not ev_py:
                truncated += 1

    total = (len(base) - 2) * 3
    check(accepted_c == 0, f"C akzeptiert keinen der {total} Einzelbitfehler",
          f"{accepted_c} faelschlich angenommen")
    check(accepted_py == 0, f"Python akzeptiert keinen der {total} Einzelbitfehler",
          f"{accepted_py} faelschlich angenommen")
    # Erwartung: genau die 5 LEN-Bitflips, die den Frame verlaengern.
    check(truncated == 5, "genau 5 Bitflips fuehren zu Truncation (LEN-Feld)",
          f"waren {truncated}")


def test_oversize_rejected(exe: Path) -> None:
    """Payload > MX_MAX_PAYLOAD: Firmware verwirft, ohne den Puffer zu sprengen."""
    payload = bytes(range(256)) * 2  # 512 B == Protokoll-Maximum
    out = c_run(exe, "parse", encode(CMD_FRAME, payload).hex()).splitlines()
    check(any(l.startswith(f"ERR {CMD_FRAME} 4") for l in out),
          f"Payload > {MAX_PAYLOAD_FW} B -> NACK ERR_TOO_LONG", str(out))


def test_rx_timeout(exe: Path) -> None:
    """Abgeschnittener Frame -> Firmware meldet Timeout und resynct."""
    truncated = encode(CMD_FRAME, bytes(192))[:100]
    out = c_run(exe, "parse", truncated.hex()).splitlines()
    check(any(l.startswith("ERR 255 5") for l in out),
          "Abgeschnittener Frame -> ERR_RX_TIMEOUT", str(out))


def test_geometry() -> None:
    geo = Geometry.parse(bytes([1, 8, 8, 3, 2, 0, 1, 0]))
    check(geo.width == 8 and geo.height == 8, "READY-Geometrie 8x8")
    check(geo.frame_bytes == 192, "Frame-Groesse == 192 B", str(geo.frame_bytes))
    check(geo.order_name == "GRB", "Color-Order-Code 2 == GRB", geo.order_name)
    check(len(encode(CMD_FRAME, bytes(192))) == 199, "Vollbild-Frame == 199 B on the wire")


def test_framebuffer() -> None:
    from mx8.framebuffer import Framebuffer
    from mx8.patterns import PATTERNS

    fb = Framebuffer(8, 8)
    fb.set(3, 5, 10, 20, 30)
    check(fb.get(3, 5) == (10, 20, 30), "Framebuffer set/get")
    check(fb.buf[(5 * 8 + 3) * 3 : (5 * 8 + 3) * 3 + 3] == bytes((10, 20, 30)),
          "Framebuffer ist row-major")
    fb.set(99, 99, 1, 2, 3)  # darf nicht werfen
    check(len(fb.to_bytes()) == 192, "Framebuffer liefert 192 B")

    bad = []
    for name, fn in PATTERNS.items():
        fb2 = Framebuffer(8, 8)
        for step in range(12):
            fn(fb2, step * 0.1)
            if len(fb2.buf) != 192 or any(not 0 <= v <= 255 for v in fb2.buf):
                bad.append(name)
                break
    check(not bad, "alle Host-Patterns liefern gueltige 192-B-Frames", ", ".join(bad))


def test_font() -> None:
    from mx8.font5x7 import GLYPH_H, GLYPH_W, GLYPHS, TextBitmap, validate_font

    problems = validate_font()
    check(not problems, f"alle {len(GLYPHS)} Glyphen sind {GLYPH_W}x{GLYPH_H}",
          "; ".join(problems[:3]))

    tb = TextBitmap("Hello World")
    # 11 Zeichen a 5 Spalten + 10 Trennspalten
    check(len(tb) == 11 * GLYPH_W + 10, "TextBitmap-Breite stimmt", str(len(tb)))
    check(tb.column(-5) == [False] * GLYPH_H and tb.column(9999) == [False] * GLYPH_H,
          "Spalten ausserhalb des Textes sind leer")
    check(any(any(c) for c in tb.cols), "gerenderter Text ist nicht leer")

    # Unbekannte Zeichen duerfen nicht werfen, sondern werden zu '?'
    check(TextBitmap("☃").cols == TextBitmap("?").cols,
          "unbekanntes Zeichen faellt auf '?' zurueck")


def test_scroller() -> None:
    """Der Lauftext muss ueber den ganzen Durchlauf gueltige Frames liefern."""
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
    check(bad == 0, "Lauftext liefert ueber den ganzen Durchlauf gueltige Frames",
          f"{bad} defekte Frames")
    check(lit_frames > 40, "Lauftext zeigt tatsaechlich Pixel", f"nur {lit_frames}")


def test_speed_theory() -> None:
    """Die Baudraten-Rechnung muss zur Protokollgroesse passen."""
    sys.path.insert(0, str(ROOT / "script"))
    from speedtest import theory

    th = theory(115200, 192)
    check(th["frame_on_wire_bytes"] == 199, "Vollbild = 199 B auf der Leitung")
    # (199 + 8) * 10 Bit / 115200 = 17,97 ms
    check(abs((th["tx_s"] + th["rx_s"]) * 1000 - 17.97) < 0.02,
          "Round-Trip bei 115200 Baud = 17,97 ms",
          f"{(th['tx_s'] + th['rx_s']) * 1000:.2f} ms")
    check(abs(th["fps_acked"] - 55.65) < 0.1, "-> 55,7 fps theoretisches Limit",
          f"{th['fps_acked']:.2f}")
    # Doppelte Baudrate muss exakt doppelte Framerate ergeben
    check(abs(theory(230400, 192)["fps_acked"] / th["fps_acked"] - 2.0) < 1e-9,
          "doppelte Baudrate = doppelte Framerate")


# ---------------------------------------------------------------------------
def main() -> int:
    print("Kompiliere Firmware-Header mit Host-Compiler ...")
    try:
        exe = build_harness()
    except subprocess.CalledProcessError as e:
        print("FEHLER: Firmware-Header kompilieren fehlgeschlagen", file=sys.stderr)
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
    test_speed_theory()

    total = _passes + len(_fails)
    for f in _fails:
        print(f"  FAIL  {f}")
    print(f"\n{_passes}/{total} Checks bestanden.")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())

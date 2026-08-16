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


def test_smiley() -> None:
    """Smiley-Animation: Timeline, Stetigkeit und gueltige Frames."""
    sys.path.insert(0, str(ROOT / "script"))
    from mx8.framebuffer import Framebuffer
    from smiley import (
        LUSTIG,
        NEUTRAL,
        SEQUENCE,
        TRAURIG,
        ZWINKERN,
        draw_face,
        face_at,
        lerp_hue,
        wink_envelope,
    )

    HOLD, MORPH, WINKS = 1.4, 0.7, 2

    # Die Animation muss auf dem neutralen Gesicht STEHEN, nicht mitten im
    # Uebergang starten -- sonst sieht man "zuerst neutral" nie.
    f0, _ = face_at(0.0, HOLD, MORPH, WINKS)
    check(f0.name == "neutral" and abs(f0.curve) < 1e-9,
          "t=0 haelt das neutrale Gesicht", f"{f0.name} curve={f0.curve}")
    f1, _ = face_at(HOLD * 0.9, HOLD, MORPH, WINKS)
    check(f1.curve == NEUTRAL.curve, "neutrales Gesicht bleibt die Standzeit ueber stehen")

    # Reihenfolge neutral -> traurig -> lustig -> zwinkern
    check([f.name for f in SEQUENCE] == ["neutral", "traurig", "lustig", "zwinkern"],
          "Reihenfolge der Mienen stimmt")
    period = HOLD + MORPH
    mid_hold = HOLD / 2
    seen = [face_at(k * period + mid_hold, HOLD, MORPH, WINKS)[0].name for k in range(4)]
    check(seen == ["neutral", "traurig", "lustig", "zwinkern"],
          "jede Miene wird einmal gehalten", str(seen))

    # Mundkruemmung: traurig < neutral < lustig
    check(TRAURIG.curve < NEUTRAL.curve < LUSTIG.curve,
          "Mundkruemmung laeuft von traurig ueber neutral nach lustig")

    # Zwinkern: Huellkurve muss an beiden Enden offen sein und dazwischen
    # wirklich schliessen, sonst springt das Bild an der Segmentgrenze.
    check(wink_envelope(0.0, HOLD, WINKS) == 1.0, "Zwinker-Kurve startet offen")
    check(abs(wink_envelope(HOLD, HOLD, WINKS) - 1.0) < 1e-9,
          "Zwinker-Kurve endet offen (= ZWINKERN.eye_r, kein Sprung)")
    check(ZWINKERN.eye_r == 1.0, "ZWINKERN.eye_r passt zur Huellkurve")
    samples = [wink_envelope(i / 200 * HOLD, HOLD, WINKS) for i in range(200)]
    check(min(samples) == 0.0, "das Auge geht waehrend des Zwinkerns wirklich ganz zu")
    closed_ratio = sum(s < 0.5 for s in samples) / len(samples)
    check(closed_ratio < 0.4,
          "Auge ist ueberwiegend offen (Zwinkern, kein Dauerblinzeln)",
          f"{closed_ratio:.0%} geschlossen")

    # Farbton auf dem kurzen Weg: Gelb -> Rosa muss ueber Rot, nicht ueber Gruen
    mid = lerp_hue(0.13, 0.92, 0.5)
    check(mid > 0.9 or mid < 0.13, "Farbton nimmt den kurzen Weg um den Farbkreis",
          f"Mitte bei {mid:.3f}")

    # Alle Frames zweier kompletter Zyklen muessen gueltig sein
    fb = Framebuffer(8, 8)
    cycle = period * len(SEQUENCE)
    bad = dark = 0
    steps = int(cycle * 2 * 16)  # zwei Runden bei 16 fps
    for i in range(steps):
        t = i / 16.0
        f, _ = face_at(t, HOLD, MORPH, WINKS)
        draw_face(fb, f, t)
        if len(fb.buf) != 192 or any(not 0 <= v <= 255 for v in fb.buf):
            bad += 1
        if not any(fb.buf):
            dark += 1
    check(bad == 0, f"alle {steps} Smiley-Frames sind gueltige 192-B-Bilder",
          f"{bad} defekt")
    check(dark == 0, "kein Frame ist komplett schwarz", f"{dark} leere Frames")


def test_smiley_ring_and_gamma() -> None:
    """Gesichtskreis und Gamma-Korrektur der schwachen Farben."""
    sys.path.insert(0, str(ROOT / "script"))
    from mx8.framebuffer import Framebuffer
    from smiley import NEUTRAL, RING_DEFAULT, draw_face, draw_ring, gamma

    # Gamma: eine "wahrgenommene" Intensitaet muss deutlich weniger PWM ergeben.
    check(gamma(1.0) == 1.0, "gamma(1.0) laesst volle Helligkeit unangetastet")
    check(gamma(0.0) == 0.0, "gamma(0.0) bleibt aus")
    check(abs(gamma(0.5) - 0.5 ** 2.2) < 1e-9, "gamma(0.5) = 0.5^2.2 ≈ 0.218")
    check(gamma(0.25) < 0.06,
          "wahrgenommene 25 % ergeben unter 6 % PWM (sonst wirkt 'zart' zu kraeftig)",
          f"{gamma(0.25):.3f}")
    check(all(gamma(a) < gamma(b) for a, b in [(.1, .2), (.2, .4), (.4, .8)]),
          "gamma ist monoton steigend")

    # Der Ring muss ein geschlossener Kreis am Rand sein und die Mitte freilassen.
    fb = Framebuffer(8, 8)
    draw_ring(fb, 1.0)  # volle Intensitaet, damit die Form klar messbar ist
    lit = {(x, y) for y in range(8) for x in range(8) if any(fb.get(x, y))}
    check((3, 0) in lit and (0, 3) in lit and (7, 4) in lit and (4, 7) in lit,
          "Ring beruehrt alle vier Seiten")
    check((1, 1) in lit and (6, 6) in lit, "Ring hat die diagonalen Eckpunkte")  # noqa
    check((0, 0) not in lit and (7, 7) not in lit, "Ring laesst die Ecken frei")
    centre = {(x, y) for x in range(2, 6) for y in range(2, 6)}
    check(not (lit & centre), "Ring laesst die Gesichtsmitte komplett frei",
          str(sorted(lit & centre)))
    check(len(lit) == 20, "Ring besteht aus 20 Pixeln", str(len(lit)))

    # Der Ring darf das Gesicht nicht ueberstrahlen. Geprueft an Pixeln, die
    # das Gesicht selbst nie belegt (Zeile 0 und Spalte 0/7).
    fb2 = Framebuffer(8, 8)
    draw_face(fb2, NEUTRAL, 0.0, ring=0.25)
    ring_px = max(max(fb2.get(x, y)) for x, y in [(0, 3), (7, 4), (3, 7)])
    face_px = max(fb2.get(2, 1))          # Augen liegen jetzt in Zeile 0/1
    check(face_px == 255, "Gesichtsmerkmale leuchten voll", str(face_px))
    check(ring_px < face_px * 0.12,
          "Ring bleibt deutlich unter dem Gesicht (zart angedeutet)",
          f"Ring {ring_px} vs Gesicht {face_px}")
    check(ring_px > 0, "Ring ist trotzdem sichtbar", str(ring_px))

    # Auf 8x8 ist der Ring per Default aus -- das Gesicht braucht die Flaeche.
    check(RING_DEFAULT == 0.0, "Ring ist auf 8x8 standardmaessig aus",
          str(RING_DEFAULT))
    fb3 = Framebuffer(8, 8)
    draw_face(fb3, NEUTRAL, 0.0, ring=0.0)
    check(all(not any(fb3.get(x, y)) for x, y in [(3, 0), (0, 3), (7, 4), (4, 7)]),
          "--ring 0 schaltet den Kreis komplett ab")


def test_smiley_geometry() -> None:
    """Festes Bitmuster, harte Pixel, volle Breite."""
    sys.path.insert(0, str(ROOT / "script"))
    from mx8.framebuffer import Framebuffer
    from smiley import (
        EYE_L_BOT,
        EYE_L_TOP,
        EYE_R_BOT,
        EYE_R_TOP,
        EYE_ROW_BOT,
        EYE_ROW_TOP,
        LUSTIG,
        NEUTRAL,
        TRAURIG,
        ZWINKERN,
        draw_face,
        mouth_rows,
    )

    def render(face, t=0.0):
        fb = Framebuffer(8, 8)
        draw_face(fb, face, t, ring=0.0)
        return fb

    def lit(fb):
        return {(x, y) for y in range(8) for x in range(8) if any(fb.get(x, y))}

    # --- KEINE gedimmten Pixel -------------------------------------------
    # Teilgedeckte Pixel landen bei kleiner Panel-Helligkeit auf PWM 1..3,
    # wo WS2812B fleckig werden. Auf 8x8 wird deshalb hart gesetzt.
    for face in (NEUTRAL, TRAURIG, LUSTIG, ZWINKERN):
        fb = render(face, 0.3)
        levels = {max(fb.get(x, y)) for x, y in lit(fb)}
        check(levels <= {255}, f"'{face.name}': jedes Pixel voll an, keine Zwischenstufen",
              f"Stufen {sorted(levels)}")

    # --- Augen exakt nach Bitmuster --------------------------------------
    #   Zeile 0   0 1 1 0 0 1 1 0
    #   Zeile 1   1 1 1 0 0 1 1 1
    #   Zeile 2   0 0 0 0 0 0 0 0
    fb = render(NEUTRAL)
    row0 = {x for x in range(8) if any(fb.get(x, EYE_ROW_TOP))}
    row1 = {x for x in range(8) if any(fb.get(x, EYE_ROW_BOT))}
    row2 = {x for x in range(8) if any(fb.get(x, 2))}
    check(row0 == {1, 2, 5, 6}, "Augen Zeile 0 = 01100110", str(sorted(row0)))
    check(row1 == {0, 1, 2, 5, 6, 7}, "Augen Zeile 1 = 11100111", str(sorted(row1)))
    check(row2 == set(), "Zeile 2 bleibt frei", str(sorted(row2)))
    check(set(EYE_L_TOP) | set(EYE_R_TOP) == {1, 2, 5, 6}, "obere Augenreihe stimmt")
    check(set(EYE_L_BOT) | set(EYE_R_BOT) == {0, 1, 2, 5, 6, 7}, "untere Augenreihe stimmt")

    # --- Mund: genau ein Pixel je Spalte, ueber alle 8 Spalten ------------
    for face in (NEUTRAL, TRAURIG, LUSTIG, ZWINKERN):
        fb = render(face)
        for x in range(8):
            n = sum(1 for y in range(3, 8) if any(fb.get(x, y)))
            check(n == 1, f"'{face.name}' Spalte {x}: genau 1 Mundpixel", f"{n} Pixel")

    # --- Mundformen ------------------------------------------------------
    check(mouth_rows(0.0) == [6] * 8, "neutral: gerade Linie in Zeile 6",
          str(mouth_rows(0.0)))
    check(mouth_rows(1.0) == [4, 5, 6, 7, 7, 6, 5, 4], "lustig: Bogen nach oben offen",
          str(mouth_rows(1.0)))
    check(mouth_rows(-1.0) == [7, 6, 5, 4, 4, 5, 6, 7], "traurig: Bogen nach unten offen",
          str(mouth_rows(-1.0)))

    # --- Keine Luecken, auch nicht in den Zwischenschritten ---------------
    # Springt der Mund zwischen zwei Nachbarspalten um mehr als eine Zeile,
    # reisst er optisch auseinander.
    worst = 0
    for i in range(-20, 21):
        rows = mouth_rows(i / 20.0)
        worst = max(worst, max(abs(rows[k + 1] - rows[k]) for k in range(7)))
    check(worst <= 1, "Mund bleibt in jedem Uebergangsschritt zusammenhaengend",
          f"groesster Sprung {worst} Zeilen")

    # --- Volle Breite, und die Mienen reichen bis an beide Kanten ---------
    for face in (NEUTRAL, TRAURIG, LUSTIG, ZWINKERN):
        cols = {x for x, _ in lit(render(face))}
        check(cols == set(range(8)), f"'{face.name}' nutzt alle 8 Spalten",
              f"nur {sorted(cols)}")
    for face in (TRAURIG, LUSTIG, ZWINKERN):
        rows = {y for _, y in lit(render(face))}
        check(rows >= {0, 1, 4, 5, 6, 7}, f"'{face.name}' reicht bis Zeile 7",
              f"Zeilen {sorted(rows)}")

    # --- Zwinkern: obere Reihe faellt weg, untere bleibt ------------------
    closed = ZWINKERN.__class__(ZWINKERN.name, ZWINKERN.curve, 1.0, 0.0,
                                ZWINKERN.hue, ZWINKERN.sat, ZWINKERN.tear)
    fb = render(closed)
    check(all(any(fb.get(x, EYE_ROW_TOP)) for x in EYE_L_TOP),
          "beim Zwinkern bleibt das linke Auge offen")
    check(not any(any(fb.get(x, EYE_ROW_TOP)) for x in EYE_R_TOP),
          "rechtes Auge: obere Reihe ist weg")
    check(all(any(fb.get(x, EYE_ROW_BOT)) for x in EYE_R_BOT),
          "rechtes Auge: untere Reihe bleibt als Lidstrich stehen")


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
    test_smiley()
    test_smiley_ring_and_gamma()
    test_smiley_geometry()
    test_speed_theory()

    total = _passes + len(_fails)
    for f in _fails:
        print(f"  FAIL  {f}")
    print(f"\n{_passes}/{total} Checks bestanden.")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Wie schnell wird das Display wirklich?

    python speedtest.py                    # Latenz + Durchsatz messen
    python speedtest.py --seconds 20       # laenger messen
    python speedtest.py --flood            # zusaetzlich der Stresstest ohne ACK
    python speedtest.py --preview          # nur die Rechnung, ohne Hardware

Gemessen wird in drei Stufen:

  1. LATENZ     PING/ACK hin und zurueck. Zeigt die reine Reaktionszeit der
                Strecke PC -> USB-CDC -> Nano -> zurueck, ohne Bilddaten.
  2. DURCHSATZ  Vollbilder so schnell wie moeglich, jeweils mit ACK-Quittung.
                Das ist die Zahl, die im Normalbetrieb zaehlt.
  3. FLOOD      (optional) Vollbilder ohne auf ACK zu warten. Zeigt, was
                passiert, wenn man die Flusskontrolle weglaesst -- erwartbar
                gehen Frames verloren, sobald FastLED in show() die Interrupts
                sperrt. Nur zur Demonstration, nicht zum Nachmachen.

Alle Messwerte werden gegen das theoretische Baudraten-Limit gestellt, damit
sichtbar wird, ob die Leitung oder der Overhead bremst.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time

from mx8 import Framebuffer, MatrixLink
from mx8.link import LinkError
from mx8.protocol import OVERHEAD_BYTES

BITS_PER_BYTE = 10  # 8N1: 1 Start + 8 Daten + 1 Stopp
ACK_FRAME_BYTES = OVERHEAD_BYTES + 1

# WS2812B: 800 kHz Einzeldraht, 1,25 us je Bit, 24 Bit je LED, danach eine
# Reset-Pause. Das ist ein FESTES Zeitbudget pro Bild, unabhaengig von allem
# anderen -- und ab ein paar hundert LEDs der eigentliche Flaschenhals.
WS2812_BIT_S = 1.25e-6
WS2812_RESET_S = 300e-6
WS2812_BITS_PER_LED = 24


def led_refresh_s(num_leds: int, strands: int = 1) -> float:
    """Dauer eines FastLED-show() fuer ``num_leds`` LEDs.

    ``strands`` = Anzahl parallel angesteuerter Straenge. FastLED kann mehrere
    Pins gleichzeitig takten, das teilt die Zeit entsprechend auf.
    """
    per_strand = math.ceil(num_leds / max(1, strands))
    return per_strand * WS2812_BITS_PER_LED * WS2812_BIT_S + WS2812_RESET_S


# ---------------------------------------------------------------------------
def theory(baud: int, frame_bytes: int, bpp: int = 3,
           strands: int = 1, overlap: bool = False) -> dict[str, float]:
    """Die zwei harten Grenzen: serielle Strecke und LED-Protokoll.

    Ob sie sich addieren, entscheidet die Plattform:

    ``overlap=False`` (AVR, Standardfall): FastLED taktet die LEDs per Bitbang
    aus und sperrt dabei die Interrupts. Der Controller kann in dieser Zeit
    nichts empfangen -- die Zeiten addieren sich.

    ``overlap=True`` (RP2040): der PIO-Treiber schiebt die Bits per DMA raus.
    showPixels() wartet nur auf die VORHERIGE Uebertragung und kehrt dann
    zurueck, Interrupts bleiben an (FASTLED_ALLOW_INTERRUPTS ist dort auf 1).
    Empfangen und Austakten laufen also gleichzeitig -- es zaehlt der laengere
    der beiden Wege, nicht die Summe.
    """
    tx = (OVERHEAD_BYTES + frame_bytes) * BITS_PER_BYTE / baud
    rx = ACK_FRAME_BYTES * BITS_PER_BYTE / baud
    num_leds = frame_bytes // bpp
    led = led_refresh_s(num_leds, strands)
    total = max(tx + rx, led) if overlap else (tx + rx + led)
    return {
        "frame_on_wire_bytes": OVERHEAD_BYTES + frame_bytes,
        "num_leds": num_leds,
        "tx_s": tx,
        "rx_s": rx,
        "led_s": led,
        "total_s": total,
        "overlap": overlap,
        "fps_stream": 1.0 / tx,           # ohne ACK-Wartezeit
        "fps_acked": 1.0 / (tx + rx),     # nur serielle Strecke
        "fps_led": 1.0 / led,             # nur LED-Protokoll
        "fps_total": 1.0 / total,         # beides, so wie es wirklich laeuft
    }


def print_theory(baud: int, frame_bytes: int, strands: int = 1,
                 overlap: bool = False) -> dict[str, float]:
    th = theory(baud, frame_bytes, strands=strands, overlap=overlap)
    ser = th["tx_s"] + th["rx_s"]
    print(f"\n  Theoretische Grenzen")
    print(f"    Frame auf der Leitung     {th['frame_on_wire_bytes']:.0f} B "
          f"({frame_bytes} B Bild + {OVERHEAD_BYTES} B Protokoll)")
    print(f"    a) seriell @ {baud} Baud  {ser * 1000:7.2f} ms  "
          f"-> {th['fps_acked']:6.1f} fps")
    print(f"    b) WS2812B-Ausgabe        {th['led_s'] * 1000:7.2f} ms  "
          f"-> {th['fps_led']:6.1f} fps"
          + (f"   ({strands} parallele Straenge)" if strands > 1 else ""))
    if overlap:
        print(f"    max(a, b) -- RP2040       {th['total_s'] * 1000:7.2f} ms  "
              f"-> {th['fps_total']:6.1f} fps  <- das ist die Obergrenze")
        print(f"       PIO+DMA taktet im Hintergrund aus, waehrend schon das")
        print(f"       naechste Bild hereinkommt.")
    else:
        print(f"    a + b (addiert sich)      {th['total_s'] * 1000:7.2f} ms  "
              f"-> {th['fps_total']:6.1f} fps  <- das ist die Obergrenze")
    dom = "die serielle Strecke" if th["led_s"] < ser else "das LED-Protokoll"
    print(f"    Dominant ist {dom}.")
    if th["led_s"] >= ser:
        print(f"    -> Mehr Baud bringt hier wenig. Abhilfe: LEDs auf mehrere Pins")
        print(f"       aufteilen (--strands) oder auf getaktete Chips (APA102) wechseln.")
    return th


# ---------------------------------------------------------------------------
def measure_latency(link: MatrixLink, n: int) -> list[float]:
    print(f"\n  1) Latenz  --  {n} × PING/ACK")
    samples = []
    for i in range(n):
        try:
            samples.append(link.ping() * 1000.0)
        except TimeoutError:
            print(f"     Ping {i + 1}: Timeout")
    if not samples:
        print("     keine Antwort")
        return samples
    samples_sorted = sorted(samples)
    print(f"     min {min(samples):6.2f} ms   "
          f"median {statistics.median(samples_sorted):6.2f} ms   "
          f"mean {statistics.fmean(samples):6.2f} ms   "
          f"max {max(samples):6.2f} ms")
    if len(samples) > 2:
        print(f"     Streuung (Stdabw)  {statistics.pstdev(samples):.2f} ms")
    return samples


# ---------------------------------------------------------------------------
def sweep(fb: Framebuffer, i: int) -> None:
    """Schneller Balken -- damit man die Geschwindigkeit auch sieht."""
    fb.clear()
    x = i % fb.width
    for y in range(fb.height):
        fb.set(x, y, 255, 255, 255)
        fb.set((x + fb.width // 2) % fb.width, y, 0, 90, 255)


def measure_throughput(link: MatrixLink, seconds: float, th: dict) -> None:
    geo = link.geometry
    fb = Framebuffer(geo.width, geo.height)
    print(f"\n  2) Durchsatz  --  Vollbilder mit ACK, {seconds:g} s")

    deltas: list[float] = []
    n = 0
    t0 = time.monotonic()
    last = t0
    deadline = t0 + seconds

    while time.monotonic() < deadline:
        sweep(fb, n)
        link.send_frame(fb.to_bytes())
        now = time.monotonic()
        deltas.append((now - last) * 1000.0)
        last = now
        n += 1

    dur = time.monotonic() - t0
    if not n:
        print("     nichts gesendet")
        return

    fps = n / dur
    payload_bps = n * (OVERHEAD_BYTES + geo.frame_bytes) * BITS_PER_BYTE / dur
    print(f"     {n} Frames in {dur:.2f} s  =  {fps:.1f} fps")
    print(f"     Frame-Abstand   min {min(deltas):.2f} ms   "
          f"median {statistics.median(deltas):.2f} ms   max {max(deltas):.2f} ms")
    print(f"     Nutzlast        {payload_bps / 1000:.1f} kbit/s "
          f"({n * geo.frame_bytes / dur / 1024:.1f} KiB/s Bilddaten)")
    print(f"     Ausnutzung      {fps / th['fps_acked'] * 100:.0f} % des "
          f"theoretischen Limits von {th['fps_acked']:.1f} fps")

    head = fps / th["fps_acked"]
    if head > 0.9:
        print("     -> Die Baudrate ist der Flaschenhals. Mehr fps gibt es nur")
        print("        ueber MX_SERIAL_BAUD (z. B. 250000 -> etwa doppelt so schnell).")
    elif head > 0.6:
        print("     -> Nahe am Leitungslimit; der Rest ist USB- und OS-Latenz.")
    else:
        print("     -> Deutlich unter dem Leitungslimit: hier bremst die Latenz")
        print("        pro Frame (USB-CDC-Puffer, Timer-Aufloesung des OS), nicht die Baudrate.")


# ---------------------------------------------------------------------------
def measure_flood(link: MatrixLink, count: int) -> None:
    """Ohne Flusskontrolle senden -- und zaehlen, was hinten ankommt."""
    geo = link.geometry
    fb = Framebuffer(geo.width, geo.height)
    print(f"\n  3) Flood (Stresstest)  --  {count} Frames OHNE auf ACK zu warten")

    link.drain()
    t0 = time.monotonic()
    for i in range(count):
        sweep(fb, i)
        link.send_frame_nowait(fb.to_bytes())
    tx_dur = time.monotonic() - t0

    time.sleep(1.0)  # Nachlauf: Antworten einsammeln
    acks, nacks = link.drain()

    print(f"     {count} Frames rausgeschoben in {tx_dur:.2f} s "
          f"(nominell {count / tx_dur:.0f} fps)")
    print(f"     zurueck: {acks} ACK, {nacks} NACK")
    lost = count - acks - nacks
    if lost > 0:
        print(f"     -> {lost} Frames ({lost / count * 100:.0f} %) sind unterwegs "
              f"verloren gegangen.")
        print("        Genau dafuer ist die ACK-Quittung da: FastLED sperrt waehrend")
        print("        show() die Interrupts, in dieser Zeit fallen RX-Bytes auf den Boden.")
    elif nacks:
        print("     -> Keine Verluste, aber NACKs: der Empfangspuffer lief knapp ueber.")
    else:
        print("     -> Alles angekommen. Der RX-Puffer hat gereicht;")
        print("        bei groesseren Panels oder hoeherer Baudrate kippt das.")


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-p", "--port", help="z. B. COM4 oder /dev/ttyACM0 (sonst MX8_PORT bzw. Auto)")
    ap.add_argument("-b", "--baud", type=int, default=None,
                   help="Standard: MX8_BAUD, sonst 115200")
    ap.add_argument("--seconds", type=float, default=8.0, help="Dauer der Durchsatzmessung")
    ap.add_argument("--pings", type=int, default=40)
    ap.add_argument("--strands", type=int, default=1,
                    help="parallel angesteuerte LED-Straenge (FastLED-Multi-Pin)")
    ap.add_argument("--rp2040", action="store_true",
                    help="RP2040-Modell: PIO+DMA taktet im Hintergrund aus, "
                         "Uebertragung und show() ueberlappen sich")
    ap.add_argument("--flood", action="store_true", help="zusaetzlich den Stresstest fahren")
    ap.add_argument("--flood-count", type=int, default=200)
    ap.add_argument("--brightness", type=int, default=None)
    ap.add_argument("--preview", action="store_true",
                    help="nur die theoretische Rechnung, ohne Hardware")
    ap.add_argument("--release", action="store_true",
                    help="am Ende RELEASE senden statt CLEAR")
    args = ap.parse_args(argv)

    print("=" * 68)
    print("  MX8 Speedtest")
    print("=" * 68)

    if args.preview:
        panels = [("8x8", 64), ("8x32", 256), ("16x16", 256),
                  ("16x32", 512), ("32x32", 1024)]
        print("\n  Obergrenze je Panelgroesse und Baudrate (fps)")
        if args.rp2040:
            print("  RP2040-Modell: PIO+DMA taktet die LEDs im Hintergrund aus, waehrend")
            print("  schon das naechste Bild hereinkommt -- es zaehlt der laengere Weg.\n")
        else:
            print("  Die WS2812B-Ausgabe kostet unabhaengig von der Baudrate Zeit und")
            print("  addiert sich zur Uebertragung -- ab ein paar hundert LEDs dominiert sie.\n")
        head = "  Panel    LEDs   show()  " + "".join(f"{b // 1000:>7}k" for b in
                                                      (115200, 250000, 500000, 1000000, 2000000))
        print(head)
        print("  " + "-" * (len(head) - 2))
        for name, leds in panels:
            th0 = theory(115200, leds * 3, strands=args.strands, overlap=args.rp2040)
            row = f"  {name:<7} {leds:>5}  {th0['led_s'] * 1000:6.2f}ms "
            for baud in (115200, 250000, 500000, 1000000, 2000000):
                t = theory(baud, leds * 3, strands=args.strands, overlap=args.rp2040)
                row += f"{t['fps_total']:8.1f}"
            print(row)
        print(f"\n  show() bei {args.strands} Strang"
              + ("" if args.strands == 1 else "/Straengen") + ".")
        print("  Ohne Hardware endet der Test hier -- fuer echte Messungen den")
        print("  Nano anstecken und ohne --preview starten.")
        return 0

    link = MatrixLink(args.port, args.baud, ack_timeout=1.0,
                      on_log=lambda s: print(f"  LOG  {s}"))
    link.open()
    try:
        geo = link.wait_ready(timeout=8.0)
        print(f"\n  Port    {link.port} @ {link.baud} Baud")
        print(f"  Panel   {geo}")
        if args.brightness is not None:
            link.set_brightness(args.brightness)

        th = print_theory(link.baud, geo.frame_bytes, args.strands, args.rp2040)
        measure_latency(link, args.pings)
        measure_throughput(link, args.seconds, th)
        if args.flood:
            measure_flood(link, args.flood_count)

        s = link.stats
        print(f"\n  Gesamt: {s['frames_sent']} Frames, {s['acks']} ACK, "
              f"{s['nacks']} NACK, {s['crc_errors']} CRC-Fehler")
        print("=" * 68)

        try:
            link.release() if args.release else link.clear()
        except Exception:
            pass
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(130)
    except LinkError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError as e:
        print(f"Timeout: {e}", file=sys.stderr)
        sys.exit(1)

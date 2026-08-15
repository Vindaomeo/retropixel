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


# ---------------------------------------------------------------------------
def theory(baud: int, frame_bytes: int) -> dict[str, float]:
    """Was die Baudrate physikalisch hergibt -- ohne jede Software-Latenz."""
    tx = (OVERHEAD_BYTES + frame_bytes) * BITS_PER_BYTE / baud
    rx = ACK_FRAME_BYTES * BITS_PER_BYTE / baud
    return {
        "frame_on_wire_bytes": OVERHEAD_BYTES + frame_bytes,
        "tx_s": tx,
        "rx_s": rx,
        "fps_stream": 1.0 / tx,        # ohne ACK-Wartezeit
        "fps_acked": 1.0 / (tx + rx),  # mit ACK-Quittung, so wie wir senden
    }


def print_theory(baud: int, frame_bytes: int) -> dict[str, float]:
    th = theory(baud, frame_bytes)
    print(f"\n  Theoretisches Limit bei {baud} Baud")
    print(f"    Frame auf der Leitung     {th['frame_on_wire_bytes']:.0f} B "
          f"({frame_bytes} B Bild + {OVERHEAD_BYTES} B Protokoll)")
    print(f"    reine Sendezeit           {th['tx_s'] * 1000:.2f} ms  "
          f"-> {th['fps_stream']:.1f} fps")
    print(f"    + ACK zurueck             {th['rx_s'] * 1000:.2f} ms  "
          f"-> {th['fps_acked']:.1f} fps  <- unser Verfahren")
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
    ap.add_argument("-p", "--port", help="z. B. COM5 oder /dev/ttyACM0 (sonst Auto)")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=8.0, help="Dauer der Durchsatzmessung")
    ap.add_argument("--pings", type=int, default=40)
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
        for baud in (9600, 57600, 115200, 250000, 500000, 1000000):
            th = theory(baud, 192)
            print(f"  {baud:>8} Baud   {th['tx_s'] * 1000:6.2f} ms/Frame   "
                  f"{th['fps_acked']:6.1f} fps mit ACK   "
                  f"{th['fps_stream']:6.1f} fps ohne")
        print("\n  (Werte fuer ein 8x8-Panel: 192 B Bild + 7 B Protokoll.)")
        print("  Ohne Hardware endet der Test hier -- fuer echte Messungen den")
        print("  Nano anstecken und ohne --preview starten.")
        return 0

    link = MatrixLink(args.port, args.baud, ack_timeout=1.0,
                      on_log=lambda s: print(f"  LOG  {s}"))
    link.open()
    try:
        geo = link.wait_ready(timeout=8.0)
        print(f"\n  Port    {link.port} @ {args.baud} Baud")
        print(f"  Panel   {geo}")
        if args.brightness is not None:
            link.set_brightness(args.brightness)

        th = print_theory(args.baud, geo.frame_bytes)
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

#!/usr/bin/env python3
"""How fast does the display really get?

    python speedtest.py                    # measure latency + throughput
    python speedtest.py --seconds 20       # measure for longer
    python speedtest.py --flood            # additionally the stress test without ACK
    python speedtest.py --preview          # just the math, no hardware

Measurement runs in three stages:

  1. LATENCY    PING/ACK round trip. Shows the pure response time of the
                path PC -> USB-CDC -> Nano -> back, without any image data.
  2. THROUGHPUT Full frames as fast as possible, each acknowledged with an ACK.
                This is the number that matters in normal operation.
  3. FLOOD      (optional) Full frames without waiting for the ACK. Shows what
                happens when you leave out flow control -- predictably,
                frames get lost as soon as FastLED disables interrupts in
                show(). For demonstration only, don't try this at home.

All measurements are set against the theoretical baud-rate limit, so you can
see whether the line or the overhead is holding things back.
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

BITS_PER_BYTE = 10  # 8N1: 1 start + 8 data + 1 stop
ACK_FRAME_BYTES = OVERHEAD_BYTES + 1

# WS2812B: 800 kHz single wire, 1.25 us per bit, 24 bits per LED, followed by a
# reset pause. That is a FIXED time budget per frame, independent of everything
# else -- and beyond a few hundred LEDs it is the actual bottleneck.
WS2812_BIT_S = 1.25e-6
WS2812_RESET_S = 300e-6
WS2812_BITS_PER_LED = 24


def led_refresh_s(num_leds: int, strands: int = 1) -> float:
    """Duration of one FastLED show() for ``num_leds`` LEDs.

    ``strands`` = number of strands driven in parallel. FastLED can clock out
    several pins at the same time, which divides the time accordingly.
    """
    per_strand = math.ceil(num_leds / max(1, strands))
    return per_strand * WS2812_BITS_PER_LED * WS2812_BIT_S + WS2812_RESET_S


# ---------------------------------------------------------------------------
#  Network transport (Wi-Fi instead of USB)
# ---------------------------------------------------------------------------
#  What an MX8 frame costs when it arrives over Wi-Fi rather than USB-CDC.
#  Three cost items, and only one of them is really expensive:
#
#    1. Bytes    -- headers for IP/TCP or UDP, LLC/SNAP and 802.11. Fixed per
#                   packet, barely matters for large frames.
#    2. Airtime  -- a fixed time budget per packet (backoff, preamble, ACK),
#                   independent of the packet size.
#    3. Latency  -- the round-trip time. ONLY relevant if the host waits for
#                   an ACK on every frame. That is exactly what breaks things.
MTU = 1500
HDR_IP, HDR_TCP, HDR_UDP = 20, 20, 8
HDR_LLC_SNAP, HDR_80211, FCS = 8, 24, 4

# Fixed time budget per 802.11 packet: DIFS + mean backoff + preamble
# + SIFS + ACK. Applies regardless of how large the packet is.
WIFI_FIXED_AIRTIME_S = (28 + 67 + 20 + 16 + 30) * 1e-6

#: Measured on the Pico W with the C SDK (iperf2). The Pico 2 W uses the same
#: CYW43439 on the same PIO-SPI; read the number as an order of magnitude,
#: not as a guarantee.
WIFI_MEASURED_BPS = 8.15e6


def net_frame_bytes(payload: int, proto: str = "udp") -> tuple[int, int]:
    """(number of packets, bytes on the air interface) for one MX8 frame."""
    if proto == "tcp":
        head = HDR_IP + HDR_TCP
    elif proto == "udp":
        head = HDR_IP + HDR_UDP
    else:
        raise ValueError(f"proto {proto!r} -- 'tcp' or 'udp'")
    pkts = max(1, math.ceil(payload / (MTU - head)))
    return pkts, payload + pkts * (head + HDR_LLC_SNAP + HDR_80211 + FCS)


def theory_net(frame_bytes: int, link_bps: float = WIFI_MEASURED_BPS,
               proto: str = "udp", rtt_s: float = 0.0, acked: bool = False,
               bpp: int = 3, strands: int = 1) -> dict[str, float]:
    """Upper bound over Wi-Fi.

    ``acked=True`` models today's behavior: the host waits for the ACK on
    every frame, so the round-trip time goes fully into the frame time.
    ``acked=False`` is fire-and-forget with a sequence number -- then latency
    drops out entirely and only throughput counts.
    """
    mx8 = OVERHEAD_BYTES + frame_bytes
    pkts, wire = net_frame_bytes(mx8, proto)
    tx = wire * 8 / link_bps
    air = pkts * WIFI_FIXED_AIRTIME_S
    led = led_refresh_s(frame_bytes // bpp, strands)
    net = max(tx, air) + (rtt_s if acked else 0.0)
    # RP2040/RP2350: PIO+DMA clocks out in the background, so receiving and
    # output overlap -- same as with USB.
    total = max(net, led)
    return {
        "mx8_bytes": mx8, "packets": pkts, "wire_bytes": wire,
        "overhead_pct": wire / mx8 - 1.0,
        "tx_s": tx, "airtime_s": air, "rtt_s": rtt_s if acked else 0.0,
        "net_s": net, "led_s": led, "total_s": total,
        "fps_net": 1.0 / net, "fps_led": 1.0 / led, "fps_total": 1.0 / total,
    }


# ---------------------------------------------------------------------------
def theory(baud: int, frame_bytes: int, bpp: int = 3,
           strands: int = 1, overlap: bool = False) -> dict[str, float]:
    """The two hard limits: serial link and LED protocol.

    Whether they add up is decided by the platform:

    ``overlap=False`` (AVR, the default case): FastLED bit-bangs the LEDs out
    and disables interrupts while doing so. The controller cannot receive
    anything during that time -- the times add up.

    ``overlap=True`` (RP2040): the PIO driver pushes the bits out via DMA.
    showPixels() only waits for the PREVIOUS transfer and then returns,
    interrupts stay enabled (FASTLED_ALLOW_INTERRUPTS is 1 there).
    So receiving and clocking out run at the same time -- what counts is the
    longer of the two paths, not the sum.
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
        "fps_stream": 1.0 / tx,           # without waiting for the ACK
        "fps_acked": 1.0 / (tx + rx),     # serial link only
        "fps_led": 1.0 / led,             # LED protocol only
        "fps_total": 1.0 / total,         # both, the way it really runs
    }


def print_theory(baud: int, frame_bytes: int, strands: int = 1,
                 overlap: bool = False) -> dict[str, float]:
    th = theory(baud, frame_bytes, strands=strands, overlap=overlap)
    ser = th["tx_s"] + th["rx_s"]
    print(f"\n  Theoretical limits")
    print(f"    Frame on the wire         {th['frame_on_wire_bytes']:.0f} B "
          f"({frame_bytes} B image + {OVERHEAD_BYTES} B protocol)")
    print(f"    a) serial @ {baud} baud   {ser * 1000:7.2f} ms  "
          f"-> {th['fps_acked']:6.1f} fps")
    print(f"    b) WS2812B output         {th['led_s'] * 1000:7.2f} ms  "
          f"-> {th['fps_led']:6.1f} fps"
          + (f"   ({strands} parallel strands)" if strands > 1 else ""))
    if overlap:
        print(f"    max(a, b) -- RP2040       {th['total_s'] * 1000:7.2f} ms  "
              f"-> {th['fps_total']:6.1f} fps  <- that is the upper bound")
        print(f"       PIO+DMA clocks out in the background while the next")
        print(f"       frame is already coming in.")
    else:
        print(f"    a + b (they add up)       {th['total_s'] * 1000:7.2f} ms  "
              f"-> {th['fps_total']:6.1f} fps  <- that is the upper bound")
    dom = "the serial link" if th["led_s"] < ser else "the LED protocol"
    print(f"    The dominant factor is {dom}.")
    if th["led_s"] >= ser:
        print(f"    -> More baud won't help much here. Remedy: split the LEDs across")
        print(f"       several pins (--strands) or switch to clocked chips (APA102).")
    return th


# ---------------------------------------------------------------------------
def measure_latency(link: MatrixLink, n: int) -> list[float]:
    print(f"\n  1) Latency  --  {n} × PING/ACK")
    samples = []
    for i in range(n):
        try:
            samples.append(link.ping() * 1000.0)
        except TimeoutError:
            print(f"     Ping {i + 1}: timeout")
    if not samples:
        print("     no response")
        return samples
    samples_sorted = sorted(samples)
    print(f"     min {min(samples):6.2f} ms   "
          f"median {statistics.median(samples_sorted):6.2f} ms   "
          f"mean {statistics.fmean(samples):6.2f} ms   "
          f"max {max(samples):6.2f} ms")
    if len(samples) > 2:
        print(f"     Spread (std dev)   {statistics.pstdev(samples):.2f} ms")
    return samples


# ---------------------------------------------------------------------------
def sweep(fb: Framebuffer, i: int) -> None:
    """Fast bar -- so you can actually see the speed."""
    fb.clear()
    x = i % fb.width
    for y in range(fb.height):
        fb.set(x, y, 255, 255, 255)
        fb.set((x + fb.width // 2) % fb.width, y, 0, 90, 255)


def measure_throughput(link: MatrixLink, seconds: float, th: dict) -> None:
    geo = link.geometry
    fb = Framebuffer(geo.width, geo.height)
    print(f"\n  2) Throughput  --  full frames with ACK, {seconds:g} s")

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
        print("     nothing sent")
        return

    fps = n / dur
    payload_bps = n * (OVERHEAD_BYTES + geo.frame_bytes) * BITS_PER_BYTE / dur
    print(f"     {n} frames in {dur:.2f} s  =  {fps:.1f} fps")
    print(f"     Frame interval  min {min(deltas):.2f} ms   "
          f"median {statistics.median(deltas):.2f} ms   max {max(deltas):.2f} ms")
    print(f"     Payload         {payload_bps / 1000:.1f} kbit/s "
          f"({n * geo.frame_bytes / dur / 1024:.1f} KiB/s image data)")
    print(f"     Utilization     {fps / th['fps_acked'] * 100:.0f} % of the "
          f"theoretical limit of {th['fps_acked']:.1f} fps")

    head = fps / th["fps_acked"]
    if head > 0.9:
        print("     -> The baud rate is the bottleneck. The only way to more fps")
        print("        is MX_SERIAL_BAUD (e.g. 250000 -> roughly twice as fast).")
    elif head > 0.6:
        print("     -> Close to the line limit; the rest is USB and OS latency.")
    else:
        print("     -> Well below the line limit: what slows things down here is the")
        print("        per-frame latency (USB-CDC buffers, OS timer resolution), not the baud rate.")


# ---------------------------------------------------------------------------
def measure_flood(link: MatrixLink, count: int) -> None:
    """Send without flow control -- and count what arrives at the other end."""
    geo = link.geometry
    fb = Framebuffer(geo.width, geo.height)
    print(f"\n  3) Flood (stress test)  --  {count} frames WITHOUT waiting for ACK")

    link.drain()
    t0 = time.monotonic()
    for i in range(count):
        sweep(fb, i)
        link.send_frame_nowait(fb.to_bytes())
    tx_dur = time.monotonic() - t0

    time.sleep(1.0)  # trailing window: collect the replies
    acks, nacks = link.drain()

    print(f"     {count} frames pushed out in {tx_dur:.2f} s "
          f"(nominally {count / tx_dur:.0f} fps)")
    print(f"     returned: {acks} ACK, {nacks} NACK")
    lost = count - acks - nacks
    if lost > 0:
        print(f"     -> {lost} frames ({lost / count * 100:.0f} %) got lost "
              f"along the way.")
        print("        This is exactly what the ACK is for: FastLED disables interrupts")
        print("        during show(), and RX bytes fall on the floor in that time.")
    elif nacks:
        print("     -> No losses, but NACKs: the receive buffer barely overflowed.")
    else:
        print("     -> Everything arrived. The RX buffer was big enough;")
        print("        with larger panels or a higher baud rate that tips over.")


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("-p", "--port", help="e.g. COM4 or /dev/ttyACM0 (otherwise MX8_PORT or auto)")
    ap.add_argument("-b", "--baud", type=int, default=None,
                   help="default: MX8_BAUD, otherwise 115200")
    ap.add_argument("--seconds", type=float, default=8.0, help="duration of the throughput measurement")
    ap.add_argument("--pings", type=int, default=40)
    ap.add_argument("--strands", type=int, default=1,
                    help="LED strands driven in parallel (FastLED multi-pin)")
    ap.add_argument("--rp2040", action="store_true",
                    help="RP2040 model: PIO+DMA clocks out in the background, "
                         "transfer and show() overlap")
    ap.add_argument("--flood", action="store_true", help="additionally run the stress test")
    ap.add_argument("--flood-count", type=int, default=200)
    ap.add_argument("--brightness", type=int, default=None)
    ap.add_argument("--preview", action="store_true",
                    help="only the theoretical calculation, no hardware")
    ap.add_argument("--release", action="store_true",
                    help="send RELEASE at the end instead of CLEAR")
    args = ap.parse_args(argv)

    print("=" * 68)
    print("  MX8 Speedtest")
    print("=" * 68)

    if args.preview:
        panels = [("8x8", 64), ("8x32", 256), ("16x16", 256),
                  ("16x32", 512), ("32x32", 1024)]
        print("\n  Upper bound per panel size and baud rate (fps)")
        if args.rp2040:
            print("  RP2040 model: PIO+DMA clocks the LEDs out in the background while")
            print("  the next frame is already coming in -- the longer path counts.\n")
        else:
            print("  The WS2812B output takes time regardless of the baud rate and adds")
            print("  to the transfer -- beyond a few hundred LEDs it dominates.\n")
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
        print(f"\n  show() with {args.strands} strand"
              + ("" if args.strands == 1 else "s") + ".")
        print("  Without hardware the test ends here -- for real measurements, plug in")
        print("  the Nano and run without --preview.")
        return 0

    link = MatrixLink(args.port, args.baud, ack_timeout=1.0,
                      on_log=lambda s: print(f"  LOG  {s}"))
    link.open()
    try:
        geo = link.wait_ready(timeout=8.0)
        print(f"\n  Port    {link.port} @ {link.baud} baud")
        print(f"  Panel   {geo}")
        if args.brightness is not None:
            link.set_brightness(args.brightness)

        th = print_theory(link.baud, geo.frame_bytes, args.strands, args.rp2040)
        measure_latency(link, args.pings)
        measure_throughput(link, args.seconds, th)
        if args.flood:
            measure_flood(link, args.flood_count)

        s = link.stats
        print(f"\n  Total: {s['frames_sent']} frames, {s['acks']} ACK, "
              f"{s['nacks']} NACK, {s['crc_errors']} CRC errors")
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
        print("\nAborted.")
        sys.exit(130)
    except LinkError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError as e:
        print(f"Timeout: {e}", file=sys.stderr)
        sys.exit(1)

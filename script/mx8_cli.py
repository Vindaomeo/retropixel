#!/usr/bin/env python3
"""MX8 CLI -- drive the panel, list ports, sniff the protocol.

Examples:
    python mx8_cli.py ports
    python mx8_cli.py monitor                       # just listen, display stays on demo
    python mx8_cli.py demo --pattern plasma --fps 30 --seconds 20
    python mx8_cli.py image picture.png --seconds 10
    python mx8_cli.py preview --pattern fire        # no hardware, in the terminal
"""

from __future__ import annotations

import argparse
import sys
import time

from serial.tools import list_ports

from mx8 import Framebuffer, MatrixLink, PATTERNS, find_port
from mx8.link import LinkError
from mx8.protocol import MSG_LOG, MSG_NACK, MSG_READY, ERR_NAMES, Geometry


# ---------------------------------------------------------------------------
def cmd_ports(args) -> int:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return 1
    for p in ports:
        vid = f"{p.vid:04X}" if p.vid else "----"
        pid = f"{p.pid:04X}" if p.pid else "----"
        print(f"{p.device:<24} {vid}:{pid}  {p.description}")
    return 0


# ---------------------------------------------------------------------------
def cmd_monitor(args) -> int:
    """Only listens -- sends no FRAME, so the test pattern keeps running."""
    link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
    with link:
        print(f"Listening on {link.port} @ {args.baud} Baud ... (Ctrl-C quits)")
        t_end = time.monotonic() + args.seconds if args.seconds else float("inf")
        try:
            while time.monotonic() < t_end:
                try:
                    msg = link._await({MSG_READY, MSG_NACK}, timeout=1.5)
                except TimeoutError:
                    continue
                stamp = time.strftime("%H:%M:%S")
                if msg.cmd == MSG_READY:
                    print(f"{stamp}  READY  {Geometry.parse(msg.payload)}")
                else:
                    cmd, err = msg.payload[0], msg.payload[1]
                    print(f"{stamp}  NACK   to 0x{cmd:02X}: {ERR_NAMES.get(err, err)}")
        except KeyboardInterrupt:
            print()
    return 0


# ---------------------------------------------------------------------------
def _connect(args) -> MatrixLink:
    link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
    link.open()
    print(f"Port     {link.port} @ {args.baud} Baud")
    geo = link.wait_ready(timeout=args.ready_timeout)
    print(f"READY    {geo}")
    print(f"Ping     {link.ping() * 1000:.1f} ms")
    if args.brightness is not None:
        link.set_brightness(args.brightness)
        print(f"Brightness set to {args.brightness}")
    return link


def _drive(link: MatrixLink, render, seconds: float, fps: float) -> None:
    """Renders at ``fps`` until ``seconds`` are up, and reports statistics at the end."""
    geo = link.geometry
    fb = Framebuffer(geo.width, geo.height)
    period = 1.0 / fps
    t0 = time.monotonic()
    next_tick = t0
    late = 0

    try:
        while True:
            now = time.monotonic()
            t = now - t0
            if seconds and t >= seconds:
                break
            render(fb, t)
            link.send_frame(fb.to_bytes())

            next_tick += period
            slack = next_tick - time.monotonic()
            if slack > 0:
                time.sleep(slack)
            else:
                late += 1
                next_tick = time.monotonic()  # do not catch up, or it runs flat out
    except KeyboardInterrupt:
        print()

    dur = time.monotonic() - t0
    s = link.stats
    print(
        f"\n{s['frames_sent']} Frames in {dur:.1f} s "
        f"= {s['frames_sent'] / dur:.1f} fps  "
        f"(target {fps:g}, {late} late, {s['nacks']} NACK, "
        f"{s['crc_errors']} CRC errors)"
    )


def cmd_demo(args) -> int:
    render = PATTERNS[args.pattern]
    link = _connect(args)
    try:
        print(f"Sending '{args.pattern}' at {args.fps:g} fps ... (Ctrl-C quits)")
        _drive(link, render, args.seconds, args.fps)
        if args.release:
            link.release()
            print("Display released -- the Nano shows its test pattern again.")
        else:
            link.clear()
    finally:
        link.close()
    return 0


# ---------------------------------------------------------------------------
def cmd_image(args) -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Image mode needs Pillow:  pip install pillow")
        return 2

    link = _connect(args)
    try:
        geo = link.geometry
        img = Image.open(args.path).convert("RGB")
        frames: list[bytes] = []
        try:  # animated GIF?
            from PIL import ImageSequence

            for f in ImageSequence.Iterator(img):
                frames.append(
                    f.convert("RGB")
                    .resize((geo.width, geo.height), Image.LANCZOS)
                    .tobytes()
                )
        except Exception:
            frames = [img.resize((geo.width, geo.height), Image.LANCZOS).tobytes()]

        print(f"{args.path}: {len(frames)} frame(s) at {geo.width}x{geo.height}")
        if len(frames) == 1:
            link.send_frame(frames[0])
            time.sleep(args.seconds or 5)
        else:
            idx = 0
            _drive(
                link,
                lambda fb, t: fb.buf.__setitem__(
                    slice(None), frames[int(t * args.fps) % len(frames)]
                ),
                args.seconds,
                args.fps,
            )
        if args.release:
            link.release()
        else:
            link.clear()
    finally:
        link.close()
    return 0


# ---------------------------------------------------------------------------
def cmd_preview(args) -> int:
    """Show the animation in the terminal -- needs no hardware."""
    render = PATTERNS[args.pattern]
    fb = Framebuffer(8, 8)
    t0 = time.monotonic()
    print("\033[?25l", end="")  # cursor off
    try:
        while True:
            t = time.monotonic() - t0
            if args.seconds and t >= args.seconds:
                break
            render(fb, t)
            print("\033[H\033[J" + fb.to_ansi())
            time.sleep(1.0 / args.fps)
    except KeyboardInterrupt:
        pass
    finally:
        print("\033[?25h", end="")  # cursor on
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p, with_render=True):
        p.add_argument("-p", "--port", help="e.g. COM4 or /dev/ttyACM0 (otherwise MX8_PORT or auto)")
        p.add_argument("-b", "--baud", type=int, default=None,
                   help="default: MX8_BAUD, otherwise 115200")
        p.add_argument("--ready-timeout", type=float, default=8.0)
        p.add_argument("--brightness", type=int, help="0..255")
        if with_render:
            p.add_argument("--fps", type=float, default=30.0)
            p.add_argument("--seconds", type=float, default=0.0, help="0 = endless")
            p.add_argument(
                "--release",
                action="store_true",
                help="send RELEASE instead of CLEAR at the end (Nano shows the demo again)",
            )

    sub.add_parser("ports", help="list serial ports").set_defaults(func=cmd_ports)

    p = sub.add_parser("monitor", help="read READY/NACK/LOG without sending")
    p.add_argument("-p", "--port")
    p.add_argument("-b", "--baud", type=int, default=None,
                   help="default: MX8_BAUD, otherwise 115200")
    p.add_argument("--seconds", type=float, default=0.0)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("demo", help="send an animation to the panel")
    add_common(p)
    p.add_argument("--pattern", choices=sorted(PATTERNS), default="plasma")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("image", help="send an image or GIF to the panel")
    add_common(p)
    p.add_argument("path")
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("preview", help="animation in the terminal, no hardware")
    p.add_argument("--pattern", choices=sorted(PATTERNS), default="plasma")
    p.add_argument("--fps", type=float, default=20.0)
    p.add_argument("--seconds", type=float, default=0.0)
    p.set_defaults(func=cmd_preview)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except LinkError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except TimeoutError as e:
        print(f"Timeout: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

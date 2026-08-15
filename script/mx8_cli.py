#!/usr/bin/env python3
"""MX8 CLI -- Panel ansteuern, Ports auflisten, Protokoll mitschneiden.

Beispiele:
    python mx8_cli.py ports
    python mx8_cli.py monitor                       # nur zuhoeren, Display bleibt Demo
    python mx8_cli.py demo --pattern plasma --fps 30 --seconds 20
    python mx8_cli.py image bild.png --seconds 10
    python mx8_cli.py preview --pattern fire        # ohne Hardware, im Terminal
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
        print("Keine seriellen Ports gefunden.")
        return 1
    for p in ports:
        vid = f"{p.vid:04X}" if p.vid else "----"
        pid = f"{p.pid:04X}" if p.pid else "----"
        print(f"{p.device:<24} {vid}:{pid}  {p.description}")
    return 0


# ---------------------------------------------------------------------------
def cmd_monitor(args) -> int:
    """Hoert nur zu -- sendet kein FRAME, das Testpattern laeuft also weiter."""
    link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
    with link:
        print(f"Lausche auf {link.port} @ {args.baud} Baud ... (Strg-C beendet)")
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
                    print(f"{stamp}  NACK   auf 0x{cmd:02X}: {ERR_NAMES.get(err, err)}")
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
        print(f"Helligkeit auf {args.brightness} gesetzt")
    return link


def _drive(link: MatrixLink, render, seconds: float, fps: float) -> None:
    """Rendert mit ``fps`` bis ``seconds`` um sind, und meldet am Ende Statistik."""
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
                next_tick = time.monotonic()  # nicht aufholen, sonst Vollgas
    except KeyboardInterrupt:
        print()

    dur = time.monotonic() - t0
    s = link.stats
    print(
        f"\n{s['frames_sent']} Frames in {dur:.1f} s "
        f"= {s['frames_sent'] / dur:.1f} fps  "
        f"(Ziel {fps:g}, {late} verspaetet, {s['nacks']} NACK, "
        f"{s['crc_errors']} CRC-Fehler)"
    )


def cmd_demo(args) -> int:
    render = PATTERNS[args.pattern]
    link = _connect(args)
    try:
        print(f"Sende '{args.pattern}' mit {args.fps:g} fps ... (Strg-C beendet)")
        _drive(link, render, args.seconds, args.fps)
        if args.release:
            link.release()
            print("Display freigegeben -- der Nano zeigt wieder sein Testpattern.")
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
        print("Fuer den Bild-Modus wird Pillow gebraucht:  pip install pillow")
        return 2

    link = _connect(args)
    try:
        geo = link.geometry
        img = Image.open(args.path).convert("RGB")
        frames: list[bytes] = []
        try:  # animiertes GIF?
            from PIL import ImageSequence

            for f in ImageSequence.Iterator(img):
                frames.append(
                    f.convert("RGB")
                    .resize((geo.width, geo.height), Image.LANCZOS)
                    .tobytes()
                )
        except Exception:
            frames = [img.resize((geo.width, geo.height), Image.LANCZOS).tobytes()]

        print(f"{args.path}: {len(frames)} Frame(s) auf {geo.width}x{geo.height}")
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
    """Animation im Terminal anzeigen -- braucht keine Hardware."""
    render = PATTERNS[args.pattern]
    fb = Framebuffer(8, 8)
    t0 = time.monotonic()
    print("\033[?25l", end="")  # Cursor aus
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
        print("\033[?25h", end="")  # Cursor an
    return 0


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p, with_render=True):
        p.add_argument("-p", "--port", help="z. B. COM5 oder /dev/ttyACM0 (sonst Auto)")
        p.add_argument("-b", "--baud", type=int, default=115200)
        p.add_argument("--ready-timeout", type=float, default=8.0)
        p.add_argument("--brightness", type=int, help="0..255")
        if with_render:
            p.add_argument("--fps", type=float, default=30.0)
            p.add_argument("--seconds", type=float, default=0.0, help="0 = endlos")
            p.add_argument(
                "--release",
                action="store_true",
                help="am Ende RELEASE senden statt CLEAR (Nano zeigt wieder Demo)",
            )

    sub.add_parser("ports", help="serielle Ports auflisten").set_defaults(func=cmd_ports)

    p = sub.add_parser("monitor", help="READY/NACK/LOG mitlesen, ohne zu senden")
    p.add_argument("-p", "--port")
    p.add_argument("-b", "--baud", type=int, default=115200)
    p.add_argument("--seconds", type=float, default=0.0)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("demo", help="Animation aufs Panel schicken")
    add_common(p)
    p.add_argument("--pattern", choices=sorted(PATTERNS), default="plasma")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("image", help="Bild oder GIF aufs Panel schicken")
    add_common(p)
    p.add_argument("path")
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("preview", help="Animation im Terminal, ohne Hardware")
    p.add_argument("--pattern", choices=sorted(PATTERNS), default="plasma")
    p.add_argument("--fps", type=float, default=20.0)
    p.add_argument("--seconds", type=float, default=0.0)
    p.set_defaults(func=cmd_preview)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except LinkError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    except TimeoutError as e:
        print(f"Timeout: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

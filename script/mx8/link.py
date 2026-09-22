"""MatrixLink -- serial connection to the Arduino."""

from __future__ import annotations

import os
import queue
import struct
import threading
import time
from typing import Optional

import serial
from serial.tools import list_ports

from .protocol import (
    CMD_CLEAR,
    CMD_FRAME,
    CMD_HELLO,
    CMD_PING,
    CMD_RAW,
    CMD_RELEASE,
    CMD_SET_BRIGHTNESS,
    ERR_NAMES,
    MSG_ACK,
    MSG_LOG,
    MSG_NACK,
    MSG_READY,
    FrameParser,
    Geometry,
    Message,
    ParseError,
    encode,
)

#: Environment variables, so you don't have to type port/baud on every call.
#: Example (PowerShell):  $env:MX8_PORT = "COM4"
ENV_PORT = "MX8_PORT"
ENV_BAUD = "MX8_BAUD"

DEFAULT_BAUD = 115200

#: Rates that ``wait_ready`` tries if no READY arrives at the configured
#: value. Descending, so the fast connection wins first.
#: With native USB CDC (Pico) the number has no effect anyway -- there the
#: first attempt hits. An explicit ``-b`` disables the search.
BAUD_CANDIDATES = (1_000_000, 500_000, 250_000, 115_200)

# Genuine Arduino boards report their own USB VID (Nano Every via its
# SAMD11 bridge chip, Uno/Mega via the 16u2).
_ARDUINO_VIDS = {
    0x2341: "Arduino",
    0x2A03: "Arduino (arduino.org)",
    0x1B4F: "SparkFun",
    0x239A: "Adafruit",
}

# Clones and the classic Nano sit behind a generic USB-serial converter.
# These VIDs are NOT Arduino-specific -- a radio module or a measuring
# instrument could just as well be behind them. Hence second choice only, and
# we ask as soon as there is more than one candidate.
_BRIDGE_VIDS = {
    0x1A86: "CH340/CH341 (QinHeng)",
    0x0403: "FTDI",
    0x10C4: "CP210x (Silicon Labs)",
    0x067B: "PL2303 (Prolific)",
}


class LinkError(RuntimeError):
    pass


class NackError(LinkError):
    def __init__(self, cmd: int, err: int) -> None:
        self.cmd, self.err = cmd, err
        super().__init__(f"NACK on 0x{cmd:02X}: {ERR_NAMES.get(err, f'code {err}')}")


def _describe(ports) -> str:
    if not ports:
        return "no serial ports found"
    return "\n".join(
        f"    {p.device:<14} {p.description}"
        + (f"  [VID {p.vid:04X}:{p.pid:04X}]" if p.vid else "")
        for p in ports
    )


def find_port(hint: Optional[str] = None) -> str:
    """Finds the Arduino port.

    Order: explicit parameter -> environment variable MX8_PORT ->
    board with an Arduino USB ID -> generic USB-serial converter (CH340, FTDI,
    CP210x, PL2303), as found on the classic Nano and most clones.

    With several converters we deliberately don't guess: you get an
    error message with the list, and pick one with ``-p``.
    """
    if hint:
        return hint
    env = os.environ.get(ENV_PORT)
    if env:
        return env

    ports = list(list_ports.comports())

    # 1) Genuine Arduino board -- unambiguous enough to just take it.
    for p in ports:
        if p.vid in _ARDUINO_VIDS:
            return p.device

    # 2) Generic USB-serial converter (classic Nano, clones).
    bridges = [p for p in ports if p.vid in _BRIDGE_VIDS]
    if len(bridges) == 1:
        return bridges[0].device
    if len(bridges) > 1:
        raise LinkError(
            "Several USB-serial converters found -- please pick one with -p:\n"
            + _describe(bridges)
        )

    # 3) Last attempt via the plain-text name.
    for p in ports:
        if any(k in (p.description or "").lower() for k in ("arduino", "nano", "usb-serial")):
            return p.device

    raise LinkError(
        "No Arduino found. Please specify the port with -p (e.g. -p COM4)\n"
        f"or set it permanently via the environment variable {ENV_PORT}.\n"
        "  Available ports:\n" + _describe(ports)
    )


def default_baud() -> int:
    """Baud rate from MX8_BAUD, otherwise 115200. Must match MX_SERIAL_BAUD."""
    raw = os.environ.get(ENV_BAUD)
    if not raw:
        return DEFAULT_BAUD
    try:
        return int(raw)
    except ValueError:
        raise LinkError(f"{ENV_BAUD}={raw!r} is not a valid baud rate")


def baud_search_order(first: int) -> list[int]:
    """``first`` first, then the remaining candidates without repeats."""
    return [first] + [b for b in BAUD_CANDIDATES if b != first]


class MatrixLink:
    """Connection to the panel.

    A background thread drains the port and dispatches the messages:
    READY/ACK/NACK into a queue, LOG into a callback. That way asynchronous
    READY tokens never get lost while we are waiting for an ACK.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: Optional[int] = None,
        ack_timeout: float = 0.5,
        on_log=None,
    ) -> None:
        self.port = find_port(port)
        #: Was the rate given explicitly (argument or MX8_BAUD)? Then
        #: wait_ready doesn't search -- whoever sets it means it.
        self.baud_is_explicit = baud is not None or os.environ.get(ENV_BAUD) is not None
        self.baud = baud if baud is not None else default_baud()
        self.ack_timeout = ack_timeout
        self.on_log = on_log or (lambda text: None)

        self._ser: Optional[serial.Serial] = None
        self._parser = FrameParser()
        self._rx: "queue.Queue[Message | ParseError]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tx_lock = threading.Lock()

        self.geometry: Optional[Geometry] = None
        self.stats = {"frames_sent": 0, "acks": 0, "nacks": 0, "crc_errors": 0}

    # -- Lifecycle ---------------------------------------------------------
    def open(self, reset_wait: float = 2.0) -> "MatrixLink":
        self._ser = serial.Serial(self.port, self.baud, timeout=0.05)
        # The Nano Every resets when the port is opened (DTR). Wait a moment,
        # otherwise our first bytes end up in the bootloader.
        time.sleep(reset_wait)
        self._ser.reset_input_buffer()
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._ser:
            self._ser.close()
            self._ser = None

    def __enter__(self) -> "MatrixLink":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- RX thread ---------------------------------------------------------
    def _reader(self) -> None:
        assert self._ser is not None
        while not self._stop.is_set():
            try:
                data = self._ser.read(4096)
            except (serial.SerialException, OSError, TypeError):
                break
            if not data:
                continue
            for msg in self._parser.feed(data):
                if isinstance(msg, ParseError):
                    self.stats["crc_errors"] += 1
                    continue
                if msg.cmd == MSG_LOG:
                    self.on_log(msg.payload.decode("ascii", "replace"))
                else:
                    self._rx.put(msg)

    def _send(self, cmd: int, payload: bytes = b"") -> None:
        if self._ser is None:
            raise LinkError("Port not open")
        with self._tx_lock:
            self._ser.write(encode(cmd, payload))

    def _await(self, wanted: set[int], timeout: float) -> Message:
        """Waits for a message from ``wanted``; everything else is discarded."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "No response ("
                    + "/".join(f"0x{c:02X}" for c in sorted(wanted))
                    + f") within {timeout:.2f} s"
                )
            try:
                msg = self._rx.get(timeout=remaining)
            except queue.Empty:
                continue
            if msg.cmd == MSG_READY:
                self.geometry = Geometry.parse(msg.payload)
            if msg.cmd in wanted:
                return msg

    # -- Public API --------------------------------------------------------
    def wait_ready(self, timeout: float = 8.0) -> Geometry:
        """Waits for the once-per-second READY token and returns the geometry.

        If nothing arrives at the configured rate, the rest of
        BAUD_CANDIDATES are tried. That removes the most common pitfall:
        MX_SERIAL_BAUD raised in the firmware, forgotten on the host.
        An explicit ``-b`` or MX8_BAUD disables the search.
        """
        candidates = ([self.baud] if self.baud_is_explicit
                      else baud_search_order(self.baud))
        per_try = timeout if len(candidates) == 1 else max(2.5, timeout / len(candidates))

        for i, baud in enumerate(candidates):
            if i:                                  # reopen port at the new rate
                self.on_log(f"no READY at {candidates[i - 1]} baud, "
                            f"trying {baud}")
                self.close()
                self.baud = baud
                self.open(reset_wait=0.5)
            try:
                self._send(CMD_HELLO)   # don't wait -- it announces itself anyway
                self._await({MSG_READY}, per_try)
            except TimeoutError:
                continue
            assert self.geometry is not None
            return self.geometry

        raise TimeoutError(
            "No READY token received. Tried: "
            + ", ".join(str(b) for b in candidates)
            + ".\n  Is a serial monitor attached to the same port? "
            "Is MX_SERIAL_BAUD in FW/mx8_config.h right?"
        )

    def _cmd_ack(self, cmd: int, payload: bytes = b"") -> None:
        self._send(cmd, payload)
        msg = self._await({MSG_ACK, MSG_NACK}, self.ack_timeout)
        if msg.cmd == MSG_NACK:
            self.stats["nacks"] += 1
            raise NackError(msg.payload[0], msg.payload[1])
        self.stats["acks"] += 1

    def ping(self) -> float:
        """Round-trip time in seconds. Does NOT interrupt the test pattern."""
        t0 = time.monotonic()
        self._cmd_ack(CMD_PING)
        return time.monotonic() - t0

    def set_brightness(self, value: int) -> None:
        self._cmd_ack(CMD_SET_BRIGHTNESS, bytes((max(0, min(255, int(value))),)))

    def clear(self) -> None:
        self._cmd_ack(CMD_CLEAR)

    def release(self) -> None:
        """Release the display -- the Nano falls back to the test pattern."""
        self._cmd_ack(CMD_RELEASE)

    def send_frame(self, pixels: bytes) -> None:
        """Sends a full frame (row-major RGB) and waits for the ACK.

        Waiting for the ACK is the flow control: FastLED disables interrupts
        during show(), and unthrottled sending would lose RX bytes.
        """
        self._cmd_ack(CMD_FRAME, self._check_frame(pixels))
        self.stats["frames_sent"] += 1

    def send_frame_nowait(self, pixels: bytes) -> None:
        """Sends a full frame WITHOUT waiting for the ACK.

        Only for measurements and stress tests (see speedtest.py). In normal
        operation it's the wrong choice: without flow control you overrun the
        Nano's receive buffer as soon as FastLED disables interrupts in show().
        """
        self._send(CMD_FRAME, self._check_frame(pixels))
        self.stats["frames_sent"] += 1

    def send_raw(self, start: int, pixels: bytes) -> None:
        """Writes straight into the LED chain WITHOUT pixel mapping.

        ``start`` is the chain index of the first pixel, ``pixels`` are
        3*n bytes of RGB. All other LEDs go black.

        Only meant for measuring the panel wiring (script/mapcheck.py):
        because nothing is reordered here, the panel shows the chain the way
        it is physically soldered.
        """
        if len(pixels) % 3:
            raise ValueError(f"pixels needs a multiple of 3 bytes, has {len(pixels)}")
        if start < 0 or start > 0xFFFF:
            raise ValueError(f"start {start} outside 0..65535")
        self._cmd_ack(CMD_RAW, struct.pack("<H", start) + bytes(pixels))

    def _check_frame(self, pixels: bytes) -> bytes:
        expected = self.geometry.frame_bytes if self.geometry else 192
        if len(pixels) != expected:
            raise ValueError(f"Frame needs {expected} B, got {len(pixels)}")
        return bytes(pixels)

    def drain(self) -> tuple[int, int]:
        """Collects everything currently in the RX queue, without blocking.

        Returns: (number of ACKs, number of NACKs). For flood mode, where
        responses are not matched up individually, only counted.
        """
        acks = nacks = 0
        while True:
            try:
                msg = self._rx.get_nowait()
            except queue.Empty:
                return acks, nacks
            if msg.cmd == MSG_ACK:
                acks += 1
                self.stats["acks"] += 1
            elif msg.cmd == MSG_NACK:
                nacks += 1
                self.stats["nacks"] += 1
            elif msg.cmd == MSG_READY:
                self.geometry = Geometry.parse(msg.payload)

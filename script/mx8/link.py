"""MatrixLink -- serielle Verbindung zum Nano Every."""

from __future__ import annotations

import queue
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

# Arduino / Nano Every USB-IDs (Nano Every meldet sich ueber einen SAMD11-Bridge-Chip)
_ARDUINO_VIDS = {0x2341, 0x2A03, 0x1B4F}


class LinkError(RuntimeError):
    pass


class NackError(LinkError):
    def __init__(self, cmd: int, err: int) -> None:
        self.cmd, self.err = cmd, err
        super().__init__(f"NACK auf 0x{cmd:02X}: {ERR_NAMES.get(err, f'Code {err}')}")


def find_port(hint: Optional[str] = None) -> str:
    """Findet den Nano-Every-Port. ``hint`` erzwingt einen bestimmten Port."""
    if hint:
        return hint
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid in _ARDUINO_VIDS:
            return p.device
    for p in ports:
        if "nano" in (p.description or "").lower():
            return p.device
    raise LinkError(
        "Kein Arduino-Port gefunden. Verfuegbar: "
        + (", ".join(f"{p.device} ({p.description})" for p in ports) or "keine")
    )


class MatrixLink:
    """Verbindung zum Panel.

    Ein Hintergrund-Thread liest den Port leer und verteilt die Messages:
    READY/ACK/NACK in eine Queue, LOG in einen Callback. Damit gehen
    asynchrone READY-Tokens nie verloren, waehrend wir auf ein ACK warten.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = 115200,
        ack_timeout: float = 0.5,
        on_log=None,
    ) -> None:
        self.port = find_port(port)
        self.baud = baud
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
        # Der Nano Every resettet beim Oeffnen des Ports (DTR). Kurz warten,
        # sonst landen unsere ersten Bytes im Bootloader.
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

    # -- RX-Thread ---------------------------------------------------------
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
            raise LinkError("Port nicht geoeffnet")
        with self._tx_lock:
            self._ser.write(encode(cmd, payload))

    def _await(self, wanted: set[int], timeout: float) -> Message:
        """Wartet auf eine Message aus ``wanted``; alles andere wird verworfen."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Keine Antwort ("
                    + "/".join(f"0x{c:02X}" for c in sorted(wanted))
                    + f") innerhalb {timeout:.2f} s"
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
        """Wartet auf das sekuendliche READY-Token und liefert die Geometrie."""
        self._send(CMD_HELLO)  # nicht warten -- der Nano meldet sich ohnehin
        self._await({MSG_READY}, timeout)
        assert self.geometry is not None
        return self.geometry

    def _cmd_ack(self, cmd: int, payload: bytes = b"") -> None:
        self._send(cmd, payload)
        msg = self._await({MSG_ACK, MSG_NACK}, self.ack_timeout)
        if msg.cmd == MSG_NACK:
            self.stats["nacks"] += 1
            raise NackError(msg.payload[0], msg.payload[1])
        self.stats["acks"] += 1

    def ping(self) -> float:
        """Round-Trip-Zeit in Sekunden. Unterbricht das Testpattern NICHT."""
        t0 = time.monotonic()
        self._cmd_ack(CMD_PING)
        return time.monotonic() - t0

    def set_brightness(self, value: int) -> None:
        self._cmd_ack(CMD_SET_BRIGHTNESS, bytes((max(0, min(255, int(value))),)))

    def clear(self) -> None:
        self._cmd_ack(CMD_CLEAR)

    def release(self) -> None:
        """Display freigeben -- der Nano faellt zurueck aufs Testpattern."""
        self._cmd_ack(CMD_RELEASE)

    def send_frame(self, pixels: bytes) -> None:
        """Sendet ein Vollbild (row-major RGB) und wartet auf das ACK.

        Das ACK-Warten ist die Flusskontrolle: FastLED sperrt waehrend show()
        die Interrupts, ungebremstes Senden wuerde RX-Bytes verlieren.
        """
        self._cmd_ack(CMD_FRAME, self._check_frame(pixels))
        self.stats["frames_sent"] += 1

    def send_frame_nowait(self, pixels: bytes) -> None:
        """Sendet ein Vollbild OHNE auf das ACK zu warten.

        Nur fuer Messungen und Stresstests (siehe speedtest.py). Im Normalbetrieb
        ist das die falsche Wahl: ohne Flusskontrolle ueberfaehrt man den
        RX-Puffer des Nano, sobald FastLED in show() die Interrupts sperrt.
        """
        self._send(CMD_FRAME, self._check_frame(pixels))
        self.stats["frames_sent"] += 1

    def _check_frame(self, pixels: bytes) -> bytes:
        expected = self.geometry.frame_bytes if self.geometry else 192
        if len(pixels) != expected:
            raise ValueError(f"Frame braucht {expected} B, bekam {len(pixels)}")
        return bytes(pixels)

    def drain(self) -> tuple[int, int]:
        """Holt alles ab, was gerade in der RX-Queue liegt, ohne zu blocken.

        Rueckgabe: (Anzahl ACK, Anzahl NACK). Fuer den Flood-Modus, in dem
        Antworten nicht einzeln zugeordnet, sondern nur gezaehlt werden.
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

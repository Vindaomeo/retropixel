"""MatrixLink -- serielle Verbindung zum Arduino."""

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

#: Umgebungsvariablen, damit man Port/Baud nicht bei jedem Aufruf tippen muss.
#: Beispiel (PowerShell):  $env:MX8_PORT = "COM4"
ENV_PORT = "MX8_PORT"
ENV_BAUD = "MX8_BAUD"

DEFAULT_BAUD = 115200

#: Raten, die ``wait_ready`` durchprobiert, wenn beim eingestellten Wert kein
#: READY kommt. Absteigend, damit die schnelle Verbindung zuerst gewinnt.
#: Bei nativem USB-CDC (Pico) ist die Zahl ohnehin wirkungslos -- dort trifft
#: gleich der erste Versuch. Ein explizites ``-b`` schaltet die Suche ab.
BAUD_CANDIDATES = (1_000_000, 500_000, 250_000, 115_200)

# Original-Arduino-Boards melden sich mit eigener USB-VID (Nano Every ueber
# seinen SAMD11-Bridge-Chip, Uno/Mega ueber den 16u2).
_ARDUINO_VIDS = {
    0x2341: "Arduino",
    0x2A03: "Arduino (arduino.org)",
    0x1B4F: "SparkFun",
    0x239A: "Adafruit",
}

# Klone und der klassische Nano haengen an einem generischen USB-Seriell-Wandler.
# Diese VIDs sind NICHT arduino-spezifisch -- daran kann genauso ein Funkmodul
# oder ein Messgeraet haengen. Deshalb nur als zweite Wahl und mit Rueckfrage,
# sobald mehr als ein Kandidat da ist.
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
        super().__init__(f"NACK auf 0x{cmd:02X}: {ERR_NAMES.get(err, f'Code {err}')}")


def _describe(ports) -> str:
    if not ports:
        return "keine seriellen Ports gefunden"
    return "\n".join(
        f"    {p.device:<14} {p.description}"
        + (f"  [VID {p.vid:04X}:{p.pid:04X}]" if p.vid else "")
        for p in ports
    )


def find_port(hint: Optional[str] = None) -> str:
    """Sucht den Arduino-Port.

    Reihenfolge: expliziter Parameter -> Umgebungsvariable MX8_PORT ->
    Board mit Arduino-USB-ID -> generischer USB-Seriell-Wandler (CH340, FTDI,
    CP210x, PL2303), wie ihn der klassische Nano und die meisten Klone haben.

    Bei mehreren Wandlern wird bewusst nicht geraten: dann kommt eine
    Fehlermeldung mit der Liste, und man waehlt mit ``-p`` aus.
    """
    if hint:
        return hint
    env = os.environ.get(ENV_PORT)
    if env:
        return env

    ports = list(list_ports.comports())

    # 1) Echtes Arduino-Board -- eindeutig genug, um es einfach zu nehmen.
    for p in ports:
        if p.vid in _ARDUINO_VIDS:
            return p.device

    # 2) Generischer USB-Seriell-Wandler (klassischer Nano, Klone).
    bridges = [p for p in ports if p.vid in _BRIDGE_VIDS]
    if len(bridges) == 1:
        return bridges[0].device
    if len(bridges) > 1:
        raise LinkError(
            "Mehrere USB-Seriell-Wandler gefunden — bitte mit -p auswaehlen:\n"
            + _describe(bridges)
        )

    # 3) Letzter Versuch ueber den Klartext-Namen.
    for p in ports:
        if any(k in (p.description or "").lower() for k in ("arduino", "nano", "usb-serial")):
            return p.device

    raise LinkError(
        "Kein Arduino gefunden. Port bitte mit -p angeben (z. B. -p COM4)\n"
        f"oder dauerhaft per Umgebungsvariable {ENV_PORT} setzen.\n"
        "  Verfuegbare Ports:\n" + _describe(ports)
    )


def default_baud() -> int:
    """Baudrate aus MX8_BAUD, sonst 115200. Muss zu MX_SERIAL_BAUD passen."""
    raw = os.environ.get(ENV_BAUD)
    if not raw:
        return DEFAULT_BAUD
    try:
        return int(raw)
    except ValueError:
        raise LinkError(f"{ENV_BAUD}={raw!r} ist keine gueltige Baudrate")


def baud_search_order(first: int) -> list[int]:
    """``first`` zuerst, danach die uebrigen Kandidaten ohne Wiederholung."""
    return [first] + [b for b in BAUD_CANDIDATES if b != first]


class MatrixLink:
    """Verbindung zum Panel.

    Ein Hintergrund-Thread liest den Port leer und verteilt die Messages:
    READY/ACK/NACK in eine Queue, LOG in einen Callback. Damit gehen
    asynchrone READY-Tokens nie verloren, waehrend wir auf ein ACK warten.
    """

    def __init__(
        self,
        port: Optional[str] = None,
        baud: Optional[int] = None,
        ack_timeout: float = 0.5,
        on_log=None,
    ) -> None:
        self.port = find_port(port)
        #: Wurde die Rate explizit vorgegeben (Argument oder MX8_BAUD)? Dann
        #: sucht wait_ready nicht -- wer sie setzt, meint sie auch so.
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
        """Wartet auf das sekuendliche READY-Token und liefert die Geometrie.

        Kommt bei der eingestellten Rate nichts, werden die uebrigen aus
        BAUD_CANDIDATES durchprobiert. Das nimmt die haeufigste Stolperfalle
        raus: MX_SERIAL_BAUD in der Firmware erhoeht, auf dem Host vergessen.
        Ein explizites ``-b`` bzw. MX8_BAUD schaltet die Suche ab.
        """
        candidates = ([self.baud] if self.baud_is_explicit
                      else baud_search_order(self.baud))
        per_try = timeout if len(candidates) == 1 else max(2.5, timeout / len(candidates))

        for i, baud in enumerate(candidates):
            if i:                                  # Port mit neuer Rate neu oeffnen
                self.on_log(f"kein READY bei {candidates[i - 1]} Baud, "
                            f"versuche {baud}")
                self.close()
                self.baud = baud
                self.open(reset_wait=0.5)
            try:
                self._send(CMD_HELLO)   # nicht warten -- er meldet sich ohnehin
                self._await({MSG_READY}, per_try)
            except TimeoutError:
                continue
            assert self.geometry is not None
            return self.geometry

        raise TimeoutError(
            "Kein READY-Token empfangen. Probiert: "
            + ", ".join(str(b) for b in candidates)
            + ".\n  Haengt ein serieller Monitor am selben Port? "
            "Stimmt MX_SERIAL_BAUD in FW/mx8_config.h?"
        )

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

    def send_raw(self, start: int, pixels: bytes) -> None:
        """Schreibt OHNE Pixel-Mapping direkt in die LED-Kette.

        ``start`` ist der Kettenindex des ersten Pixels, ``pixels`` sind
        3*n Byte RGB. Alle uebrigen LEDs werden schwarz.

        Nur zum Ausmessen der Panel-Verdrahtung gedacht (script/mapcheck.py):
        weil hier nichts umsortiert wird, zeigt das Panel die Kette so, wie
        sie physisch verloetet ist.
        """
        if len(pixels) % 3:
            raise ValueError(f"pixels braucht ein Vielfaches von 3 Byte, hat {len(pixels)}")
        if start < 0 or start > 0xFFFF:
            raise ValueError(f"start {start} ausserhalb 0..65535")
        self._cmd_ack(CMD_RAW, struct.pack("<H", start) + bytes(pixels))

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

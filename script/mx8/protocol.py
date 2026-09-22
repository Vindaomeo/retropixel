"""MX8 binary protocol -- framing, CRC and incremental parser.

Mirror image of FW/mx8_protocol.h.
Always apply changes to BOTH files.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator, NamedTuple

# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------
SYNC0 = 0xA5
SYNC1 = 0x5A
SYNC = bytes((SYNC0, SYNC1))
OVERHEAD_BYTES = 7  # SYNC0 SYNC1 CMD LEN_LO LEN_HI ... CRC_LO CRC_HI
MAX_PAYLOAD = 4096  # == MX_PROTO_MAX_PAYLOAD in FW/mx8_protocol.h

# PC -> Nano
CMD_FRAME = 0x01
CMD_PING = 0x02
CMD_SET_BRIGHTNESS = 0x03
CMD_CLEAR = 0x04
CMD_HELLO = 0x05
CMD_RELEASE = 0x06
CMD_RAW = 0x07  # straight into the chain, no mapping (script/mapcheck.py)

# Nano -> PC
MSG_READY = 0x81
MSG_ACK = 0x82
MSG_NACK = 0x83
MSG_LOG = 0x84

CMD_NAMES = {
    CMD_FRAME: "FRAME",
    CMD_PING: "PING",
    CMD_SET_BRIGHTNESS: "SET_BRIGHTNESS",
    CMD_CLEAR: "CLEAR",
    CMD_HELLO: "HELLO",
    CMD_RELEASE: "RELEASE",
    CMD_RAW: "RAW",
    MSG_READY: "READY",
    MSG_ACK: "ACK",
    MSG_NACK: "NACK",
    MSG_LOG: "LOG",
}

# NACK error codes
ERR_CRC = 0x01
ERR_LENGTH = 0x02
ERR_UNKNOWN_CMD = 0x03
ERR_TOO_LONG = 0x04
ERR_RX_TIMEOUT = 0x05

ERR_NAMES = {
    ERR_CRC: "CRC error",
    ERR_LENGTH: "wrong payload length",
    ERR_UNKNOWN_CMD: "unknown command",
    ERR_TOO_LONG: "frame too long",
    ERR_RX_TIMEOUT: "RX timeout / resync",
}

COLOR_ORDERS = ["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"]


# ---------------------------------------------------------------------------
# CRC-16/CCITT-FALSE : poly 0x1021, init 0xFFFF, no reflect, no XorOut
# ---------------------------------------------------------------------------
def _build_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC_TABLE = _build_table()


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE over ``data``, can be continued via ``crc``."""
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------
def encode(cmd: int, payload: bytes = b"") -> bytes:
    """Builds a ready-to-send frame."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"Payload {len(payload)} B > MAX_PAYLOAD {MAX_PAYLOAD} B")
    body = struct.pack("<BH", cmd, len(payload)) + payload
    return SYNC + body + struct.pack("<H", crc16(body))


# ---------------------------------------------------------------------------
# Decode (incremental)
# ---------------------------------------------------------------------------
class Message(NamedTuple):
    cmd: int
    payload: bytes

    @property
    def name(self) -> str:
        return CMD_NAMES.get(self.cmd, f"0x{self.cmd:02X}")


class ParseError(NamedTuple):
    reason: str
    cmd: int


class FrameParser:
    """Byte stream in, complete messages out.

    Tolerates garbage between frames: it resynchronizes byte by byte on the
    start token. CRC errors are reported as ParseError, not raised.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.crc_errors = 0
        self.resyncs = 0

    def feed(self, data: bytes) -> Iterator[Message | ParseError]:
        self._buf.extend(data)
        while True:
            start = self._buf.find(SYNC)
            if start < 0:
                # Last byte could be the start of a token -> keep it.
                keep = 1 if self._buf[-1:] == SYNC[:1] else 0
                if len(self._buf) > keep:
                    self.resyncs += 1
                del self._buf[: len(self._buf) - keep]
                return
            if start > 0:
                self.resyncs += 1
                del self._buf[:start]

            if len(self._buf) < 5:
                return
            cmd, length = struct.unpack_from("<BH", self._buf, 2)
            if length > MAX_PAYLOAD:
                # Can't be a valid header -> skip past the token.
                del self._buf[:2]
                self.resyncs += 1
                continue
            total = OVERHEAD_BYTES + length
            if len(self._buf) < total:
                return

            body = bytes(self._buf[2 : 5 + length])
            (recv_crc,) = struct.unpack_from("<H", self._buf, 5 + length)
            del self._buf[:total]

            if recv_crc != crc16(body):
                self.crc_errors += 1
                yield ParseError("CRC mismatch", cmd)
            else:
                yield Message(cmd, body[3:])


# ---------------------------------------------------------------------------
# READY geometry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Geometry:
    proto: int
    width: int
    height: int
    bpp: int
    color_order: int
    layout: int
    fw_major: int
    fw_minor: int

    @classmethod
    def parse(cls, payload: bytes) -> "Geometry":
        if len(payload) != 8:
            raise ValueError(f"READY expects 8 B payload, got {len(payload)}")
        return cls(*payload)

    @property
    def num_pixels(self) -> int:
        return self.width * self.height

    @property
    def frame_bytes(self) -> int:
        return self.num_pixels * self.bpp

    @property
    def order_name(self) -> str:
        if self.color_order < len(COLOR_ORDERS):
            return COLOR_ORDERS[self.color_order]
        return "?"

    def __str__(self) -> str:
        layout = "serpentine" if self.layout else "progressive"
        return (
            f"{self.width}x{self.height}x{self.bpp} ({self.order_name}, {layout}) "
            f"proto v{self.proto} fw {self.fw_major}.{self.fw_minor} "
            f"-> {self.frame_bytes} B/frame"
        )

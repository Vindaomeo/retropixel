"""MX8 -- host library for the 8x8 RGB matrix on the Arduino Nano Every."""

from .framebuffer import Framebuffer
from .link import (
    DEFAULT_BAUD,
    ENV_BAUD,
    ENV_PORT,
    LinkError,
    MatrixLink,
    NackError,
    default_baud,
    find_port,
)
from .patterns import PATTERNS
from .protocol import FrameParser, Geometry, Message, crc16, encode

__all__ = [
    "Framebuffer",
    "MatrixLink",
    "LinkError",
    "NackError",
    "find_port",
    "default_baud",
    "ENV_PORT",
    "ENV_BAUD",
    "DEFAULT_BAUD",
    "PATTERNS",
    "FrameParser",
    "Geometry",
    "Message",
    "crc16",
    "encode",
]
__version__ = "1.0.0"

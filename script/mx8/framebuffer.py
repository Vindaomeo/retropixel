"""Minimal framebuffer -- pure Python, no dependencies."""

from __future__ import annotations

import colorsys


class Framebuffer:
    """Row-major RGB buffer. Row 0 = top, column 0 = left."""

    __slots__ = ("width", "height", "buf")

    def __init__(self, width: int = 8, height: int = 8) -> None:
        self.width = width
        self.height = height
        self.buf = bytearray(width * height * 3)

    # -- Access ------------------------------------------------------------
    def set(self, x: int, y: int, r: int, g: int, b: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self.buf[i] = r & 0xFF
            self.buf[i + 1] = g & 0xFF
            self.buf[i + 2] = b & 0xFF

    def get(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.buf[i], self.buf[i + 1], self.buf[i + 2]

    def set_hsv(self, x: int, y: int, h: float, s: float = 1.0, v: float = 1.0) -> None:
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
        self.set(x, y, int(r * 255), int(g * 255), int(b * 255))

    # -- Whole area --------------------------------------------------------
    def fill(self, r: int = 0, g: int = 0, b: int = 0) -> None:
        self.buf[:] = bytes((r & 0xFF, g & 0xFF, b & 0xFF)) * (self.width * self.height)

    def clear(self) -> None:
        self.buf[:] = bytes(len(self.buf))

    def fade(self, factor: float) -> None:
        """Darken all pixels multiplicatively (0.0 = black, 1.0 = unchanged)."""
        f = max(0.0, min(1.0, factor))
        self.buf[:] = bytes(int(v * f) for v in self.buf)

    def to_bytes(self) -> bytes:
        return bytes(self.buf)

    # -- Debug -------------------------------------------------------------
    def to_ansi(self) -> str:
        """Preview in the terminal -- handy with no hardware attached."""
        out = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                r, g, b = self.get(x, y)
                row.append(f"\033[48;2;{r};{g};{b}m  ")
            out.append("".join(row) + "\033[0m")
        return "\n".join(out)

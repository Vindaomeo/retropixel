"""Demo animations for the host. Each function draws the moment ``t``
(in seconds) into the framebuffer."""

from __future__ import annotations

import math
import random

from .framebuffer import Framebuffer


def rainbow(fb: Framebuffer, t: float) -> None:
    for y in range(fb.height):
        for x in range(fb.width):
            fb.set_hsv(x, y, (x + y) / 16.0 + t * 0.25)


def plasma(fb: Framebuffer, t: float) -> None:
    for y in range(fb.height):
        for x in range(fb.width):
            v = (
                math.sin(x * 0.6 + t * 1.7)
                + math.sin(y * 0.7 - t * 1.1)
                + math.sin((x + y) * 0.45 + t * 2.3)
            ) / 3.0
            fb.set_hsv(x, y, 0.6 + v * 0.35, 0.9, 0.35 + 0.65 * abs(v))


def comet(fb: Framebuffer, t: float) -> None:
    fb.fade(0.75)
    n = fb.width * fb.height
    pos = int(t * 24) % n
    fb.set_hsv(pos % fb.width, pos // fb.width, t * 0.3, 1.0, 1.0)


def spiral(fb: Framebuffer, t: float) -> None:
    cx, cy = (fb.width - 1) / 2.0, (fb.height - 1) / 2.0
    for y in range(fb.height):
        for x in range(fb.width):
            dx, dy = x - cx, y - cy
            ang = math.atan2(dy, dx) / (2 * math.pi)
            rad = math.hypot(dx, dy) / 5.0
            fb.set_hsv(x, y, ang + rad - t * 0.5, 1.0, max(0.15, 1.0 - rad * 0.6))


_fire_heat: list[float] = []


def fire(fb: Framebuffer, t: float) -> None:
    global _fire_heat
    n = fb.width * fb.height
    if len(_fire_heat) != n:
        _fire_heat = [0.0] * n

    for i in range(n):  # cool down
        _fire_heat[i] = max(0.0, _fire_heat[i] - random.uniform(0.0, 0.28))
    for y in range(fb.height - 1):  # heat rises
        for x in range(fb.width):
            _fire_heat[y * fb.width + x] = (
                _fire_heat[(y + 1) * fb.width + x] * 0.62
                + _fire_heat[min(fb.height - 1, y + 2) * fb.width + x] * 0.33
            )
    for x in range(fb.width):  # embers in the bottom row
        if random.random() < 0.55:
            _fire_heat[(fb.height - 1) * fb.width + x] = random.uniform(0.7, 1.0)

    for y in range(fb.height):
        for x in range(fb.width):
            h = min(1.0, _fire_heat[y * fb.width + x])
            fb.set(x, y, int(255 * h), int(255 * h**2.4), int(180 * h**7))


def checker(fb: Framebuffer, t: float) -> None:
    puls = 0.5 + 0.5 * math.sin(t * 4)
    for y in range(fb.height):
        for x in range(fb.width):
            if (x ^ y) & 1:
                fb.set_hsv(x, y, t * 0.2, 1.0, puls)
            else:
                fb.set_hsv(x, y, t * 0.2 + 0.5, 1.0, 1.0 - puls)


def bounce(fb: Framebuffer, t: float) -> None:
    """Two bars sweeping past each other inside the rectangle."""
    fb.fade(0.6)
    col = int((math.sin(t * 2.1) * 0.5 + 0.5) * (fb.width - 1) + 0.5)
    row = int((math.cos(t * 1.4) * 0.5 + 0.5) * (fb.height - 1) + 0.5)
    for y in range(fb.height):
        fb.set_hsv(col, y, 0.55, 1.0, 1.0)
    for x in range(fb.width):
        fb.set_hsv(x, row, 0.08, 1.0, 1.0)


PATTERNS = {
    "rainbow": rainbow,
    "plasma": plasma,
    "comet": comet,
    "spiral": spiral,
    "fire": fire,
    "checker": checker,
    "bounce": bounce,
}

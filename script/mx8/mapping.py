"""Pixel mapping (x,y) -> index in the LED chain.

Mirror image of FW/mx8_map.h. Always apply changes to BOTH files --
tests/test_protocol.py compares the two implementations pixel by pixel.

Host coordinates: row 0 = top, column 0 = left, row-major.
"""

from __future__ import annotations

from dataclasses import dataclass

# Corner in which the first pixel of a tile sits.
ORIGIN_TOP_LEFT = 0
ORIGIN_TOP_RIGHT = 1
ORIGIN_BOTTOM_LEFT = 2
ORIGIN_BOTTOM_RIGHT = 3

ORIGIN_NAMES = {
    ORIGIN_TOP_LEFT: "top left",
    ORIGIN_TOP_RIGHT: "top right",
    ORIGIN_BOTTOM_LEFT: "bottom left",
    ORIGIN_BOTTOM_RIGHT: "bottom right",
}


@dataclass(frozen=True)
class TileMap:
    """Describes how tiles form a display area and how the chain runs.

    A single panel is the special case ``tiles_x = tiles_y = 1``.
    """

    tile_w: int = 8
    tile_h: int = 8
    tiles_x: int = 1
    tiles_y: int = 1
    vertical: bool = False            # chain runs by column instead of by row
    serpentine: bool = False          # every other row/column reversed
    origin: int = ORIGIN_TOP_LEFT     # corner of a tile's first pixel
    flip_alt_rows: bool = False       # every other tile row rotated 180 degrees
    tile_order_serpentine: bool = False   # tiles chained in serpentine (zigzag) order
    strands: int = 1                  # data pins; splits the chain into blocks

    # -- derived -----------------------------------------------------------
    @property
    def width(self) -> int:
        return self.tile_w * self.tiles_x

    @property
    def height(self) -> int:
        return self.tile_h * self.tiles_y

    @property
    def tile_leds(self) -> int:
        return self.tile_w * self.tile_h

    @property
    def num_tiles(self) -> int:
        return self.tiles_x * self.tiles_y

    @property
    def num_leds(self) -> int:
        return self.width * self.height

    @property
    def leds_per_strand(self) -> int:
        return self.num_leds // self.strands

    # -- the actual mapping -----------------------------------------------
    def index(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"({x},{y}) outside {self.width}x{self.height}")

        # 1) determine the tile
        tx, lx = divmod(x, self.tile_w)
        ty, ly = divmod(y, self.tile_h)

        tcol = (self.tiles_x - 1 - tx) if (self.tile_order_serpentine and ty & 1) else tx
        tile = ty * self.tiles_x + tcol

        # 2) normalize the local coordinate
        if self.flip_alt_rows and (ty & 1):
            lx = self.tile_w - 1 - lx
            ly = self.tile_h - 1 - ly

        if self.origin in (ORIGIN_TOP_RIGHT, ORIGIN_BOTTOM_RIGHT):
            lx = self.tile_w - 1 - lx
        if self.origin in (ORIGIN_BOTTOM_LEFT, ORIGIN_BOTTOM_RIGHT):
            ly = self.tile_h - 1 - ly

        # 3) local coordinate -> local chain index
        if self.vertical:
            if self.serpentine and (lx & 1):
                ly = self.tile_h - 1 - ly
            local = lx * self.tile_h + ly
        else:
            if self.serpentine and (ly & 1):
                lx = self.tile_w - 1 - lx
            local = ly * self.tile_w + lx

        return tile * self.tile_leds + local

    def strand_of(self, chain_index: int) -> int:
        """Which data pin drives this chain index?"""
        return chain_index // self.leds_per_strand

    def is_bijective(self) -> bool:
        """Does every pixel hit exactly one chain index? Must always be True."""
        seen = {self.index(x, y) for y in range(self.height) for x in range(self.width)}
        return len(seen) == self.num_leds and max(seen) == self.num_leds - 1

    def to_defines(self) -> str:
        """The matching block for FW/mx8_config.h."""
        # Pre-filled with the pins of the current build (Pico GP14 / GP17).
        default_pins = [14, 17, 18, 19]
        pins = "\n".join(
            f"#define {'LED_DATA_PIN' if s == 0 else f'LED_DATA_PIN_{s + 1}':<25}"
            f"{default_pins[s]}   // tile {s}"
            for s in range(self.strands)
        )
        return (
            f"#define MX_TILE_W                {self.tile_w}\n"
            f"#define MX_TILE_H                {self.tile_h}\n"
            f"#define MX_TILES_X               {self.tiles_x}\n"
            f"#define MX_TILES_Y               {self.tiles_y}\n"
            f"#define MX_TILE_VERTICAL         {int(self.vertical)}\n"
            f"#define MX_TILE_SERPENTINE       {int(self.serpentine)}\n"
            f"#define MX_TILE_ORIGIN           {self.origin}"
            f"   // {ORIGIN_NAMES[self.origin]}\n"
            f"#define MX_TILE_FLIP_ALT_ROWS    {int(self.flip_alt_rows)}\n"
            f"#define MX_TILE_ORDER_SERPENTINE {int(self.tile_order_serpentine)}\n"
            f"#define MX_STRANDS               {self.strands}\n"
            f"{pins}\n"
        )


# ---------------------------------------------------------------------------
# The presets from FW/mx8_config.h -- mirrored here so host tools
# and tests see the same numbers.
# ---------------------------------------------------------------------------
PRESETS: dict[str, TileMap] = {
    "8x8": TileMap(tile_w=8, tile_h=8, tiles_x=1, tiles_y=1, strands=1),
    # Two 8x32 panels stacked. The chain runs BY COLUMN, eight pixels per
    # column, serpentine -- measured on the panel. Whether a panel is internally
    # made of four 8x8 blocks doesn't matter with a vertical chain: column c
    # lands on chain index 8c..8c+7 either way.
    "16x32": TileMap(
        tile_w=32, tile_h=8, tiles_x=1, tiles_y=2,
        vertical=True, serpentine=True, strands=2,
    ),
    # Alternatives, in case another panel is wired differently.
    "16x32-rows": TileMap(          # by row, serpentine
        tile_w=32, tile_h=8, tiles_x=1, tiles_y=2,
        vertical=False, serpentine=True, strands=2,
    ),
    "16x32-blocks": TileMap(        # four chained 8x8 blocks per panel
        tile_w=8, tile_h=8, tiles_x=4, tiles_y=2,
        vertical=False, serpentine=True, strands=2,
    ),
}

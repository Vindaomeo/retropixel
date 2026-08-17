"""Pixel-Mapping (x,y) -> Index in der LED-Kette.

Spiegelbild von FW/mx8_map.h. Aenderungen immer in BEIDEN Dateien nachziehen --
tests/test_protocol.py vergleicht beide Implementierungen Pixel fuer Pixel.

Host-Koordinaten: Zeile 0 = oben, Spalte 0 = links, row-major.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ecke, in der das erste Pixel einer Kachel sitzt.
ORIGIN_TOP_LEFT = 0
ORIGIN_TOP_RIGHT = 1
ORIGIN_BOTTOM_LEFT = 2
ORIGIN_BOTTOM_RIGHT = 3

ORIGIN_NAMES = {
    ORIGIN_TOP_LEFT: "oben links",
    ORIGIN_TOP_RIGHT: "oben rechts",
    ORIGIN_BOTTOM_LEFT: "unten links",
    ORIGIN_BOTTOM_RIGHT: "unten rechts",
}


@dataclass(frozen=True)
class TileMap:
    """Beschreibt, wie aus Kacheln eine Flaeche wird und wie die Kette laeuft.

    Ein einzelnes Panel ist der Sonderfall ``tiles_x = tiles_y = 1``.
    """

    tile_w: int = 8
    tile_h: int = 8
    tiles_x: int = 1
    tiles_y: int = 1
    vertical: bool = False            # Kette spaltenweise statt zeilenweise
    serpentine: bool = False          # jede zweite Zeile/Spalte rueckwaerts
    origin: int = ORIGIN_TOP_LEFT     # Ecke des ersten Pixels einer Kachel
    flip_alt_rows: bool = False       # jede zweite Kachelzeile 180 Grad gedreht
    tile_order_serpentine: bool = False   # Kacheln im Zickzack verkettet
    strands: int = 1                  # Datenpins; teilt die Kette in Bloecke

    # -- abgeleitet --------------------------------------------------------
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

    # -- das eigentliche Mapping ------------------------------------------
    def index(self, x: int, y: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise IndexError(f"({x},{y}) ausserhalb {self.width}x{self.height}")

        # 1) Kachel bestimmen
        tx, lx = divmod(x, self.tile_w)
        ty, ly = divmod(y, self.tile_h)

        tcol = (self.tiles_x - 1 - tx) if (self.tile_order_serpentine and ty & 1) else tx
        tile = ty * self.tiles_x + tcol

        # 2) lokale Koordinate normalisieren
        if self.flip_alt_rows and (ty & 1):
            lx = self.tile_w - 1 - lx
            ly = self.tile_h - 1 - ly

        if self.origin in (ORIGIN_TOP_RIGHT, ORIGIN_BOTTOM_RIGHT):
            lx = self.tile_w - 1 - lx
        if self.origin in (ORIGIN_BOTTOM_LEFT, ORIGIN_BOTTOM_RIGHT):
            ly = self.tile_h - 1 - ly

        # 3) lokale Koordinate -> lokaler Kettenindex
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
        """An welchem Datenpin haengt dieser Kettenindex?"""
        return chain_index // self.leds_per_strand

    def is_bijective(self) -> bool:
        """Trifft jedes Pixel genau einen Kettenindex? Muss immer True sein."""
        seen = {self.index(x, y) for y in range(self.height) for x in range(self.width)}
        return len(seen) == self.num_leds and max(seen) == self.num_leds - 1

    def to_defines(self) -> str:
        """Der passende Block fuer FW/mx8_config.h."""
        # Vorbelegt mit den Pins des aktuellen Aufbaus (Pico GP14 / GP17).
        default_pins = [14, 17, 18, 19]
        pins = "\n".join(
            f"#define {'LED_DATA_PIN' if s == 0 else f'LED_DATA_PIN_{s + 1}':<25}"
            f"{default_pins[s]}   // Kachel {s}"
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
# Die Presets aus FW/mx8_config.h -- hier gespiegelt, damit Host-Werkzeuge
# und Tests dieselben Zahlen sehen.
# ---------------------------------------------------------------------------
PRESETS: dict[str, TileMap] = {
    "8x8": TileMap(tile_w=8, tile_h=8, tiles_x=1, tiles_y=1, strands=1),
    # Zwei 8x32-Panels uebereinander. Die Kette laeuft SPALTENWEISE, acht Pixel
    # je Spalte, im Zickzack -- am Panel gemessen. Ob ein Panel intern aus vier
    # 8x8-Bloecken besteht, ist bei senkrechter Kette egal: Spalte c liegt in
    # beiden Faellen auf Kettenindex 8c..8c+7.
    "16x32": TileMap(
        tile_w=32, tile_h=8, tiles_x=1, tiles_y=2,
        vertical=True, serpentine=True, strands=2,
    ),
    # Gegenentwuerfe, falls ein anderes Panel anders verdrahtet ist.
    "16x32-rows": TileMap(          # zeilenweise, Zickzack
        tile_w=32, tile_h=8, tiles_x=1, tiles_y=2,
        vertical=False, serpentine=True, strands=2,
    ),
    "16x32-blocks": TileMap(        # vier verkettete 8x8-Bloecke je Panel
        tile_w=8, tile_h=8, tiles_x=4, tiles_y=2,
        vertical=False, serpentine=True, strands=2,
    ),
}

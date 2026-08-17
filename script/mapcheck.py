#!/usr/bin/env python3
"""mapcheck -- die tatsaechliche Verdrahtung eines Panels ausmessen.

Wie ein gekauftes Panel intern verloetet ist, steht selten im Datenblatt:
zeilenweise oder spaltenweise, im Zickzack oder nicht, erstes Pixel oben links
oder unten rechts. Raten kostet mehr Zeit als messen.

Dieses Skript nutzt CMD_RAW: damit landen die Farben OHNE Pixel-Mapping direkt
in der LED-Kette. Was das Panel zeigt, ist also die physische Verdrahtung --
unabhaengig davon, was gerade in FW/mx8_config.h steht.

Ablauf:
    python mapcheck.py                  vier Fragen -> fertiger Config-Block
    python mapcheck.py --chase          Kette langsam ablaufen, nur anschauen
    python mapcheck.py --verify 16x32   Testbild mit einem bekannten Preset

Am Ende steht ein Block, den man 1:1 nach FW/mx8_config.h kopiert. Vorher wird
das Ergebnis noch mit einem Testbild geprueft -- ebenfalls ueber CMD_RAW, das
Mapping rechnet dabei der PC. Erst wenn das Bild stimmt, lohnt das Flashen.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from mx8.font5x7 import GLYPH_H, GLYPH_W, glyph
from mx8.link import MatrixLink
from mx8.mapping import (
    ORIGIN_BOTTOM_LEFT,
    ORIGIN_BOTTOM_RIGHT,
    ORIGIN_NAMES,
    ORIGIN_TOP_LEFT,
    ORIGIN_TOP_RIGHT,
    PRESETS,
    TileMap,
)

WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (60, 120, 255)


# ---------------------------------------------------------------------------
# Ein-/Ausgabe
# ---------------------------------------------------------------------------
def ask(question: str, options: list[tuple[str, object]]) -> object:
    """Eine Frage mit nummerierten Antworten. Gibt den Wert der Antwort zurueck."""
    print()
    print(question)
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}) {label}")
    while True:
        raw = input("  > ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][1]
        print(f"  Bitte 1 bis {len(options)} eingeben.")


def rgb_bytes(*colors: tuple[int, int, int]) -> bytes:
    return bytes(v for c in colors for v in c)


# ---------------------------------------------------------------------------
# Testbild
# ---------------------------------------------------------------------------
def test_image(tm: TileMap) -> list[list[tuple[int, int, int]]]:
    """Ein Bild, dem man jede Verdrehung sofort ansieht.

    - gruener Rahmen um die Gesamtflaeche  -> ist die Flaeche vollstaendig?
    - rote Ecke oben links, kurzer Arm nach rechts, langer Arm nach unten
      -> verraet jede Drehung, Spiegelung UND Achsvertauschung
    - je Kachel ihre Nummer als Ziffer  -> sitzen die Panels richtig herum?
    """
    w, h = tm.width, tm.height
    img = [[(0, 0, 0) for _ in range(w)] for _ in range(h)]

    # Rahmen
    for x in range(w):
        img[0][x] = img[h - 1][x] = (0, 40, 0)
    for y in range(h):
        img[y][0] = img[y][w - 1] = (0, 40, 0)

    # Kachelnummern -- nur sinnvoll, wenn es mehr als eine Kachel gibt.
    if tm.num_tiles > 1:
        for ty in range(tm.tiles_y):
            for tx in range(tm.tiles_x):
                n = ty * tm.tiles_x + tx
                rows = glyph(str(n)) if n < 10 else glyph("?")
                # mittig in der Kachel, aber nie im Rahmen
                ox = min(max(tx * tm.tile_w + (tm.tile_w - GLYPH_W) // 2, 1),
                         max(1, w - 1 - GLYPH_W))
                oy = min(max(ty * tm.tile_h + (tm.tile_h - GLYPH_H) // 2, 1),
                         max(1, h - 1 - GLYPH_H))
                for gy, row in enumerate(rows):
                    for gx, ch in enumerate(row):
                        if ch != "#":
                            continue
                        x, y = ox + gx, oy + gy
                        if 0 <= x < w and 0 <= y < h:
                            img[y][x] = (120, 120, 120)

    # Asymmetrische Ecke: kurzer Arm nach rechts, langer nach unten.
    # Nur so faellt auch eine vertauschte x/y-Achse auf.
    for x in range(min(3, w)):
        img[0][x] = RED
    for y in range(min(5, h)):
        img[y][0] = RED
    return img


def show_image(link: MatrixLink, tm: TileMap, img) -> None:
    """Bild ueber CMD_RAW anzeigen -- das Mapping rechnet hier der PC.

    Damit laesst sich eine Konfiguration pruefen, ohne die Firmware neu zu
    flashen: stimmt das Bild, stimmt der Config-Block.
    """
    chain = [(0, 0, 0)] * tm.num_leds
    for y in range(tm.height):
        for x in range(tm.width):
            chain[tm.index(x, y)] = img[y][x]
    link.send_raw(0, rgb_bytes(*chain))


def ansi_preview(img) -> str:
    out = []
    for row in img:
        out.append("".join(f"\033[48;2;{r};{g};{b}m  " for r, g, b in row) + "\033[0m")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Der Assistent
# ---------------------------------------------------------------------------
def wizard(link: MatrixLink, tile_w: int, tile_h: int,
           tiles_x: int, tiles_y: int, strands: int) -> TileMap:
    tile_leds = tile_w * tile_h
    print(f"\nAngenommen: {tiles_x}x{tiles_y} Kacheln zu je {tile_w}x{tile_h} Pixel "
          f"= {tile_w * tiles_x}x{tile_h * tiles_y}, {strands} Datenpin(s).")
    print("Stimmt das nicht, mit --tile / --tiles / --strands korrigieren.")
    print("\nSchau auf das Panel, so wie es spaeter haengen soll.")

    # -- 1) Wo sitzt Kettenindex 0? -----------------------------------------
    link.send_raw(0, rgb_bytes(WHITE))
    origin = ask(
        "Genau ein Pixel leuchtet weiss. In welcher Ecke der ERSTEN Kachel?",
        [
            ("oben links", ORIGIN_TOP_LEFT),
            ("oben rechts", ORIGIN_TOP_RIGHT),
            ("unten links", ORIGIN_BOTTOM_LEFT),
            ("unten rechts", ORIGIN_BOTTOM_RIGHT),
        ],
    )

    # -- 2) Laeuft die Kette in Zeilen oder in Spalten? ----------------------
    run = min(tile_w, tile_h, 4)
    link.send_raw(0, rgb_bytes(*[RED] * run))
    vertical = ask(
        f"Jetzt leuchten {run} rote Pixel. Wie liegen sie?",
        [("waagrecht nebeneinander", False), ("senkrecht untereinander", True)],
    )

    # -- 3) Zickzack? --------------------------------------------------------
    # Erster Durchlauf rot, zweiter gruen. Wo Gruen anfaengt, verraet alles.
    # Bewusst ohne "oben/unten": das haengt schon von Frage 1 ab.
    run_len = tile_h if vertical else tile_w
    link.send_raw(0, rgb_bytes(*([RED] * run_len + [GREEN] * run_len)))
    serpentine = ask(
        "Eine rote und eine gruene Reihe liegen nebeneinander.\n"
        "Wo beginnt Gruen?",
        [
            ("direkt neben dem LETZTEN roten Pixel (Zickzack)", True),
            ("direkt neben dem ERSTEN roten Pixel (jede Reihe gleiche Richtung)", False),
        ],
    )

    # -- 4) Wie sitzen die weiteren Kacheln? ---------------------------------
    flip_alt_rows = False
    tile_order_serpentine = False

    if tiles_x * tiles_y > 1:
        # Weiss = Kettenindex 0, Blau = erstes Pixel der zweiten Kachel.
        # Beides in EINEM RAW-Frame, denn RAW loescht immer den Rest.
        span = [(0, 0, 0)] * (tile_leds + 1)
        span[0] = WHITE
        span[tile_leds] = BLUE
        link.send_raw(0, rgb_bytes(*span))

        where = ask(
            "Zusaetzlich leuchtet ein blaues Pixel -- das erste Pixel der\n"
            "ZWEITEN Kachel. Wo sitzt es?",
            [
                ("in einer anderen Kachel als das weisse", "other"),
                ("in derselben Kachel wie das weisse", "wrong"),
            ],
        )
        if where == "wrong":
            print("\n  Dann haengen beide Kacheln am selben Datenpin, oder --tile")
            print("  stimmt nicht. Verkabelung und Kachelgroesse pruefen,")
            print("  dann nochmal starten.")
            sys.exit(1)

        if tiles_y > 1:
            flip_alt_rows = bool(ask(
                "Vergleiche die Ecke: sitzt das blaue Pixel in SEINER Kachel\n"
                "in derselben Ecke wie das weisse in seiner?",
                [
                    ("ja, dieselbe Ecke", False),
                    ("nein, diagonal gegenueber (Kachel ist 180 Grad gedreht)", True),
                ],
            ))

        if tiles_x > 1:
            tile_order_serpentine = bool(ask(
                "Und die Verkettung der Kacheln: faengt jede Kachelzeile wieder\n"
                "links an, oder laeuft sie im Zickzack weiter?",
                [
                    ("jede Kachelzeile beginnt wieder links", False),
                    ("Zickzack -- die naechste Zeile laeuft rueckwaerts", True),
                ],
            ))

    link.clear()
    return TileMap(
        tile_w=tile_w, tile_h=tile_h, tiles_x=tiles_x, tiles_y=tiles_y,
        vertical=bool(vertical), serpentine=bool(serpentine), origin=int(origin),
        flip_alt_rows=flip_alt_rows, tile_order_serpentine=tile_order_serpentine,
        strands=strands,
    )


def chase(link: MatrixLink, num_leds: int, delay: float) -> None:
    print(f"Laeuft die Kette ab, {num_leds} LEDs, {delay:g} s pro Pixel. Strg-C beendet.")
    i = 0
    while True:
        link.send_raw(i, rgb_bytes(WHITE))
        print(f"\r  Kettenindex {i:>5}", end="", flush=True)
        time.sleep(delay)
        i = (i + 1) % num_leds


# ---------------------------------------------------------------------------
def parse_pair(text: str, what: str) -> tuple[int, int]:
    try:
        a, b = text.lower().split("x")
        return int(a), int(b)
    except ValueError:
        raise SystemExit(f"--{what} braucht die Form BREITExHOEHE, z. B. 32x8")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Panel-Verdrahtung ausmessen und Config-Block erzeugen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("-p", "--port", help="z. B. COM4 (sonst MX8_PORT / Autoerkennung)")
    ap.add_argument("-b", "--baud", type=int, default=None)
    ap.add_argument("--tile", default="32x8", help="Groesse EINER Kachel (Standard 32x8)")
    ap.add_argument("--tiles", default="1x2", help="Kacheln nebeneinander x untereinander")
    ap.add_argument("--strands", type=int, default=2, help="Anzahl Datenpins")
    ap.add_argument("--brightness", type=int, default=24,
                    help="Helligkeit zum Ausmessen (Standard 24 -- reicht und blendet nicht)")
    ap.add_argument("--chase", action="store_true",
                    help="nur die Kette ablaufen lassen, keine Fragen")
    ap.add_argument("--delay", type=float, default=0.25, help="Sekunden pro Pixel bei --chase")
    ap.add_argument("--verify", metavar="PRESET",
                    help=f"nur Testbild mit einem Preset: {', '.join(PRESETS)}")
    ap.add_argument("--release", action="store_true",
                    help="am Ende RELEASE senden (Panel zeigt wieder sein Testpattern)")
    args = ap.parse_args(argv)

    tile_w, tile_h = parse_pair(args.tile, "tile")
    tiles_x, tiles_y = parse_pair(args.tiles, "tiles")

    link = MatrixLink(args.port, args.baud, on_log=lambda s: print(f"  LOG  {s}"))
    link.open()
    geo = link.wait_ready(timeout=8.0)
    print(f"Port     {link.port} @ {link.baud} Baud")
    print(f"READY    {geo}   (das ist die AKTUELL geflashte Konfiguration)")
    link.set_brightness(args.brightness)

    try:
        if args.chase:
            chase(link, geo.width * geo.height, args.delay)
            return 0

        if args.verify:
            if args.verify not in PRESETS:
                raise SystemExit(f"Preset unbekannt. Bekannt: {', '.join(PRESETS)}")
            tm = PRESETS[args.verify]
        else:
            tm = wizard(link, tile_w, tile_h, tiles_x, tiles_y, args.strands)

        if not tm.is_bijective():
            print("\nFEHLER: Das ergibt kein gueltiges Mapping -- bitte nochmal messen.")
            return 1

        img = test_image(tm)
        show_image(link, tm, img)

        print("\n" + "=" * 68)
        print("So sollte das Panel jetzt aussehen:")
        print("=" * 68)
        print(ansi_preview(img))
        print("=" * 68)
        print("Rote Ecke oben links (kurzer Arm nach rechts, langer nach unten),")
        print("gruener Rahmen aussen herum, je Kachel ihre Nummer -- aufrecht")
        print("und nicht gespiegelt.\n")
        print("Passt das Bild, diesen Block nach FW/mx8_config.h uebernehmen:\n")
        print(tm.to_defines())
        print("Passt es nicht, das Skript nochmal starten und genauer hinschauen.")
        input("\n[Enter] zum Beenden ")
        return 0
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        try:
            if args.release:
                link.release()
            else:
                link.clear()
        except Exception:
            pass
        link.close()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WS2812B-Matrix-Generator fuer KiCad
===================================

Erzeugt ein komplettes KiCad-Projekt (Schaltplan + 4-Lagen-Layout) fuer eine
LED-Matrix aus WS2812B (5050) im festen Raster, inkl. Freiraum fuer
Kunststoff-Trennstege zwischen den LEDs.

Ausgabe-Format: KiCad 7 (kicad_sch 20230121 / kicad_pcb 20221018).
KiCad 8 (und 9) oeffnen diese Dateien direkt und konvertieren beim Speichern.

Benutzung:
    python3 gen_ledmatrix.py            -> Ordner ./LED_Matrix_16x16/
    python3 gen_ledmatrix.py mein_ordner

Keine Abhaengigkeiten ausser Python 3.8+.

Aufbau pro Zelle (Blick von oben, Datenfluss nach rechts):
    - LED in der Zellmitte, gerade Reihen 180 Grad gedreht, ungerade 0 Grad
      -> DOUT zeigt immer zur naechsten LED (Serpentine)
    - 100 nF (0402) unter der LED, VDD/GND per Via auf die Innenlagen
    - Stegbereich (WALL) auf dem Zellrand bleibt frei von Bauteilen
      (Rule-Areas "footprints not allowed"), Leiterbahnen duerfen drunter
Lagen (LAYERS = 4, Standard):
    F.Cu   LEDs, Kondensatoren, Datenleitung
    In1.Cu GND-Flaeche
    In2.Cu +5V-Flaeche
    B.Cu   GND-Flaeche, Einspeise-Pads, Pegelwandler, Daten-Pads
Lagen (LAYERS = 2, Aufruf mit --layers 2):
    F.Cu   LEDs, Kondensatoren, Datenleitung, +5V-Flaeche um alles herum
    B.Cu   GND-Flaeche, Einspeise-Pads, Pegelwandler, Daten-Pads

Aufruf:
    python3 gen_ledmatrix.py                    -> 4 Lagen, ./LED_Matrix_16x16/
    python3 gen_ledmatrix.py --layers 2         -> 2 Lagen, ./LED_Matrix_16x16_2L/
    python3 gen_ledmatrix.py --layers 2 ordner  -> 2 Lagen in ./ordner/
"""

import math
import os
import sys
import uuid
import json

# =============================================================================
# PARAMETER
# =============================================================================
PROJECT = "LED_Matrix_16x16"
COLS, ROWS = 16, 16          # Matrixgroesse
PITCH = 10.0                 # Raster in mm (Zellgroesse)
WALL = 1.2                   # Breite der Trennstege in mm (bauteilfrei)
ORIGIN = (50.0, 50.0)        # linke obere Platinenecke auf dem Blatt (mm)

# Loecher an Steg-Kreuzungen (Index der Kreuzung, 0..COLS / 0..ROWS)
# z.B. fuer M2-Schrauben oder Zapfen des Trenngitters
HOLE_DIA = 2.2
HOLES = [(i, j) for i in (4, 8, 12) for j in (4, 8, 12)]

# Fiducials (Passermarken fuer die Bestueckung) an Steg-Kreuzungen, oben.
# 3 Stueck, unsymmetrisch, damit der Automat die Lage eindeutig erkennt.
# 1 mm Kupferpunkt, 2 mm Maskenoeffnung, 3 mm kupferfrei.
FIDUCIALS = [(1, 1), (COLS - 1, 1), (1, ROWS - 1)]
# Fiducials auf der Unterseite (gleiche Kreuzungen; von unten gesehen gespiegelt,
# also ebenfalls unsymmetrisch). Leere Liste = keine.
FIDUCIALS_BOTTOM = [(1, 1), (COLS - 1, 1), (1, ROWS - 1)]

# Einspeisepunkte (Spalte, Zeile): +5V-Pad in dieser Zelle (Unterseite),
# GND-Pad in der Zelle rechts daneben
POWER_FEEDS = [(3, 3), (11, 3), (3, 12), (11, 12)]

# JLCPCB-Bestueckung ----------------------------------------------------------
# LCSC-Teilenummern je Footprint-Typ (landen als Feld "LCSC" in Schaltplan und
# Layout und in der BOM). Vor der Bestellung Lagerbestand bei JLCPCB pruefen!
LCSC = {
    "LED": "C2843785",     # Worldsemi WS2812B-B/W, 5050 (Alternative: C2761795, C114586)
    "C0402": "C1525",     # Samsung CL05B104KO5NNNC 100nF 16V X7R 0402 (Basic)
    "R0402": "C25105",    # UNI-ROYAL 0402WGF330JTCE 33R 0402 (Basic)
    "SOT235": "C7484",    # TI SN74AHCT1G125DBVR SOT-23-5
}
# Drehwinkel-Korrektur KiCad -> JLCPCB (gleiche Werte wie KiBot/JLC-Plugins)
JLC_ROT = {"LED": 180, "SOT235": 180}
# False: JLC bestueckt nur die Oberseite (LEDs + Kondensatoren), U1/R1/C257 auf
#        der Unterseite werden von Hand geloetet (spart den Aufpreis fuer
#        beidseitige Bestueckung).  True: beide Seiten in BOM/CPL.
JLC_BOTTOM = False
LCSC_BY_REF = {}

TRACK_DATA = 0.25            # Datenleitung
TRACK_PWR = 0.40             # Stichleitungen VDD/GND
VIA = (0.6, 0.3)             # Standard-Via (Durchmesser, Bohrung)
VIA_PWR = (0.8, 0.4)         # Vias in den Einspeise-Pads

LAYERS = 4                   # 2 oder 4 Kupferlagen (Kommandozeile: --layers 2)
TITLE = "WS2812B Matrix %dx%d, Raster %.0f mm, %d Lagen" % (COLS, ROWS, PITCH, LAYERS)

# =============================================================================
# Hilfsfunktionen
# =============================================================================
_NS = uuid.UUID("6a1f3c52-8d0e-4c1b-9d7e-2f5a6b7c8d90")


def uid(*parts):
    """Deterministische UUIDs -> gleiche Eingabe erzeugt gleiche Datei."""
    return str(uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


def f(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def rot(x, y, deg):
    """KiCad-Rotation (Grad, gegen Uhrzeigersinn, y nach unten)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (x * c + y * s, -x * s + y * c)


# =============================================================================
# Footprint-Definitionen (Geometrie aus der KiCad-Standardbibliothek)
# pad: (nummer, typ, form, (x,y), (w,h), drill, rratio)
# =============================================================================
FP = {
    "LED": dict(
        lib="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
        descr="5.0mm x 5.0mm Addressable RGB LED NeoPixel",
        tags="LED RGB NeoPixel PLCC-4 5050", attr="smd",
        pads=[("1", "smd", "rect", (-2.45, -1.65), (1.5, 0.9), None, None),
              ("2", "smd", "rect", (-2.45, 1.65), (1.5, 0.9), None, None),
              ("3", "smd", "rect", (2.45, 1.65), (1.5, 0.9), None, None),
              ("4", "smd", "rect", (2.45, -1.65), (1.5, 0.9), None, None)],
        silk=[((-3.65, -2.75), (3.65, -2.75)), ((-3.65, 2.75), (3.65, 2.75)),
              ((3.65, 2.75), (3.65, 1.6))],
        crtyd=(-3.45, -2.75, 3.45, 2.75),
        fab=[((-2.5, -2.5), (-2.5, 2.5)), ((-2.5, 2.5), (2.5, 2.5)),
             ((2.5, 2.5), (2.5, -2.5)), ((2.5, -2.5), (-2.5, -2.5)),
             ((2.5, 1.5), (1.5, 2.5))],
        model="${KICAD8_3DMODEL_DIR}/LED_SMD.3dshapes/LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm.wrl",
        ref_at=(0, 0)),
    "C0402": dict(
        lib="Capacitor_SMD:C_0402_1005Metric",
        descr="Capacitor SMD 0402 (1005 Metric)", tags="capacitor", attr="smd",
        pads=[("1", "smd", "roundrect", (-0.48, 0), (0.56, 0.62), None, 0.25),
              ("2", "smd", "roundrect", (0.48, 0), (0.56, 0.62), None, 0.25)],
        silk=[], crtyd=(-0.91, -0.46, 0.91, 0.46),
        fab=[((-0.5, -0.25), (0.5, -0.25)), ((0.5, -0.25), (0.5, 0.25)),
             ((0.5, 0.25), (-0.5, 0.25)), ((-0.5, 0.25), (-0.5, -0.25))],
        model="${KICAD8_3DMODEL_DIR}/Capacitor_SMD.3dshapes/C_0402_1005Metric.wrl",
        ref_at=(0, 0)),
    "R0402": dict(
        lib="Resistor_SMD:R_0402_1005Metric",
        descr="Resistor SMD 0402 (1005 Metric)", tags="resistor", attr="smd",
        pads=[("1", "smd", "roundrect", (-0.51, 0), (0.54, 0.64), None, 0.25),
              ("2", "smd", "roundrect", (0.51, 0), (0.54, 0.64), None, 0.25)],
        silk=[], crtyd=(-0.93, -0.47, 0.93, 0.47),
        fab=[((-0.525, -0.27), (0.525, -0.27)), ((0.525, -0.27), (0.525, 0.27)),
             ((0.525, 0.27), (-0.525, 0.27)), ((-0.525, 0.27), (-0.525, -0.27))],
        model="${KICAD8_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0402_1005Metric.wrl",
        ref_at=(0, 0)),
    "SOT235": dict(
        lib="Package_TO_SOT_SMD:SOT-23-5",
        descr="SOT, 5 Pin", tags="SOT TO_SOT_SMD", attr="smd",
        pads=[("1", "smd", "roundrect", (-1.1375, -0.95), (1.325, 0.6), None, 0.25),
              ("2", "smd", "roundrect", (-1.1375, 0), (1.325, 0.6), None, 0.25),
              ("3", "smd", "roundrect", (-1.1375, 0.95), (1.325, 0.6), None, 0.25),
              ("4", "smd", "roundrect", (1.1375, 0.95), (1.325, 0.6), None, 0.25),
              ("5", "smd", "roundrect", (1.1375, -0.95), (1.325, 0.6), None, 0.25)],
        silk=[((0, -1.56), (0.8, -1.56)), ((0, 1.56), (0.8, 1.56)), ((0, 1.56), (-0.8, 1.56))],
        crtyd=(-2.05, -1.7, 2.05, 1.7),
        fab=[((-0.4, -1.45), (0.8, -1.45)), ((0.8, -1.45), (0.8, 1.45)),
             ((0.8, 1.45), (-0.8, 1.45)), ((-0.8, 1.45), (-0.8, -1.05)),
             ((-0.8, -1.05), (-0.4, -1.45))],
        model="${KICAD8_3DMODEL_DIR}/Package_TO_SOT_SMD.3dshapes/SOT-23-5.wrl",
        ref_at=(0, 0)),
    # --- projekteigene Footprints (Bibliothek LEDMatrix.pretty) ---
    "PAD_SIG": dict(
        lib="LEDMatrix:SolderPad_3x2mm", descr="Loetpad 3x2 mm fuer Kabel",
        tags="pad wire", attr="smd",
        pads=[("1", "smd", "roundrect", (0, 0), (3.0, 2.0), None, 0.15)],
        silk=[], crtyd=(-1.75, -1.25, 1.75, 1.25), fab=[], model=None, ref_at=(0, 0)),
    "PAD_PWR": dict(
        lib="LEDMatrix:SolderPad_3.6x5mm", descr="Einspeise-Loetpad 3.6x5 mm (Vias setzt das Layout)",
        tags="pad wire power", attr="smd",
        pads=[("1", "smd", "roundrect", (0, 0), (3.6, 5.0), None, 0.1)],
        silk=[], crtyd=(-2.0, -2.7, 2.0, 2.7), fab=[], model=None, ref_at=(0, 0)),
    "HOLE": dict(
        lib="LEDMatrix:NPTH_%.1fmm" % HOLE_DIA, descr="Bohrung ohne Kupfer an Steg-Kreuzung",
        tags="hole npth", attr="exclude_from_pos_files exclude_from_bom",
        pads=[("", "np_thru_hole", "circle", (0, 0), (HOLE_DIA, HOLE_DIA), HOLE_DIA, None)],
        silk=[], crtyd=None, fab=[], model=None, ref_at=(0, 0)),
    # Fiducial ohne Courtyard, damit es in der Steg-Sperrflaeche liegen darf
    "FID": dict(
        lib="LEDMatrix:Fiducial_1mm_Mask2mm", descr="Fiducial 1 mm, Maskenoeffnung 2 mm",
        tags="fiducial", attr="smd exclude_from_pos_files exclude_from_bom",
        pads=[("", "smd", "circle", (0, 0), (1.0, 1.0), None, None)],
        pad_layers=("F.Cu", "F.Mask"), pad_extra="(solder_mask_margin 0.5) (clearance 1)",
        silk=[], crtyd=None, fab=[], model=None, ref_at=(0, 0)),
}

LAYER_FLIP = {"F.Cu": "B.Cu", "F.Paste": "B.Paste", "F.Mask": "B.Mask",
              "F.SilkS": "B.SilkS", "F.CrtYd": "B.CrtYd", "F.Fab": "B.Fab"}


def pad_abs(fpkey, num, x, y, angle, side="F"):
    """Absolute Position eines Pads (fuer das Routing)."""
    for p in FP[fpkey]["pads"]:
        if p[0] == num:
            px, py = p[3]
            if side == "B":
                py = -py
            dx, dy = rot(px, py, angle)
            return (x + dx, y + dy)
    raise KeyError(num)


# =============================================================================
# Netze & Bauteile (gemeinsame Datenbasis fuer Schaltplan UND Layout)
# =============================================================================
class Design:
    def __init__(self):
        self.nets = [""]            # Index = Netznummer
        self.parts = []             # dict(ref, value, sym, fp, pins{pin:net}, ...)

    def net(self, name):
        if name not in self.nets:
            self.nets.append(name)
        return self.nets.index(name)


def cell_center(c, r):
    return (ORIGIN[0] + PITCH * (c + 0.5), ORIGIN[1] + PITCH * (r + 0.5))


def flow_right(r):
    return r % 2 == 0


def led_no(c, r):
    return r * COLS + (c if flow_right(r) else COLS - 1 - c) + 1


def led_pos(n):
    """LED-Nummer (1..N) -> (Spalte, Zeile)."""
    r, k = divmod(n - 1, COLS)
    return (k if flow_right(r) else COLS - 1 - k, r)


def dnet(k):
    """Label-Name der Datenleitung zwischen LED k und k+1 (lokales Label)."""
    return "/D%03d" % k


N_LED = COLS * ROWS


def build_design():
    d = Design()
    d.net("GND")
    d.net("+5V")
    for n in range(1, N_LED + 1):
        d.parts.append(dict(ref="D%d" % n, value="WS2812B", sym="LED:WS2812B", fp="LED",
                            pins={"1": "+5V", "2": dnet(n), "3": "GND", "4": dnet(n - 1)}))
    for n in range(1, N_LED + 1):
        d.parts.append(dict(ref="C%d" % n, value="100nF", sym="Device:C_Small", fp="C0402",
                            pins={"1": "+5V", "2": "GND"}))
    # Eingang: Pegelwandler 3,3 V -> 5 V
    d.parts.append(dict(ref="U1", value="74AHCT1G125", sym="74xGxx:74AHCT1G125", fp="SOT235",
                        pins={"1": "GND", "2": "/DATA_IN", "3": "GND", "4": "/DATA_Y", "5": "+5V"}))
    d.parts.append(dict(ref="R1", value="33R", sym="Device:R_Small", fp="R0402",
                        pins={"1": dnet(0), "2": "/DATA_Y"}))
    d.parts.append(dict(ref="C%d" % (N_LED + 1), value="100nF", sym="Device:C_Small", fp="C0402",
                        pins={"1": "+5V", "2": "GND"}))
    d.parts.append(dict(ref="J1", value="DIN", sym="Connector_Generic:Conn_01x01", fp="PAD_SIG",
                        pins={"1": "/DATA_IN"}))
    d.parts.append(dict(ref="J2", value="GND", sym="Connector_Generic:Conn_01x01", fp="PAD_SIG",
                        pins={"1": "GND"}))
    d.parts.append(dict(ref="J3", value="DOUT", sym="Connector_Generic:Conn_01x01", fp="PAD_SIG",
                        pins={"1": dnet(N_LED)}))
    d.parts.append(dict(ref="J4", value="GND", sym="Connector_Generic:Conn_01x01", fp="PAD_SIG",
                        pins={"1": "GND"}))
    j = 5
    for i, _ in enumerate(POWER_FEEDS):
        d.parts.append(dict(ref="J%d" % j, value="+5V", sym="Connector_Generic:Conn_01x01",
                            fp="PAD_PWR", pins={"1": "+5V"}))
        d.parts.append(dict(ref="J%d" % (j + 1), value="GND", sym="Connector_Generic:Conn_01x01",
                            fp="PAD_PWR", pins={"1": "GND"}))
        j += 2
    for i, _ in enumerate(HOLES):
        d.parts.append(dict(ref="H%d" % (i + 1), value="Hole", sym="Mechanical:MountingHole",
                            fp="HOLE", pins={}))
    for i, _ in enumerate(FIDUCIALS + FIDUCIALS_BOTTOM):
        d.parts.append(dict(ref="FID%d" % (i + 1), value="Fiducial", sym="Mechanical:Fiducial",
                            fp="FID", pins={}))
    for p in d.parts:
        for n in p["pins"].values():
            d.net(n)
        p["uuid"] = uid("sym", p["ref"])
        p["lcsc"] = LCSC.get(p["fp"], "")
        LCSC_BY_REF[p["ref"]] = p["lcsc"]
    return d

# Symbol-Definitionen (aus der KiCad-Standardbibliothek, eingebettet)
SYMBOLS = {
    'Mechanical:Fiducial': r'''  (symbol "Mechanical:Fiducial" (in_bom yes) (on_board yes)
    (property "Reference" "FID" (at 0 5.08 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "Fiducial" (at 0 3.175 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "~" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "fiducial marker" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Fiducial Marker" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_fp_filters" "Fiducial*" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "Fiducial_0_1"
      (circle (center 0 0) (radius 1.27)
        (stroke (width 0.508) (type default))
        (fill (type background))
      )
    )
  )''',
    'LED:WS2812B': r'''  (symbol "LED:WS2812B" (pin_names (offset 0.254)) (in_bom yes) (on_board yes)
    (property "Reference" "D" (at 5.08 5.715 0)
      (effects (font (size 1.27 1.27)) (justify right bottom))
    )
    (property "Value" "WS2812B" (at 1.27 -5.715 0)
      (effects (font (size 1.27 1.27)) (justify left top))
    )
    (property "Footprint" "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm" (at 1.27 -7.62 0)
      (effects (font (size 1.27 1.27)) (justify left top) hide)
    )
    (property "Datasheet" "https://cdn-shop.adafruit.com/datasheets/WS2812B.pdf" (at 2.54 -9.525 0)
      (effects (font (size 1.27 1.27)) (justify left top) hide)
    )
    (property "ki_keywords" "RGB LED NeoPixel addressable" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "RGB LED with integrated controller" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_fp_filters" "LED*WS2812*PLCC*5.0x5.0mm*P3.2mm*" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "WS2812B_0_0"
      (text "RGB" (at 2.286 -4.191 0)
        (effects (font (size 0.762 0.762)))
      )
    )
    (symbol "WS2812B_0_1"
      (polyline
        (pts
          (xy 1.27 -3.556)
          (xy 1.778 -3.556)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 1.27 -2.54)
          (xy 1.778 -2.54)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 4.699 -3.556)
          (xy 2.667 -3.556)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 2.286 -2.54)
          (xy 1.27 -3.556)
          (xy 1.27 -3.048)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 2.286 -1.524)
          (xy 1.27 -2.54)
          (xy 1.27 -2.032)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 3.683 -1.016)
          (xy 3.683 -3.556)
          (xy 3.683 -4.064)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 4.699 -1.524)
          (xy 2.667 -1.524)
          (xy 3.683 -3.556)
          (xy 4.699 -1.524)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (rectangle (start 5.08 5.08) (end -5.08 -5.08)
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
    )
    (symbol "WS2812B_1_1"
      (pin power_in line (at 0 7.62 270) (length 2.54)
        (name "VDD" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin output line (at 7.62 0 180) (length 2.54)
        (name "DOUT" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
      (pin power_in line (at 0 -7.62 90) (length 2.54)
        (name "VSS" (effects (font (size 1.27 1.27))))
        (number "3" (effects (font (size 1.27 1.27))))
      )
      (pin input line (at -7.62 0 0) (length 2.54)
        (name "DIN" (effects (font (size 1.27 1.27))))
        (number "4" (effects (font (size 1.27 1.27))))
      )
    )
  )''',
    'Device:C_Small': r'''  (symbol "Device:C_Small" (pin_numbers hide) (pin_names (offset 0.254) hide) (in_bom yes) (on_board yes)
    (property "Reference" "C" (at 0.254 1.778 0)
      (effects (font (size 1.27 1.27)) (justify left))
    )
    (property "Value" "C_Small" (at 0.254 -2.032 0)
      (effects (font (size 1.27 1.27)) (justify left))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "~" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "capacitor cap" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Unpolarized capacitor, small symbol" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_fp_filters" "C_*" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "C_Small_0_1"
      (polyline
        (pts
          (xy -1.524 -0.508)
          (xy 1.524 -0.508)
        )
        (stroke (width 0.3302) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy -1.524 0.508)
          (xy 1.524 0.508)
        )
        (stroke (width 0.3048) (type default))
        (fill (type none))
      )
    )
    (symbol "C_Small_1_1"
      (pin passive line (at 0 2.54 270) (length 2.032)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin passive line (at 0 -2.54 90) (length 2.032)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
    )
  )''',
    'Device:R_Small': r'''  (symbol "Device:R_Small" (pin_numbers hide) (pin_names (offset 0.254) hide) (in_bom yes) (on_board yes)
    (property "Reference" "R" (at 0.762 0.508 0)
      (effects (font (size 1.27 1.27)) (justify left))
    )
    (property "Value" "R_Small" (at 0.762 -1.016 0)
      (effects (font (size 1.27 1.27)) (justify left))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "~" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "R resistor" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Resistor, small symbol" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_fp_filters" "R_*" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "R_Small_0_1"
      (rectangle (start -0.762 1.778) (end 0.762 -1.778)
        (stroke (width 0.2032) (type default))
        (fill (type none))
      )
    )
    (symbol "R_Small_1_1"
      (pin passive line (at 0 2.54 270) (length 0.762)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin passive line (at 0 -2.54 90) (length 0.762)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
    )
  )''',
    'power:+5V': r'''  (symbol "power:+5V" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes)
    (property "Reference" "#PWR" (at 0 -3.81 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Value" "+5V" (at 0 3.556 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "global power" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Power symbol creates a global label with name \"+5V\"" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "+5V_0_1"
      (polyline
        (pts
          (xy -0.762 1.27)
          (xy 0 2.54)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 0 0)
          (xy 0 2.54)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
      (polyline
        (pts
          (xy 0 2.54)
          (xy 0.762 1.27)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
    )
    (symbol "+5V_1_1"
      (pin power_in line (at 0 0 90) (length 0) hide
        (name "+5V" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )''',
    'power:GND': r'''  (symbol "power:GND" (power) (pin_names (offset 0)) (in_bom yes) (on_board yes)
    (property "Reference" "#PWR" (at 0 -6.35 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Value" "GND" (at 0 -3.81 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "global power" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Power symbol creates a global label with name \"GND\" , ground" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "GND_0_1"
      (polyline
        (pts
          (xy 0 0)
          (xy 0 -1.27)
          (xy 1.27 -1.27)
          (xy 0 -2.54)
          (xy -1.27 -1.27)
          (xy 0 -1.27)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
    )
    (symbol "GND_1_1"
      (pin power_in line (at 0 0 270) (length 0) hide
        (name "GND" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )''',
    'power:PWR_FLAG': r'''  (symbol "power:PWR_FLAG" (power) (pin_numbers hide) (pin_names (offset 0) hide) (in_bom yes) (on_board yes)
    (property "Reference" "#FLG" (at 0 1.905 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Value" "PWR_FLAG" (at 0 3.81 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "~" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "flag power" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Special symbol for telling ERC where power comes from" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "PWR_FLAG_0_0"
      (pin power_out line (at 0 0 90) (length 0)
        (name "pwr" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
    (symbol "PWR_FLAG_0_1"
      (polyline
        (pts
          (xy 0 0)
          (xy 0 1.27)
          (xy -1.016 1.905)
          (xy 0 2.54)
          (xy 1.016 1.905)
          (xy 0 1.27)
        )
        (stroke (width 0) (type default))
        (fill (type none))
      )
    )
  )''',
    'Connector_Generic:Conn_01x01': r'''  (symbol "Connector_Generic:Conn_01x01" (pin_names (offset 1.016) hide) (in_bom yes) (on_board yes)
    (property "Reference" "J" (at 0 2.54 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "Conn_01x01" (at 0 -2.54 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "~" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "connector" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Generic connector, single row, 01x01, script generated (kicad-library-utils/schlib/autogen/connector/)" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_fp_filters" "Connector*:*_1x??_*" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "Conn_01x01_1_1"
      (rectangle (start -1.27 0.127) (end 0 -0.127)
        (stroke (width 0.1524) (type default))
        (fill (type none))
      )
      (rectangle (start -1.27 1.27) (end 1.27 -1.27)
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
      (pin passive line (at -5.08 0 0) (length 3.81)
        (name "Pin_1" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )''',
    'Mechanical:MountingHole': r'''  (symbol "Mechanical:MountingHole" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)
    (property "Reference" "H" (at 0 5.08 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "MountingHole" (at 0 3.175 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "~" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "mounting hole" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Mounting Hole without connection" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_fp_filters" "MountingHole*" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "MountingHole_0_1"
      (circle (center 0 0) (radius 1.27)
        (stroke (width 1.27) (type default))
        (fill (type none))
      )
    )
  )''',
    '74xGxx:74AHCT1G125': r'''  (symbol "74xGxx:74AHCT1G125" (in_bom yes) (on_board yes)
    (property "Reference" "U" (at -10.16 7.62 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "74AHCT1G125" (at 2.54 -7.62 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "Datasheet" "https://www.ti.com/lit/ds/symlink/sn74lvc1g125.pdf" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_keywords" "Single Gate Buff Tri-State LVC CMOS" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_description" "Single Buffer Gate Tri-State, Low-Voltage CMOS" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "ki_fp_filters" "SOT?23* Texas?R-PDSO-G5?DCK* Texas?R-PDSO-N5?DRL* Texas?X2SON*0.8x0.8mm*P0.48mm*" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (symbol "74AHCT1G125_0_1"
      (polyline
        (pts
          (xy -7.62 6.35)
          (xy -7.62 -6.35)
          (xy 5.08 0)
          (xy -7.62 6.35)
        )
        (stroke (width 0.254) (type default))
        (fill (type background))
      )
    )
    (symbol "74AHCT1G125_1_1"
      (pin input inverted (at 0 10.16 270) (length 7.62)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin input line (at -15.24 0 0) (length 7.62)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
      (pin power_in line (at -5.08 -10.16 90) (length 5.08)
        (name "GND" (effects (font (size 1.27 1.27))))
        (number "3" (effects (font (size 1.27 1.27))))
      )
      (pin tri_state line (at 12.7 0 180) (length 7.62)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "4" (effects (font (size 1.27 1.27))))
      )
      (pin power_in line (at -5.08 10.16 270) (length 5.08)
        (name "VCC" (effects (font (size 1.27 1.27))))
        (number "5" (effects (font (size 1.27 1.27))))
      )
    )
  )''',
}

# =============================================================================
# SCHALTPLAN (.kicad_sch, Format KiCad 7)
# =============================================================================
# Pin-Endpunkte relativ zum Symbolursprung (Symbolkoordinaten, y nach oben)
SYM_PINS = {
    "LED:WS2812B": {"1": (0, 7.62), "2": (7.62, 0), "3": (0, -7.62), "4": (-7.62, 0)},
    "Device:C_Small": {"1": (0, 2.54), "2": (0, -2.54)},
    "Device:R_Small": {"1": (0, 2.54), "2": (0, -2.54)},
    "74xGxx:74AHCT1G125": {"1": (0, 10.16), "2": (-15.24, 0), "3": (-5.08, -10.16),
                           "4": (12.7, 0), "5": (-5.08, 10.16)},
    "Connector_Generic:Conn_01x01": {"1": (-5.08, 0)},
    "Mechanical:MountingHole": {},
    "Mechanical:Fiducial": {},
}
FOOTPRINT_FIELD = {k: v["lib"] for k, v in FP.items()}
DATASHEET = {"LED:WS2812B": "https://cdn-shop.adafruit.com/datasheets/WS2812B.pdf",
             "74xGxx:74AHCT1G125": "https://www.ti.com/lit/ds/symlink/sn74ahct1g125.pdf"}


class Sch:
    def __init__(self, root):
        self.root = root
        self.items = []
        self.pwr_n = 0

    def _eff(self, hide=False, just=None):
        j = " (justify %s)" % just if just else ""
        return "(effects (font (size 1.27 1.27))%s%s)" % (j, " hide" if hide else "")

    def symbol(self, lib_id, ref, value, x, y, angle=0, footprint="", key=None,
               ref_off=(2.54, -1.27), val_off=(2.54, 1.27), hide_val=False, pins=()):
        key = key or ref
        u = uid("sym", key)
        props = [
            '(property "Reference" "%s" (at %s %s 0) %s)' % (ref, f(x + ref_off[0]), f(y + ref_off[1]), self._eff(just="left")),
            '(property "Value" "%s" (at %s %s 0) %s)' % (value, f(x + val_off[0]), f(y + val_off[1]), self._eff(hide=hide_val, just="left")),
            '(property "Footprint" "%s" (at %s %s 0) %s)' % (footprint, f(x), f(y), self._eff(hide=True)),
            '(property "Datasheet" "%s" (at %s %s 0) %s)' % (DATASHEET.get(lib_id, "~"), f(x), f(y), self._eff(hide=True)),
        ]
        if LCSC_BY_REF.get(ref):
            props.append('(property "LCSC" "%s" (at %s %s 0) %s)' % (LCSC_BY_REF[ref], f(x), f(y), self._eff(hide=True)))
        pin_s = " ".join('(pin "%s" (uuid %s))' % (p, uid("pin", key, p)) for p in pins)
        self.items.append(
            '  (symbol (lib_id "%s") (at %s %s %d) (unit 1)\n'
            '    (in_bom yes) (on_board yes) (dnp no)\n'
            '    (uuid %s)\n    %s\n    %s\n'
            '    (instances (project "%s" (path "/%s" (reference "%s") (unit 1))))\n  )'
            % (lib_id, f(x), f(y), angle, u, "\n    ".join(props), pin_s, PROJECT, self.root, ref))
        return u

    def power(self, kind, x, y, angle=0):
        self.pwr_n += 1
        ref = "#PWR%04d" % self.pwr_n if kind != "PWR_FLAG" else "#FLG%02d" % self.pwr_n
        lib = "power:" + kind
        val = kind
        u = uid("pwr", self.pwr_n)
        up = angle == 0 and kind != "GND" or angle == 180 and kind == "GND"
        ty = y - 3.81 if up else y + 3.81
        self.items.append(
            '  (symbol (lib_id "%s") (at %s %s %d) (unit 1)\n'
            '    (in_bom yes) (on_board yes) (dnp no)\n    (uuid %s)\n'
            '    (property "Reference" "%s" (at %s %s 0) %s)\n'
            '    (property "Value" "%s" (at %s %s 0) %s)\n'
            '    (property "Footprint" "" (at %s %s 0) %s)\n'
            '    (property "Datasheet" "" (at %s %s 0) %s)\n'
            '    (pin "1" (uuid %s))\n'
            '    (instances (project "%s" (path "/%s" (reference "%s") (unit 1))))\n  )'
            % (lib, f(x), f(y), angle, u, ref, f(x), f(y), self._eff(hide=True),
               val, f(x), f(ty), self._eff(hide=(kind != "PWR_FLAG" and False)),
               f(x), f(y), self._eff(hide=True), f(x), f(y), self._eff(hide=True),
               uid("pwrpin", self.pwr_n), PROJECT, self.root, ref))

    def label(self, name, x, y, left=True):
        ang = 180 if left else 0
        just = "right bottom" if left else "left bottom"
        self.items.append('  (label "%s" (at %s %s %d) (effects (font (size 1.27 1.27)) (justify %s)) (uuid %s))'
                          % (name, f(x), f(y), ang, just, uid("lbl", name, x, y)))

    def wire(self, x1, y1, x2, y2):
        self.items.append('  (wire (pts (xy %s %s) (xy %s %s)) (stroke (width 0) (type default)) (uuid %s))'
                          % (f(x1), f(y1), f(x2), f(y2), uid("wire", x1, y1, x2, y2)))

    def text(self, s, x, y, size=2.54):
        self.items.append('  (text "%s" (at %s %s 0) (effects (font (size %s %s)) (justify left bottom)) (uuid %s))'
                          % (s, f(x), f(y), f(size), f(size), uid("text", s)))


def pin_xy(lib_id, pin, x, y):
    px, py = SYM_PINS[lib_id][pin]
    return (x + px, y - py)


def attach(sch, lib_id, pin, x, y, net, label_left=True):
    """Haengt an einen Pin ein Power-Symbol oder ein Label."""
    px, py = pin_xy(lib_id, pin, x, y)
    if net == "+5V":
        sch.power("+5V", px, py, 0 if py <= y else 180)
    elif net == "GND":
        sch.power("GND", px, py, 0 if py >= y else 180)
    else:
        sch.label(net.lstrip("/"), px, py, left=label_left)


def write_sch(d, path):
    root = uid("root-sheet")
    s = Sch(root)
    BX, BY = 43.18, 25.4
    X0, Y0 = 38.1, 50.8
    parts = {p["ref"]: p for p in d.parts}
    s.text(TITLE + " - Datenkette D1 -> D%d als Serpentine, Anordnung wie auf der Platine" % N_LED, X0 - 12.7, Y0 - 25.4, 3.0)

    # LED-Raster: gleiche Anordnung wie auf der Platine
    for r in range(ROWS):
        for c in range(COLS):
            n = led_no(c, r)
            x, y = X0 + c * BX, Y0 + r * BY
            led = parts["D%d" % n]
            s.symbol("LED:WS2812B", led["ref"], "WS2812B", x, y, footprint=FP["LED"]["lib"],
                     ref_off=(6.35, -6.35), val_off=(6.35, 6.35), hide_val=True, pins="1234")
            attach(s, "LED:WS2812B", "1", x, y, "+5V")
            attach(s, "LED:WS2812B", "3", x, y, "GND")
            attach(s, "LED:WS2812B", "4", x, y, led["pins"]["4"], label_left=True)
            attach(s, "LED:WS2812B", "2", x, y, led["pins"]["2"], label_left=False)
            cx, cy = x + 20.32, y - 7.62
            s.symbol("Device:C_Small", "C%d" % n, "100nF", cx, cy, footprint=FP["C0402"]["lib"],
                     ref_off=(1.27, -0.635), val_off=(1.27, 1.905), hide_val=True, pins="12")
            attach(s, "Device:C_Small", "1", cx, cy, "+5V")
            attach(s, "Device:C_Small", "2", cx, cy, "GND")

    # Peripherie unterhalb des Rasters
    y = Y0 + ROWS * BY + 25.4
    s.text("Eingang: 3,3V-Daten -> Pegelwandler -> D1   |   Ausgang D%d -> J3   |   Einspeisung +5V/GND (Unterseite)" % N_LED,
           X0 - 12.7, y - 17.78, 2.0)
    # J1 -> U1 -> R1 -> D000
    x = X0 + 12.7
    s.symbol("Connector_Generic:Conn_01x01", "J1", "DIN", x, y, angle=0, footprint=FP["PAD_SIG"]["lib"],
             ref_off=(-2.54, -2.54), val_off=(-2.54, 2.54), pins="1")
    # Conn_01x01 bei 0 Grad: Pin links -> Symbol spiegeln waere huebscher, Label genuegt
    attach(s, "Connector_Generic:Conn_01x01", "1", x, y, "/DATA_IN", label_left=True)
    ux = x + 45.72
    s.symbol("74xGxx:74AHCT1G125", "U1", "74AHCT1G125", ux, y, footprint=FP["SOT235"]["lib"],
             ref_off=(2.54, -8.89), val_off=(2.54, 8.89), pins="12345")
    attach(s, "74xGxx:74AHCT1G125", "2", ux, y, "/DATA_IN", label_left=True)
    attach(s, "74xGxx:74AHCT1G125", "4", ux, y, "/DATA_Y", label_left=False)
    attach(s, "74xGxx:74AHCT1G125", "5", ux, y, "+5V")
    attach(s, "74xGxx:74AHCT1G125", "3", ux, y, "GND")
    ox, oy = pin_xy("74xGxx:74AHCT1G125", "1", ux, y)
    s.power("GND", ox, oy, 180)
    rx, ry = ux + 38.1, y
    s.symbol("Device:R_Small", "R1", "33R", rx, ry, footprint=FP["R0402"]["lib"],
             ref_off=(1.27, -0.635), val_off=(1.27, 1.905), pins="12")
    s.wire(rx, ry - 2.54, rx, ry - 7.62)
    s.label(dnet(0).lstrip("/"), rx, ry - 7.62, left=False)
    s.wire(rx, ry + 2.54, rx, ry + 7.62)
    s.label("DATA_Y", rx, ry + 7.62, left=False)
    cx = rx + 20.32
    s.symbol("Device:C_Small", "C%d" % (N_LED + 1), "100nF", cx, y, footprint=FP["C0402"]["lib"],
             ref_off=(1.27, -0.635), val_off=(1.27, 1.905), pins="12")
    attach(s, "Device:C_Small", "1", cx, y, "+5V")
    attach(s, "Device:C_Small", "2", cx, y, "GND")
    # J2..J(n): Pads
    jx = cx + 25.4
    for p in d.parts:
        if not p["ref"].startswith("J") or p["ref"] == "J1":
            continue
        s.symbol("Connector_Generic:Conn_01x01", p["ref"], p["value"], jx, y, footprint=FP[p["fp"]]["lib"],
                 ref_off=(-2.54, -2.54), val_off=(-2.54, 2.54), pins="1")
        net = p["pins"]["1"]
        px, py = pin_xy("Connector_Generic:Conn_01x01", "1", jx, y)
        if net in ("+5V", "GND"):
            s.wire(px, py, px, py - 5.08 if net == "+5V" else py + 5.08)
            s.power(net, px, py - 5.08 if net == "+5V" else py + 5.08, 0)
        else:
            s.label(net.lstrip("/"), px, py, left=True)
        jx += 15.24
    # PWR_FLAGs
    fx = jx + 7.62
    s.power("+5V", fx, y - 5.08, 0)
    s.wire(fx, y - 5.08, fx, y)
    s.power("PWR_FLAG", fx, y, 180)
    s.power("GND", fx + 10.16, y + 5.08, 0)
    s.wire(fx + 10.16, y + 5.08, fx + 10.16, y)
    s.power("PWR_FLAG", fx + 10.16, y, 0)
    # Loecher
    hx, hy = X0, y + 30.48
    for p in d.parts:
        if p["ref"].startswith("H"):
            s.symbol("Mechanical:MountingHole", p["ref"], "Hole", hx, hy, footprint=FP["HOLE"]["lib"],
                     ref_off=(2.54, -1.27), val_off=(2.54, 1.27), hide_val=True)
            hx += 17.78
    for p in d.parts:
        if p["ref"].startswith("FID"):
            s.symbol("Mechanical:Fiducial", p["ref"], "Fiducial", hx, hy, footprint=FP["FID"]["lib"],
                     ref_off=(2.54, -1.27), val_off=(2.54, 1.27), hide_val=True)
            hx += 17.78

    libs = "\n".join(SYMBOLS[k] for k in sorted(SYMBOLS))
    out = ('(kicad_sch (version 20230121) (generator eeschema)\n\n  (uuid %s)\n\n  (paper "A1")\n\n'
           '  (title_block\n    (title "%s")\n    (rev "1")\n    (comment 1 "generiert mit gen_ledmatrix.py")\n  )\n\n'
           '  (lib_symbols\n%s\n  )\n\n%s\n\n  (sheet_instances\n    (path "/" (page "1"))\n  )\n)\n'
           % (root, TITLE, libs, "\n".join(s.items)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)

# =============================================================================
# LAYOUT (.kicad_pcb, Format KiCad 7)
# =============================================================================
class Pcb:
    def __init__(self, d):
        self.d = d
        self.fps = []
        self.items = []
        self.n = 0

    def t(self, *k):
        self.n += 1
        return uid("pcb", self.n, *k)

    def netno(self, name):
        return self.d.nets.index(name)

    # ---------------- Footprint ----------------
    def footprint(self, part, x, y, angle, side="F"):
        fp = FP[part["fp"]]
        B = side == "B"
        L = (lambda l: LAYER_FLIP.get(l, l)) if B else (lambda l: l)
        my = (lambda p: (p[0], -p[1])) if B else (lambda p: p)
        mir = " (justify mirror)" if B else ""
        o = []
        o.append('  (footprint "%s" (layer "%s")\n    (tstamp %s)\n    (at %s %s %s)'
                 % (fp["lib"], L("F.Cu"), self.t(part["ref"]), f(x), f(y), f(angle)))
        o.append('    (descr "%s")\n    (tags "%s")' % (fp["descr"], fp["tags"]))
        o.append('    (property "Sheetfile" "%s.kicad_sch")\n    (property "Sheetname" "")' % PROJECT)
        if part.get("lcsc"):
            o.append('    (property "LCSC" "%s")' % part["lcsc"])
        part["place"] = (x, y, angle, side)
        o.append('    (path "/%s")' % part["uuid"])
        o.append('    (attr %s)' % fp["attr"])
        rx, ry = my(fp["ref_at"])
        o.append('    (fp_text reference "%s" (at %s %s %s) (layer "%s")\n      (effects (font (size 0.8 0.8) (thickness 0.12))%s)\n      (tstamp %s)\n    )'
                 % (part["ref"], f(rx), f(ry), f(angle), L("F.Fab"), mir, self.t()))
        o.append('    (fp_text value "%s" (at 0 %s %s) (layer "%s") hide\n      (effects (font (size 0.8 0.8) (thickness 0.12))%s)\n      (tstamp %s)\n    )'
                 % (part["value"], f(my((0, 1.5))[1]), f(angle), L("F.Fab"), mir, self.t()))
        for layer, w, lines in (("F.SilkS", 0.12, fp["silk"]), ("F.Fab", 0.1, fp["fab"])):
            for a, b in lines:
                a, b = my(a), my(b)
                o.append('    (fp_line (start %s %s) (end %s %s) (stroke (width %s) (type solid)) (layer "%s") (tstamp %s))'
                         % (f(a[0]), f(a[1]), f(b[0]), f(b[1]), f(w), L(layer), self.t()))
        if fp["crtyd"]:
            x1, y1, x2, y2 = fp["crtyd"]
            o.append('    (fp_rect (start %s %s) (end %s %s) (stroke (width 0.05) (type solid)) (fill none) (layer "%s") (tstamp %s))'
                     % (f(x1), f(y1), f(x2), f(y2), L("F.CrtYd"), self.t()))
        for num, typ, shape, (px, py), (w, h), drill, rr in fp["pads"]:
            px, py = my((px, py))
            if typ == "np_thru_hole":
                layers = '"*.Cu" "*.Mask"'
            else:
                layers = '"%s" "%s" "%s"' % (L("F.Cu"), L("F.Paste"), L("F.Mask"))
                if part["fp"].startswith("PAD_"):
                    layers = '"%s" "%s"' % (L("F.Cu"), L("F.Mask"))
                if fp.get("pad_layers"):
                    layers = " ".join('"%s"' % L(l) for l in fp["pad_layers"])
            net = part["pins"].get(num)
            s = '    (pad "%s" %s %s (at %s %s %s) (size %s %s)' % (num, typ, shape, f(px), f(py), f(angle), f(w), f(h))
            if drill:
                s += ' (drill %s)' % f(drill)
            s += ' (layers %s)' % layers
            if rr:
                s += ' (roundrect_rratio %s)' % f(rr)
            if fp.get("pad_extra"):
                s += ' ' + fp["pad_extra"]
            if net:
                s += ' (net %d "%s")' % (self.netno(net), net)
            s += ' (tstamp %s))' % self.t()
            o.append(s)
        if fp["model"]:
            o.append('    (model "%s"\n      (offset (xyz 0 0 0))\n      (scale (xyz 1 1 1))\n      (rotate (xyz 0 0 0))\n    )' % fp["model"])
        o.append('  )')
        self.fps.append("\n".join(o))

    # ---------------- Kupfer ----------------
    def seg(self, pts, net, w=TRACK_DATA, layer="F.Cu"):
        for a, b in zip(pts, pts[1:]):
            self.items.append('  (segment (start %s %s) (end %s %s) (width %s) (layer "%s") (net %d) (tstamp %s))'
                              % (f(a[0]), f(a[1]), f(b[0]), f(b[1]), f(w), layer, self.netno(net), self.t()))

    def via(self, p, net, size=VIA):
        self.items.append('  (via (at %s %s) (size %s) (drill %s) (layers "F.Cu" "B.Cu") (net %d) (tstamp %s))'
                          % (f(p[0]), f(p[1]), f(size[0]), f(size[1]), self.netno(net), self.t()))

    def poly(self, pts):
        return "(polygon (pts %s))" % " ".join("(xy %s %s)" % (f(x), f(y)) for x, y in pts)

    def zone(self, net, layer, pts, prio=0):
        self.items.append(
            '  (zone (net %d) (net_name "%s") (layer "%s") (tstamp %s) (name "%s_%s") (hatch edge 0.5)%s\n'
            '    (connect_pads (clearance 0.25))\n    (min_thickness 0.25) (filled_areas_thickness no)\n'
            '    (fill yes (thermal_gap 0.4) (thermal_bridge_width 0.4))\n    %s\n  )'
            % (self.netno(net), net, layer, self.t(), net.strip("+"), layer.split(".")[0],
               " (priority %d)" % prio if prio else "", self.poly(pts)))

    def keepout(self, pts, name):
        self.items.append(
            '  (zone (net 0) (net_name "") (layer "F.Cu") (tstamp %s) (name "%s") (hatch edge 0.5)\n'
            '    (connect_pads (clearance 0))\n    (min_thickness 0.25) (filled_areas_thickness no)\n'
            '    (keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour allowed) (footprints not_allowed))\n'
            '    (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))\n    %s\n  )' % (self.t(), name, self.poly(pts)))

    def gline(self, a, b, layer, w=0.1):
        self.items.append('  (gr_line (start %s %s) (end %s %s) (stroke (width %s) (type default)) (layer "%s") (tstamp %s))'
                          % (f(a[0]), f(a[1]), f(b[0]), f(b[1]), f(w), layer, self.t()))

    def grect(self, a, b, layer, w=0.1):
        self.items.append('  (gr_rect (start %s %s) (end %s %s) (stroke (width %s) (type default)) (fill none) (layer "%s") (tstamp %s))'
                          % (f(a[0]), f(a[1]), f(b[0]), f(b[1]), f(w), layer, self.t()))

    def text(self, s, p, layer, size=1.2, mirror=False, just=None):
        j = []
        if just:
            j.append(just)
        if mirror:
            j.append("mirror")
        js = " (justify %s)" % " ".join(j) if j else ""
        self.items.append('  (gr_text "%s" (at %s %s) (layer "%s") (tstamp %s)\n    (effects (font (size %s %s) (thickness %s))%s)\n  )'
                          % (s, f(p[0]), f(p[1]), layer, self.t(), f(size), f(size), f(size * 0.15), js))


def write_pcb(d, path):
    pcb = Pcb(d)
    parts = {p["ref"]: p for p in d.parts}
    W, H = COLS * PITCH, ROWS * PITCH
    X0, Y0 = ORIGIN

    def A(c, r, lx, ly):
        """Zell-lokale Koordinate (kanonisch: Fluss nach rechts) -> absolut.
        Zeilen mit Fluss nach links sind um 180 Grad gedreht."""
        cx, cy = cell_center(c, r)
        s = 1 if flow_right(r) else -1
        return (cx + s * lx, cy + s * ly)

    # --- Zellen: LED + C + Vias + Stichleitungen ---------------------------
    # Kanonische Zelle (LED 180 Grad): VDD(2.45,1.65) DOUT(2.45,-1.65)
    #                                  GND(-2.45,-1.65) DIN(-2.45,1.65)
    C_POS = (0.6, 3.5)
    V_VDD = (2.45, 3.5)
    V_GNDC = (-1.0, 3.5)
    V_GNDL = (-2.45, -3.4)
    for r in range(ROWS):
        for c in range(COLS):
            n = led_no(c, r)
            ang = 180 if flow_right(r) else 0
            cx, cy = cell_center(c, r)
            pcb.footprint(parts["D%d" % n], cx, cy, ang)
            cpx, cpy = A(c, r, *C_POS)
            pcb.footprint(parts["C%d" % n], cpx, cpy, ang)
            # VDD: LED-Pad -> Via, C-Pad1 -> Via
            pcb.seg([A(c, r, 2.45, 1.65), A(c, r, *V_VDD)], "+5V", TRACK_PWR)
            pcb.seg([A(c, r, C_POS[0] + 0.48, C_POS[1]), A(c, r, *V_VDD)], "+5V", 0.3)
            if LAYERS == 4:   # bei 2 Lagen haengt VDD direkt an der +5V-Flaeche auf F.Cu
                pcb.via(A(c, r, *V_VDD), "+5V")
            # GND: C-Pad2 -> Via, LED-Pad3 -> Via
            pcb.seg([A(c, r, C_POS[0] - 0.48, C_POS[1]), A(c, r, *V_GNDC)], "GND", 0.3)
            pcb.via(A(c, r, *V_GNDC), "GND")
            pcb.seg([A(c, r, -2.45, -1.65), A(c, r, *V_GNDL)], "GND", TRACK_PWR)
            pcb.via(A(c, r, *V_GNDL), "GND")

    # --- Datenkette ---------------------------------------------------------
    for n in range(1, N_LED):
        c, r = led_pos(n)
        net = dnet(n)
        c2, r2 = led_pos(n + 1)
        if r2 == r:   # Nachbar in derselben Zeile
            pts = [(2.45, -1.65), (3.5, -1.65), (6.8, 1.65), (7.55, 1.65)]
            pcb.seg([A(c, r, *p) for p in pts], net)
        else:         # Zeilenwechsel: aussen am Rand herum nach unten
            cx, cy = cell_center(c, r)
            if flow_right(r):   # rechter Rand
                pts = [(cx + 2.45, cy - 1.65), (cx + 4.1, cy - 1.65), (cx + 4.1, cy + PITCH - 1.65), (cx + 2.45, cy + PITCH - 1.65)]
            else:               # linker Rand
                pts = [(cx - 2.45, cy + 1.65), (cx - 4.1, cy + 1.65), (cx - 4.1, cy + PITCH + 1.65), (cx - 2.45, cy + PITCH + 1.65)]
            pcb.seg(pts, net)

    # --- Eingang (Unterseite, links oben unter LED 1) ------------------------
    c1, r1 = led_pos(1)
    cx, cy = cell_center(c1, r1)
    s1 = 1 if flow_right(r1) else -1
    din_via = A(c1, r1, -4.0, 1.65)
    pcb.seg([A(c1, r1, -2.45, 1.65), din_via], dnet(0))
    pcb.via(din_via, dnet(0))
    # U1 an der Zellgrenze zu Zelle 2 (dort ist unten frei), 180 Grad
    ux, uy = cx + 5.0, cy
    pcb.footprint(parts["U1"], ux, uy, 180, "B")
    P = lambda num: pad_abs("SOT235", num, ux, uy, 180, "B")
    # R1 unter LED 1
    rx, ry = cx - 1.0, din_via[1]
    pcb.footprint(parts["R1"], rx, ry, 0, "B")
    R = lambda num: pad_abs("R0402", num, rx, ry, 0, "B")
    pcb.seg([din_via, R("1")], dnet(0), layer="B.Cu")
    y4 = P("4")
    pcb.seg([R("2"), (y4[0] - 0.7, R("2")[1]), y4], "/DATA_Y", layer="B.Cu")
    # U1 VCC -> +5V-Via (unter dem LED-Koerper, oben frei) + C257
    v5 = (cx + 1.0, cy - 1.8)
    p5 = P("5")
    pcb.seg([p5, (cx + 1.85, p5[1]), v5], "+5V", 0.3, "B.Cu")
    pcb.via(v5, "+5V")
    cdx, cdy = cx + 2.6, cy - 2.3
    pcb.footprint(parts["C%d" % (N_LED + 1)], cdx, cdy, 0, "B")
    pcb.seg([pad_abs("C0402", "1", cdx, cdy, 0, "B"), v5], "+5V", 0.3, "B.Cu")
    # GND-Pins von U1 in die B.Cu-Flaeche ziehen
    pcb.seg([P("1"), (P("1")[0], P("1")[1] - 1.0)], "GND", 0.3, "B.Cu")
    pcb.seg([P("3"), (P("3")[0], P("3")[1] + 1.0)], "GND", 0.3, "B.Cu")
    # J1 (DIN) und J2 (GND) Pads in Zelle 2/3
    jx, jy = cx + 10.0, cy
    pcb.footprint(parts["J1"], jx, jy, 0, "B")
    pcb.seg([P("2"), (jx - 1.5, jy)], "/DATA_IN", layer="B.Cu")
    pcb.footprint(parts["J2"], cx + 20.0, cy, 0, "B")
    pcb.text("DIN", (jx, jy + 2.4), "B.SilkS", 1.0, mirror=True)
    pcb.text("GND", (cx + 20.0, cy + 2.4), "B.SilkS", 1.0, mirror=True)
    pcb.text("AHCT125", (ux, uy + 3.0), "B.SilkS", 0.8, mirror=True)

    # --- Ausgang (Unterseite, unter der letzten LED) -------------------------
    cn, rn = led_pos(N_LED)
    ocx, ocy = cell_center(cn, rn)
    # DOUT der letzten LED liegt kanonisch bei (2.45,-1.65)
    dp = A(cn, rn, 2.45, -1.65)
    dout_via = (dp[0] + (1.55 if dp[0] > ocx else -1.55), dp[1])
    pcb.seg([dp, dout_via], dnet(N_LED))
    pcb.via(dout_via, dnet(N_LED))
    dirx = 1 if dout_via[0] < ocx else -1          # Richtung ins Innere
    j3 = (ocx + dirx * 10.0, ocy)
    pcb.footprint(parts["J3"], j3[0], j3[1], 0, "B")
    pcb.seg([dout_via, (dout_via[0] + dirx * 1.65, ocy), (j3[0] - dirx * 1.5, ocy)], dnet(N_LED), layer="B.Cu")
    pcb.footprint(parts["J4"], ocx + dirx * 20.0, ocy, 0, "B")
    pcb.text("DOUT", (j3[0], ocy + 2.4), "B.SilkS", 1.0, mirror=True)
    pcb.text("GND", (ocx + dirx * 20.0, ocy + 2.4), "B.SilkS", 1.0, mirror=True)

    # --- Einspeisung (Unterseite) --------------------------------------------
    j = 5
    for (c, r) in POWER_FEEDS:
        for k, net in ((0, "+5V"), (1, "GND")):
            px, py = cell_center(c + k, r)
            pcb.footprint(parts["J%d" % (j + k)], px, py, 0, "B")
            if net == "+5V" or LAYERS == 4:   # bei 2 Lagen liegt GND schon auf B.Cu
                for vx in (-0.7, 0.7):
                    for vy in (-1.5, 0, 1.5):
                        pcb.via((px + vx, py + vy), net, VIA_PWR)
            pcb.text("+5V" if net == "+5V" else "GND", (px, py + 3.3), "B.SilkS", 0.9, mirror=True)
        j += 2

    # --- Loecher an Steg-Kreuzungen -----------------------------------------
    for i, (hi, hj) in enumerate(HOLES):
        pcb.footprint(parts["H%d" % (i + 1)], X0 + hi * PITCH, Y0 + hj * PITCH, 0)
    # --- Fiducials (Oberseite, an Steg-Kreuzungen) ---------------------------
    for i, (fi, fj) in enumerate(FIDUCIALS):
        pcb.footprint(parts["FID%d" % (i + 1)], X0 + fi * PITCH, Y0 + fj * PITCH, 0)
    for i, (fi, fj) in enumerate(FIDUCIALS_BOTTOM):
        pcb.footprint(parts["FID%d" % (len(FIDUCIALS) + i + 1)], X0 + fi * PITCH, Y0 + fj * PITCH, 0, "B")

    # --- Platinenumriss, Stege, Flaechen -------------------------------------
    pcb.grect((X0, Y0), (X0 + W, Y0 + H), "Edge.Cuts", 0.1)
    hw = WALL / 2
    for i in range(COLS + 1):
        x = X0 + i * PITCH
        xa, xb = max(X0, x - hw), min(X0 + W, x + hw)
        pcb.keepout([(xa, Y0), (xb, Y0), (xb, Y0 + H), (xa, Y0 + H)], "Steg_V%d" % i)
        pcb.grect((xa, Y0), (xb, Y0 + H), "Dwgs.User", 0.05)
    for j2 in range(ROWS + 1):
        y = Y0 + j2 * PITCH
        ya, yb = max(Y0, y - hw), min(Y0 + H, y + hw)
        pcb.keepout([(X0, ya), (X0 + W, ya), (X0 + W, yb), (X0, yb)], "Steg_H%d" % j2)
        pcb.grect((X0, ya), (X0 + W, yb), "Dwgs.User", 0.05)
    outline = [(X0, Y0), (X0 + W, Y0), (X0 + W, Y0 + H), (X0, Y0 + H)]
    if LAYERS == 4:
        pcb.zone("GND", "In1.Cu", outline)
        pcb.zone("+5V", "In2.Cu", outline)
    else:
        pcb.zone("+5V", "F.Cu", outline)
    pcb.zone("GND", "B.Cu", outline)
    pcb.text(TITLE, (X0 + W / 2, Y0 + H - 5.0 - 1.9), "B.SilkS", 1.2, mirror=True)
    pcb.text("Stege %.1f mm (Dwgs.User)" % WALL, (X0 + W / 2, Y0 + H + 3), "Dwgs.User", 1.5)

    nets = "\n".join('  (net %d "%s")' % (i, n) for i, n in enumerate(d.nets))
    hdr = PCB_HEADER.replace("%TITLE%", TITLE)
    if LAYERS == 2:
        hdr = hdr.replace('    (1 "In1.Cu" power)\n    (2 "In2.Cu" power)\n', "")
        hdr = hdr.replace(STACKUP_4L, STACKUP_2L)
    out = hdr.replace("%NETS%", nets) + "\n".join(pcb.fps) + "\n" + "\n".join(pcb.items) + "\n)\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)


PCB_HEADER = """(kicad_pcb (version 20221018) (generator pcbnew)

  (general
    (thickness 1.6)
  )

  (paper "A3")
  (title_block
    (title "%TITLE%")
    (rev "1")
    (comment 1 "generiert mit gen_ledmatrix.py")
  )

  (layers
    (0 "F.Cu" signal)
    (1 "In1.Cu" power)
    (2 "In2.Cu" power)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
    (34 "B.Paste" user)
    (35 "F.Paste" user)
    (36 "B.SilkS" user "B.Silkscreen")
    (37 "F.SilkS" user "F.Silkscreen")
    (38 "B.Mask" user)
    (39 "F.Mask" user)
    (40 "Dwgs.User" user "User.Drawings")
    (41 "Cmts.User" user "User.Comments")
    (42 "Eco1.User" user "User.Eco1")
    (43 "Eco2.User" user "User.Eco2")
    (44 "Edge.Cuts" user)
    (45 "Margin" user)
    (46 "B.CrtYd" user "B.Courtyard")
    (47 "F.CrtYd" user "F.Courtyard")
    (48 "B.Fab" user)
    (49 "F.Fab" user)
  )

  (setup
    (stackup
      (layer "F.SilkS" (type "Top Silk Screen"))
      (layer "F.Paste" (type "Top Solder Paste"))
      (layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.1) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.24) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.1) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
      (layer "B.Paste" (type "Bottom Solder Paste"))
      (layer "B.SilkS" (type "Bottom Silk Screen"))
      (copper_finish "HAL lead-free")
      (dielectric_constraints no)
    )
    (pad_to_mask_clearance 0)
    (pcbplotparams
      (layerselection 0x00010fc_ffffffff)
      (plot_on_all_layers_selection 0x0000000_00000000)
      (disableapertmacros false)
      (usegerberextensions false)
      (usegerberattributes true)
      (usegerberadvancedattributes true)
      (creategerberjobfile true)
      (dashed_line_dash_ratio 12.000000)
      (dashed_line_gap_ratio 3.000000)
      (svgprecision 4)
      (plotframeref false)
      (viasonmask false)
      (mode 1)
      (useauxorigin false)
      (hpglpennumber 1)
      (hpglpenspeed 20)
      (hpglpendiameter 15.000000)
      (dxfpolygonmode true)
      (dxfimperialunits true)
      (dxfusepcbnewfont true)
      (psnegative false)
      (psa4output false)
      (plotreference true)
      (plotvalue true)
      (plotinvisibletext false)
      (sketchpadsonfab false)
      (subtractmaskfromsilk false)
      (outputformat 1)
      (mirror false)
      (drillshape 1)
      (scaleselection 1)
      (outputdirectory "gerber/")
    )
  )

%NETS%

"""

STACKUP_4L = """      (layer "dielectric 1" (type "prepreg") (thickness 0.1) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.24) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.1) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
"""
STACKUP_2L = """      (layer "dielectric 1" (type "core") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02))
"""
assert STACKUP_4L in PCB_HEADER

# =============================================================================
# JLCPCB: BOM + CPL (Bestueckungsdaten)
# =============================================================================
def write_jlc(d, folder):
    """Schreibt BOM.csv und CPL.csv im JLCPCB-Format.
    Koordinaten in mm, Ursprung = linke untere Platinenecke, Y nach oben."""
    import csv
    os.makedirs(folder, exist_ok=True)
    X0, Y0 = ORIGIN
    H = ROWS * PITCH
    sel = [p for p in d.parts if p.get("lcsc") and "place" in p
           and (JLC_BOTTOM or p["place"][3] == "F")]
    groups = {}
    for p in sel:
        k = (p["value"], FP[p["fp"]]["lib"].split(":")[1], p["lcsc"])
        groups.setdefault(k, []).append(p["ref"])
    nkey = lambda r: (r.rstrip("0123456789"), int(r[len(r.rstrip("0123456789")):] or 0))
    with open(os.path.join(folder, PROJECT + "_BOM.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (val, fpn, lc), refs in sorted(groups.items()):
            w.writerow([val, ",".join(sorted(refs, key=nkey)), fpn, lc])
    with open(os.path.join(folder, PROJECT + "_CPL.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for p in sorted(sel, key=lambda p: nkey(p["ref"])):
            x, y, ang, side = p["place"]
            corr = JLC_ROT.get(p["fp"], 0)
            if side == "F":
                rot = (ang + corr) % 360
            else:   # Unterseite: (180 - Winkel) + Korrektur (wie KiBot)
                rot = ((180 - ang) + corr) % 360
            w.writerow([p["ref"], "%.4f" % (x - X0), "%.4f" % ((Y0 + H) - y),
                        "Top" if side == "F" else "Bottom", "%g" % rot])
    manual = [p for p in d.parts if p.get("lcsc") and p not in sel]
    with open(os.path.join(folder, "Handbestueckung.txt"), "w", encoding="utf-8") as fh:
        fh.write("Nicht von JLCPCB bestueckt (JLC_BOTTOM = False), selbst loeten:\n")
        for p in manual:
            fh.write("  %-6s %-12s %-28s LCSC %s\n" % (p["ref"], p["value"], FP[p["fp"]]["lib"], p["lcsc"]))
    return sel


# =============================================================================
# Projektdateien
# =============================================================================
def write_project(folder):
    os.makedirs(folder, exist_ok=True)
    d = build_design()
    write_sch(d, os.path.join(folder, PROJECT + ".kicad_sch"))
    write_pcb(d, os.path.join(folder, PROJECT + ".kicad_pcb"))
    write_jlc(d, os.path.join(folder, "jlcpcb"))
    pro = {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2},
            "rules": {"min_clearance": 0.15, "min_track_width": 0.15, "min_via_diameter": 0.5,
                      "min_through_hole_diameter": 0.3, "min_via_annular_width": 0.1,
                      "min_copper_edge_clearance": 0.5, "min_hole_clearance": 0.25,
                      "min_hole_to_hole": 0.25, "min_silk_clearance": 0.0},
            "rule_severities": {"silk_overlap": "ignore", "silk_over_copper": "warning",
                                "lib_footprint_mismatch": "ignore", "lib_footprint_issues": "ignore"},
        }},
        "meta": {"filename": PROJECT + ".kicad_pro", "version": 1},
        "net_settings": {"classes": [
            {"name": "Default", "clearance": 0.2, "track_width": TRACK_DATA,
             "via_diameter": VIA[0], "via_drill": VIA[1], "microvia_diameter": 0.3, "microvia_drill": 0.1,
             "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
             "wire_width": 6, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
             "schematic_color": "rgba(0, 0, 0, 0.000)"},
            {"name": "Power", "clearance": 0.2, "track_width": TRACK_PWR,
             "via_diameter": VIA_PWR[0], "via_drill": VIA_PWR[1], "microvia_diameter": 0.3, "microvia_drill": 0.1,
             "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
             "wire_width": 6, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
             "schematic_color": "rgba(0, 0, 0, 0.000)"}],
            "meta": {"version": 3},
            "netclass_patterns": [{"netclass": "Power", "pattern": "+5V"}, {"netclass": "Power", "pattern": "GND"}]},
        "sheets": [[uid("root-sheet"), ""]],
    }
    with open(os.path.join(folder, PROJECT + ".kicad_pro"), "w") as fh:
        json.dump(pro, fh, indent=2)
    with open(os.path.join(folder, "fp-lib-table"), "w") as fh:
        fh.write('(fp_lib_table\n  (lib (name "LEDMatrix")(type "KiCad")(uri "${KIPRJMOD}/LEDMatrix.pretty")(options "")(descr "Projekt-Footprints"))\n)\n')
    lib = os.path.join(folder, "LEDMatrix.pretty")
    os.makedirs(lib, exist_ok=True)
    for key in ("PAD_SIG", "PAD_PWR", "HOLE", "FID"):
        fp = FP[key]
        name = fp["lib"].split(":")[1]
        o = ['(footprint "%s" (version 20221018) (generator gen_ledmatrix)' % name,
             '  (layer "F.Cu")', '  (descr "%s")' % fp["descr"], '  (tags "%s")' % fp["tags"],
             '  (attr %s)' % fp["attr"],
             '  (fp_text reference "REF**" (at 0 -3) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))',
             '  (fp_text value "%s" (at 0 3) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))' % name]
        if fp["crtyd"]:
            x1, y1, x2, y2 = fp["crtyd"]
            o.append('  (fp_rect (start %s %s) (end %s %s) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))' % (f(x1), f(y1), f(x2), f(y2)))
        for num, typ, shape, (px, py), (w, h), drill, rr in fp["pads"]:
            layers = '"*.Cu" "*.Mask"' if typ == "np_thru_hole" else '"F.Cu" "F.Mask"'
            s = '  (pad "%s" %s %s (at %s %s) (size %s %s)' % (num, typ, shape, f(px), f(py), f(w), f(h))
            if drill:
                s += ' (drill %s)' % f(drill)
            s += ' (layers %s)' % layers
            if rr:
                s += ' (roundrect_rratio %s)' % f(rr)
            if fp.get("pad_extra"):
                s += ' ' + fp["pad_extra"]
            o.append(s + ')')
        o.append(')')
        with open(os.path.join(lib, name + ".kicad_mod"), "w") as fh:
            fh.write("\n".join(o) + "\n")
    return d


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--layers" in args:
        i = args.index("--layers")
        LAYERS = int(args[i + 1])
        del args[i:i + 2]
    if LAYERS not in (2, 4):
        sys.exit("--layers muss 2 oder 4 sein")
    if LAYERS == 2:
        PROJECT += "_2L"
    TITLE = "WS2812B Matrix %dx%d, Raster %.0f mm, %d Lagen" % (COLS, ROWS, PITCH, LAYERS)
    out = args[0] if args else PROJECT
    d = write_project(out)
    print("Fertig: %s  (%d Bauteile, %d Netze)" % (os.path.abspath(out), len(d.parts), len(d.nets) - 1))

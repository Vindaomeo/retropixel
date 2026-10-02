#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lichtgitter- und Rahmen-Generator fuer die WS2812B-Matrix
=========================================================

Erzeugt zwei STL-Dateien zum 3D-Druck, passend zur Platine aus gen_ledmatrix.py:
  1. Lichtgitter (Trennstege, liegt vorne auf der Platine)
  2. Traegerrahmen (hinten, laeuft FRAME_BAND = 10 mm am Rand entlang)
Der Rahmen hat duenne, leicht konische Stifte (kuerzer als die Platine dick ist,
Presspassung erst auf dem letzten Millimeter), die von hinten
in die Bohrungen pcb.FRAME_HOLES und pcb.LOCATE_HOLES gesteckt werden. Damit
sitzt er exakt, befestigt wird er durch Verkleben. Im Lichtgitter gibt es an
diesen Stellen nichts, was in den Pixeln sichtbar waere. Raster, Stegbreite, Matrixgroesse und die
Position der Bohrungen werden direkt aus gen_ledmatrix.py gelesen, damit
Gitter und Platine immer zusammenpassen.

Benutzung (im selben Ordner wie gen_ledmatrix.py):
    pip install manifold3d numpy
    python gen_lightgrid.py                  -> Lichtgitter.stl + Rahmen.stl
    python gen_lightgrid.py --only grid      -> nur Lichtgitter
    python gen_lightgrid.py --only frame     -> nur Traegerrahmen
    python gen_lightgrid.py --test           -> Teststueck 3x3 Zellen (Ecke links oben)
    python gen_lightgrid.py --test 4         -> Teststueck 4x4 Zellen

Koordinaten der STL: mm, Ursprung linke untere Platinenecke, Draufsicht wie
in KiCad. Die STL ist schon in Druckorientierung gedreht:
    FIX = "screw": Auflageseite (zur Platine) unten auf dem Druckbett
    FIX = "peg":   Oberseite unten, Zapfen zeigen nach oben
    Rahmen:        Rueckseite unten, Auflageseite mit den Stiften oben
"""

import os
import struct
import sys

import gen_ledmatrix as pcb

# =============================================================================
# PARAMETER
# =============================================================================
HEIGHT = 10.0               # Gitterhoehe ueber der Platine (mm)
WALL_T = pcb.WALL           # Stegdicke innen = bauteilfreier Streifen der PCB (1,2 mm)
OUTER_T = pcb.WALL          # Dicke des Aussenrahmens (liegt innerhalb der Platine).
                            # Zum lueckenlosen Aneinanderreihen mehrerer Panels:
                            # OUTER_T = pcb.WALL / 2
TOP_CHAMFER = 0.0           # >0: Stege oben auf diese Dicke verjuengt (z.B. 0.6),
                            # schmalere sichtbare Linie zwischen den Pixeln

# Befestigung an den Bohrungen der Platine (pcb.HOLES, Durchmesser pcb.HOLE_DIA)
FIX = "peg"                 # "screw": Schraubdome fuer M2-Schrauben von unten
                            # "peg":   Zapfen, die durch die Bohrungen gesteckt werden
                            # "none":  nichts, Gitter wird nur aufgelegt/geklebt
BOSS_DIA = 4.0              # Durchmesser Schraubdom
PILOT_DIA = 1.6             # Kernloch fuer M2 selbstschneidend (1,6-1,7 mm)
PILOT_TOP = 1.5             # Material ueber dem Kernloch (bleibt oben geschlossen)
PCB_T = 1.6                 # Platinendicke (fuer Zapfenlaenge)
PEG_CLEAR = 0.25            # Spiel Zapfen <-> Bohrung (Durchmesser)
PEG_EXTRA = 0.6             # Zapfen ragt so weit unten aus der Platine (zum Verkleben/Verschmelzen)

# Traegerrahmen (Rueckseite) ---------------------------------------------------
FRAME_W = pcb.FRAME_BAND    # Breite der Auflage auf der Platine (10 mm)
FRAME_DEPTH = 10.0          # Bautiefe hinter der Platine
FRAME_WALL = 2.0            # Aussenwand
PLATE_T = 1.5               # Dicke der Auflageflaeche, darunter 45-Grad-Schraege
LUG_DIA = 5.0               # Verstaerkung um die Stifte (kegelfoermig, stuetzenfrei)
PIN_LEN = 1.4               # Stiftlaenge (< Platinendicke 1,6 mm, vorne nicht sichtbar)
# Stiftform (Durchmesser relativ zur Bohrung, von der Spitze zum Fuss):
#   Spitze: Bohrung - 0,4 (Fase zum Einfaedeln)
#   bis PIN_PRESS_LEN vor dem Fuss: leicht kleiner als die Bohrung (Bohrung - 0,1 ... +-0)
#   letzter Millimeter: konisch auf Bohrung + PIN_PRESS -> Presspassung
PIN_PRESS = 0.10            # Uebermass am Fuss (Durchmesser)
PIN_PRESS_LEN = 1.0         # Laenge der Presszone am Fuss
PIN_TIP_CHAMFER = 0.2       # Laenge der Einfuehrfase an der Spitze
CABLE_SLOTS = []            # optional: [("bottom", 0.5, 20.0, 6.0)] = (Seite, Position 0..1,
                            # Breite, Tiefe ab Rueckseite)

SEGMENTS = 48               # Kreisaufloesung

# =============================================================================
try:
    from manifold3d import Manifold, OpType
    import numpy as np
except ImportError:
    sys.exit("Bitte zuerst installieren:  pip install manifold3d numpy")


def box(x0, y0, x1, y1, z0, z1):
    return Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def tapered_wall(x0, y0, x1, y1, z0, z1, top):
    """Steg, der oben auf 'top' mm Dicke zulaeuft (Huelle aus Boden- und Deckflaeche)."""
    e = 0.01
    if (x1 - x0) < (y1 - y0):              # senkrechter Steg, duenn in x
        cx = (x0 + x1) / 2
        upper = box(cx - top / 2, y0, cx + top / 2, y1, z1 - e, z1)
    else:                                   # waagrechter Steg, duenn in y
        cy = (y0 + y1) / 2
        upper = box(x0, cy - top / 2, x1, cy + top / 2, z1 - e, z1)
    return Manifold.batch_hull([box(x0, y0, x1, y1, z0, z0 + e), upper])


def build(c0=0, r0=0, nc=None, nr=None):
    """Gitter fuer die Zellen [c0, c0+nc) x [r0, r0+nr) (KiCad-Zaehlung, Zeile 0 oben).
    Ohne Angaben: ganzes Gitter. An der Platinenkante gibt es den Aussenrahmen,
    an einer Schnittkante im Inneren einen vollen Steg."""
    P = pcb.PITCH
    nc = pcb.COLS if nc is None else nc
    nr = pcb.ROWS if nr is None else nr
    W, H = pcb.COLS * P, pcb.ROWS * P
    hw = WALL_T / 2.0
    parts = []

    def wall(x0, y0, x1, y1):
        if TOP_CHAMFER and TOP_CHAMFER < min(x1 - x0, y1 - y0):
            parts.append(tapered_wall(x0, y0, x1, y1, 0, HEIGHT, TOP_CHAMFER))
        else:
            parts.append(box(x0, y0, x1, y1, 0, HEIGHT))

    def span(i, n_max):
        """Wandbereich (von, bis) fuer Rasterlinie i."""
        if i == 0:
            return (0.0, OUTER_T)
        if i == n_max:
            return (n_max * P - OUTER_T, n_max * P)
        return (i * P - hw, i * P + hw)

    xa, xb = span(c0, pcb.COLS)[0], span(c0 + nc, pcb.COLS)[1]
    ya, yb = span(r0, pcb.ROWS)[0], span(r0 + nr, pcb.ROWS)[1]
    for i in range(c0, c0 + nc + 1):          # senkrechte Stege
        x0, x1 = span(i, pcb.COLS)
        wall(x0, ya, x1, yb)
    for j in range(r0, r0 + nr + 1):          # waagrechte Stege
        y0, y1 = span(j, pcb.ROWS)
        wall(xa, y0, xb, y1)

    # Befestigung an den Bohrungen (nur die, die im Bereich liegen)
    cut = []
    for i, j in pcb.HOLES:
        if not (c0 < i < c0 + nc and r0 < j < r0 + nr):
            continue
        x, y = i * P, j * P
        if FIX == "screw":
            parts.append(Manifold.cylinder(HEIGHT, BOSS_DIA / 2, BOSS_DIA / 2, SEGMENTS).translate([x, y, 0]))
            cut.append(Manifold.cylinder(HEIGHT - PILOT_TOP + 0.01, PILOT_DIA / 2, PILOT_DIA / 2, SEGMENTS)
                       .translate([x, y, -0.01]))
        elif FIX == "peg":
            r = (pcb.HOLE_DIA - PEG_CLEAR) / 2
            ln = PCB_T + PEG_EXTRA
            parts.append(Manifold.cylinder(ln + 0.01, r, r, SEGMENTS).translate([x, y, -ln]))

    grid = Manifold.batch_boolean(parts, OpType.Add)
    if cut:
        grid = grid - Manifold.batch_boolean(cut, OpType.Add)

    # KiCad-y (nach unten) -> STL-y (nach oben)
    grid = grid.mirror([0, 1, 0]).translate([0, H, 0])
    # Druckorientierung
    if FIX == "peg" and any(c0 < i < c0 + nc and r0 < j < r0 + nr for i, j in pcb.HOLES):
        grid = grid.mirror([0, 0, 1]).translate([0, 0, HEIGHT])   # Oberseite aufs Bett
    return grid


def pin(dia):
    """Stift, z = 0 am Fuss: Presszone (konisch Bohrung+PIN_PRESS -> Bohrung),
    dann leicht untermassig, Spitze mit Fase."""
    zp = min(PIN_PRESS_LEN, PIN_LEN - PIN_TIP_CHAMFER)
    zc = PIN_LEN - PIN_TIP_CHAMFER
    seg = [(0.0, dia + PIN_PRESS), (0.01, dia + PIN_PRESS), (zp + 0.01, dia),
           (zc + 0.01, dia - 0.1), (PIN_LEN + 0.01, dia - 0.4)]
    parts = []
    for (z0, d0), (z1, d1) in zip(seg, seg[1:]):
        parts.append(Manifold.cylinder(z1 - z0, d0 / 2, d1 / 2, SEGMENTS).translate([0, 0, z0]))
    return Manifold.batch_boolean(parts, OpType.Add)


def build_frame():
    """Traegerrahmen fuer die Rueckseite, in Druckorientierung:
    z = 0 Rueckseite (Druckbett), z = FRAME_DEPTH Auflage auf der Platine,
    darueber die Stifte. Draufsicht = Platine von vorne gesehen, d.h. der
    Rahmen wird zum Einbau einfach umgedreht auf die Platinenrueckseite gelegt."""
    P = pcb.PITCH
    W, H = pcb.COLS * P, pcb.ROWS * P
    D = FRAME_DEPTH
    fw, wt, pt = FRAME_W, FRAME_WALL, PLATE_T
    e = 0.01
    z0 = max(0.0, D - pt - (fw - wt))           # Beginn der 45-Grad-Schraege
    add, cut = [], []
    for (ax0, ay0, ax1, ay1, bx0, by0, bx1, by1) in (
            (0, 0, W, fw, 0, 0, W, wt), (0, H - fw, W, H, 0, H - wt, W, H),
            (0, 0, fw, H, 0, 0, wt, H), (W - fw, 0, W, H, W - wt, 0, W, H)):
        add.append(box(bx0, by0, bx1, by1, 0, D))                       # Aussenwand
        add.append(Manifold.batch_hull([box(ax0, ay0, ax1, ay1, D - pt, D),   # Auflage + Schraege
                                        box(bx0, by0, bx1, by1, z0, z0 + e)]))
    # Stifte (mit kegelfoermiger Verstaerkung darunter)
    pins = [(i, j, pcb.HOLE_DIA) for i, j in pcb.FRAME_HOLES] + \
           [(i, j, pcb.LOC_DIA) for i, j in getattr(pcb, "LOCATE_HOLES", [])]
    for i, j, dia in pins:
        x, y = i * P, j * P
        lr = LUG_DIA / 2
        add.append(Manifold.cylinder(lr, 0.01, lr, SEGMENTS).translate([x, y, D - lr]))
        add.append(pin(dia).translate([x, y, D - 0.01]))
    # optionale Kabeldurchlaesse (Aussparung an der Rueckseite der Aussenwand)
    for side, rel, w, dz in CABLE_SLOTS:
        if side in ("top", "bottom"):
            x = rel * W
            y0, y1 = (-1, wt + 1) if side == "top" else (H - wt - 1, H + 1)
            cut.append(box(x - w / 2, y0, x + w / 2, y1, -1, dz))
        else:
            y = rel * H
            x0, x1 = (-1, wt + 1) if side == "left" else (W - wt - 1, W + 1)
            cut.append(box(x0, y - w / 2, x1, y + w / 2, -1, dz))
    frame = Manifold.batch_boolean(add, OpType.Add)
    if cut:
        frame = frame - Manifold.batch_boolean(cut, OpType.Add)
    # KiCad-y (nach unten) -> STL-y (nach oben)
    return frame.mirror([0, 1, 0]).translate([0, H, 0])


def write_stl(m, path):
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32)
    t = np.asarray(mesh.tri_verts)
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    with open(path, "wb") as fh:
        fh.write(b"WS2812B Lichtgitter, gen_lightgrid.py".ljust(80, b" "))
        fh.write(struct.pack("<I", len(t)))
        rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("a", "<f4", 3), ("b", "<f4", 3),
                                      ("c", "<f4", 3), ("attr", "<u2")])
        rec["n"], rec["a"], rec["b"], rec["c"] = n, a, b, c
        fh.write(rec.tobytes())
    return len(t)


def report(g, path, ntri):
    bb = g.bounding_box()
    print("Fertig: %s" % os.path.abspath(path))
    print("  Groesse: %.1f x %.1f x %.1f mm, %d Dreiecke, Volumen %.1f cm3 (ca. %.0f g PLA)"
          % (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2], ntri, g.volume() / 1000, g.volume() / 1000 * 1.24))


if __name__ == "__main__":
    args = sys.argv[1:]
    test, only = None, None
    if "--test" in args:
        i = args.index("--test")
        test = 3
        if i + 1 < len(args) and args[i + 1].isdigit():
            test = int(args[i + 1])
            del args[i + 1]
        del args[i]
    if "--only" in args:
        i = args.index("--only")
        only = args[i + 1]
        del args[i:i + 2]
    if test:
        out = args[0] if args else "%s_Lichtgitter_Test%dx%d.stl" % (pcb.PROJECT, test, test)
        g = build(0, 0, test, test)          # Ecke links oben (bei LED D1)
        report(g, out, write_stl(g, out))
        print("  Teststueck fuer die Ecke links oben (Rahmen links/oben, voller Steg rechts/unten)")
    else:
        if only in (None, "grid"):
            out = "%s_Lichtgitter.stl" % pcb.PROJECT
            g = build()
            report(g, out, write_stl(g, out))
        if only in (None, "frame"):
            out = "%s_Rahmen.stl" % pcb.PROJECT
            g = build_frame()
            report(g, out, write_stl(g, out))

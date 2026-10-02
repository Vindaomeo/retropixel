#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lichtgitter-Generator fuer die WS2812B-Matrix
=============================================

Erzeugt das Trenngitter (Lichtgitter) als STL zum 3D-Druck, passend zur
Platine aus gen_ledmatrix.py. Raster, Stegbreite, Matrixgroesse und die
Position der Bohrungen werden direkt aus gen_ledmatrix.py gelesen, damit
Gitter und Platine immer zusammenpassen.

Benutzung (im selben Ordner wie gen_ledmatrix.py):
    pip install manifold3d numpy
    python gen_lightgrid.py                  -> LED_Matrix_16x16_Lichtgitter.stl
    python gen_lightgrid.py meingitter.stl

Koordinaten der STL: mm, Ursprung linke untere Platinenecke, Draufsicht wie
in KiCad. Die STL ist schon in Druckorientierung gedreht:
    FIX = "screw": Auflageseite (zur Platine) unten auf dem Druckbett
    FIX = "peg":   Oberseite unten, Zapfen zeigen nach oben
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


def build():
    W, H = pcb.COLS * pcb.PITCH, pcb.ROWS * pcb.PITCH
    hw = WALL_T / 2.0
    parts = []

    def wall(x0, y0, x1, y1):
        if TOP_CHAMFER and TOP_CHAMFER < min(x1 - x0, y1 - y0):
            parts.append(tapered_wall(x0, y0, x1, y1, 0, HEIGHT, TOP_CHAMFER))
        else:
            parts.append(box(x0, y0, x1, y1, 0, HEIGHT))

    # Aussenrahmen
    wall(0, 0, W, OUTER_T)
    wall(0, H - OUTER_T, W, H)
    wall(0, 0, OUTER_T, H)
    wall(W - OUTER_T, 0, W, H)
    # Innenstege
    for i in range(1, pcb.COLS):
        x = i * pcb.PITCH
        wall(x - hw, 0, x + hw, H)
    for j in range(1, pcb.ROWS):
        y = j * pcb.PITCH
        wall(0, y - hw, W, y + hw)

    # Befestigung an den Bohrungen (KiCad-y nach unten -> STL-y nach oben)
    holes = [(i * pcb.PITCH, H - j * pcb.PITCH) for i, j in pcb.HOLES]
    cut = []
    for (x, y) in holes:
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

    # Druckorientierung
    if FIX == "peg":
        grid = grid.mirror([0, 0, 1]).translate([0, 0, HEIGHT])   # Oberseite aufs Bett
        grid = grid.mirror([1, 0, 0]).translate([W, 0, 0])         # gespiegelt -> wieder richtig herum
    return grid


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


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "%s_Lichtgitter.stl" % pcb.PROJECT
    g = build()
    ntri = write_stl(g, out)
    bb = g.bounding_box()
    print("Fertig: %s" % os.path.abspath(out))
    print("  Groesse: %.1f x %.1f x %.1f mm, %d Dreiecke, Volumen %.1f cm3 (ca. %.0f g PLA)"
          % (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2], ntri, g.volume() / 1000, g.volume() / 1000 * 1.24))

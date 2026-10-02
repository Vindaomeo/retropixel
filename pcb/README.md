# WS2812B-Matrix 16x16 (10 mm Raster), generiert

## Inhalt

- `gen_ledmatrix.py`: Generator (reines Python 3.8+, keine Abhängigkeiten)
- `LED_Matrix_16x16/`: fertiges KiCad-Projekt (Schaltplan + 4-Lagen-Layout, Zonen gefüllt)

- `LED_Matrix_16x16_2L/`: dieselbe Matrix als 2-Lagen-Platine

## Neu generieren

```
python gen_ledmatrix.py                  (4 Lagen -> LED_Matrix_16x16)
python gen_ledmatrix.py --layers 2       (2 Lagen -> LED_Matrix_16x16_2L)
```

## 2-Lagen-Variante

| Lage | Inhalt |
|------|--------|
| F.Cu | LEDs, Kondensatoren, Datenleitung, +5V-Fläche um alles herum |
| B.Cu | GND-Fläche, Einspeise-Pads, Pegelwandler, Daten-Pads |

- Schaltplan, Platzierung, Stege, Löcher und Datenführung sind identisch mit der 4-Lagen-Version.
- VDD jeder LED hängt direkt an der +5V-Fläche oben. Die Fläche läuft unter jedem LED-Körper durch und ist damit überall zusammenhängend.
- GND-Pads der LEDs und Kondensatoren gehen per Via auf die GND-Fläche unten.
- Die +5V-Einspeise-Pads (unten) haben je 6 Vias zur +5V-Fläche oben.
- Die +5V-Fläche oben ist von den Datenleitungen durchlöchert und leitet schlechter als eine volle Innenlage. Daher: 2 oz Kupfer bestellen, wenn es das Budget erlaubt, und alle 4 Einspeisepaare anschließen.
- DRC: 0 Fehler, 0 ungeroutete Verbindungen. Netzliste Schaltplan ↔ Layout: alle 1557 Pins identisch.

Parameter stehen oben im Script (Rastermaß, Stegbreite, Löcher, Einspeisepunkte, Leiterbahnbreiten).
Achtung: Der Generator überschreibt das Projekt komplett. Manuelle Änderungen in KiCad also erst machen, wenn die Parameter passen.

## Öffnen in KiCad 8

Die Dateien sind im KiCad-7-Format geschrieben. KiCad 8 öffnet sie direkt und speichert sie beim ersten Speichern im 8er-Format.
Danach im PCB-Editor einmal **Zonen füllen (B)** und **DRC** laufen lassen.

## Aufbau

| Lage   | Inhalt |
|--------|--------|
| F.Cu   | 256 × WS2812B (5050), 256 × 100 nF (0402), Datenleitung als Serpentine |
| In1.Cu | GND-Fläche |
| In2.Cu | +5V-Fläche |
| B.Cu   | GND-Fläche, Einspeise-Pads, Pegelwandler, Daten-Pads |

- **Platine:** 160 × 160 mm, Zelle 10 × 10 mm
- **Trennstege:** 1,2 mm breite Streifen auf allen Zellgrenzen sind bauteilfrei (Rule Areas "Steg_*", Footprints verboten, Leiterbahnen erlaubt). Die Stege sind auf Dwgs.User eingezeichnet.
  Lichte Weite pro Zelle: 8,8 mm. LED-Courtyard: 6,9 × 5,5 mm.
- **Löcher:** 9 × Ø2,2 mm (NPTH) an den Steg-Kreuzungen 4/8/12, für M2-Schrauben oder Zapfen des Gitters.
- **Fiducials:** 3 Passermarken auf der Oberseite an den Steg-Kreuzungen (1,1), (15,1) und (1,15), unsymmetrisch angeordnet. 1 mm Kupferpunkt, 2 mm Maskenöffnung, 3 mm kupferfrei. Sie liegen unter dem Trenngitter, das erst nach der Bestückung aufgesetzt wird.
- **Fiducials Unterseite:** 3 weitere an denselben Kreuzungen auf B.Cu (FID4-FID6), damit auch die Unterseite maschinell bestückt werden kann (`JLC_BOTTOM = True`).
- **Datenkette:** D1 links oben, Zeile 1 nach rechts, Zeile 2 nach links usw. (Serpentine). D256 endet links unten.
- **Eingang (Unterseite, unter D1):** Pad DIN → 74AHCT1G125 (3,3 V → 5 V) → 33 Ω → D1. Daneben ein GND-Pad fürs Datenkabel.
- **Ausgang (Unterseite, unter D256):** Pad DOUT + GND, zum Weiterverketten.
- **Einspeisung (Unterseite):** 4 Paare Lötpads (+5V / GND, je 3,6 × 5 mm mit 6 Vias). Bei voller Helligkeit fließen bis ca. 15 A, also möglichst alle 4 Paare anschließen.

## Prüfung

- DRC: 0 Fehler, 0 ungeroutete Verbindungen
- Netzliste Schaltplan ↔ Layout: alle 1557 Pins identisch

## Hinweise für die Fertigung

- Innenlagen mit 1 oz Kupfer bestellen, wenn möglich (bessere Stromverteilung).
- Die Bauteile auf der Unterseite (U1, R1, C257, Pads) lassen sich gut von Hand löten; die Oberseite ist für die Bestückung durch den Fertiger gedacht.
- Software-Mapping: Serpentine (jede zweite Zeile umgekehrt), in FastLED/WLED als "serpentine" einstellbar.
- Einen Elko (z.B. 1000 µF) direkt am Netzteil bzw. an der Einspeisung vorsehen.

## JLCPCB-Fertigung und Bestückung

Jedes Bauteil hat ein Feld **LCSC** mit der JLC/LCSC-Teilenummer, im Schaltplan und im Layout. Damit funktionieren auch die KiCad-Plugins "Fabrication Toolkit" und "JLCPCB Tools", falls du sie später nutzen willst.

| Bauteil | Wert | Footprint | LCSC |
|---|---|---|---|
| D1-D256 | WS2812B-B/W | LED_WS2812B_PLCC4_5.0x5.0mm | C114586 (Alternative C2761795) |
| C1-C257 | 100 nF X7R | 0402 | C1525 (Basic) |
| R1 | 33 Ω | 0402 | C25105 (Basic) |
| U1 | SN74AHCT1G125DBVR | SOT-23-5 | C7484 |

Im Ordner `jlcpcb/` jedes Projekts liegen fertige Upload-Dateien:

- `*_Gerber.zip`: Gerber + Bohrdaten (beim PCB-Bestellen hochladen)
- `*_BOM.csv`: Stückliste (Comment, Designator, Footprint, LCSC Part #)
- `*_CPL.csv`: Bestückungsdaten (Position in mm ab linker unterer Ecke, Drehwinkel schon für JLC korrigiert: WS2812B und SOT-23 +180°)
- `Handbestueckung.txt`: Bauteile, die JLC nicht bestückt

**Standard:** JLC bestückt nur die Oberseite (256 LEDs + 256 Kondensatoren). U1, R1 und C257 auf der Unterseite lötest du selbst, das spart den Aufpreis für beidseitige Bestückung. Mit `JLC_BOTTOM = True` im Script kommen sie mit in BOM/CPL.

**Bestellablauf:** Gerber-Zip hochladen → Lagen/Kupferstärke wählen → "PCB Assembly" aktivieren, Seite "Top" → BOM und CPL hochladen → in der Vorschau die LED-Ausrichtung prüfen (Pin-1-Markierung) → Lagerbestand der Teile prüfen.

Nach Änderungen in KiCad die Gerber neu erzeugen: Datei → Fertigungsunterlagen → Gerber + Bohrdateien, oder mit dem Plugin "Fabrication Toolkit" (erzeugt Gerber, BOM und CPL in einem Schritt aus dem LCSC-Feld).

## Lichtgitter (3D-Druck)

`gen_lightgrid.py` erzeugt das Trenngitter als STL, passend zur Platine. Raster, Stegbreite, Matrixgröße und Bohrungen liest es direkt aus `gen_ledmatrix.py`, beide Dateien müssen also im selben Ordner liegen.

```
pip install manifold3d numpy
python gen_lightgrid.py          -> LED_Matrix_16x16_Lichtgitter.stl
```

- 160 × 160 × 10 mm, Stege 1,2 mm genau auf den bauteilfreien Streifen der Platine, Außenrahmen 1,2 mm innerhalb der Platinenkante
- 9 Schraubdome (Ø 4 mm) über den Bohrungen, Kernloch Ø 1,6 mm für M2-Blechschrauben von unten (z.B. M2 × 8), oben geschlossen
- Engster Abstand Gitter zu einem Bauteil-Courtyard: 0,35 mm (Randspalten), innen 0,95 mm
- Druck: Auflageseite nach unten, ohne Stützen. 0,4-mm-Düse, weißes oder schwarzes PLA (weiß reflektiert mehr Licht, schwarz trennt die Pixel schärfer)

Parameter oben im Script: `HEIGHT` (Höhe), `FIX` ("screw", "peg" = Zapfen durch die Bohrungen, "none"), `OUTER_T` (für lückenlos aneinandergereihte Panels `pcb.WALL / 2`), `TOP_CHAMFER` (Stege oben verjüngt, z.B. 0.6).

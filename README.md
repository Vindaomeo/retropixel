# pixeldisplay

8×8 RGB-Matrix am **Arduino Nano Every**, angesteuert vom PC über USB-CDC mit
einem schlanken Binärprotokoll (Start-Token, Länge, Daten, CRC-16).

Der Nano ist dabei nicht nur dummer Empfänger: nach dem Reset meldet er sich
sekündlich mit einem `READY`-Token, das seine Display-Geometrie mitschickt.
Der PC muss also nichts über die Hardware wissen — er fragt sie.

```
┌──────────────┐   USB-CDC 115200    ┌─────────────────┐   WS2812B
│  PC (Python) │ ◄─────────────────► │  Nano Every     │ ──────────► 8×8 Panel
│  script/     │   MX8-Protokoll     │  FW/ (FastLED)  │   Pin 6
└──────────────┘                     └─────────────────┘
```

---

## Verhalten nach dem Einschalten

| Zustand         | Display                  | Serielle Ausgabe        | Verlassen durch                          |
|-----------------|--------------------------|-------------------------|------------------------------------------|
| **BOOT** (5 s)  | schwarz                  | `READY` jede Sekunde    | gültiger `FRAME` → RUNNING · 5 s → TEST  |
| **TESTPATTERN** | 4 Demos, Wechsel alle 6 s| `READY` jede Sekunde    | gültiger `FRAME` → RUNNING               |
| **RUNNING**     | was der PC schickt       | nur `ACK`/`NACK`        | `RELEASE` oder 5 s ohne Frame → TEST     |

Das heißt konkret: Panel einstecken → 5 s schwarz mit „ich bin ready"-Rufen →
danach läuft die Demo, die Ready-Rufe laufen weiter. Sobald ein Python-Skript
den ersten Frame schickt, übernimmt der PC. Verstummt er, kehrt der Nano nach
5 s von selbst zur Demo zurück — das Display friert also nie ein.

`PING` und `HELLO` lösen bewusst **keinen** Moduswechsel aus: der PC darf
anklopfen, ohne die Demo abzuwürgen.

---

## Aufbau

```
pixeldisplay/
├── FW/                          Arduino-Sketch (Nano Every)
│   ├── matrix8x8_nano_every.ino Hauptprogramm, State-Machine, Testpattern
│   ├── mx8_config.h             ← HIER anpassen: Chipsatz, Pin, Geometrie
│   ├── mx8_protocol.h           Framing-Konstanten + CRC-16
│   └── mx8_rx.h                 RX-State-Machine (hardwarefrei, testbar)
├── script/                      Python-Host
│   ├── hello_rainbow.py         Lauftext in Regenbogenfarben
│   ├── speedtest.py             Latenz-, Durchsatz- und Flood-Messung
│   ├── mx8_cli.py               Kommandozeile (ports/monitor/demo/image/preview)
│   ├── mx8/protocol.py          Framing/CRC — Spiegel von mx8_protocol.h
│   ├── mx8/link.py              serielle Verbindung, READY-Handshake, ACK-Flow
│   ├── mx8/framebuffer.py       8×8-RGB-Puffer + ANSI-Terminalvorschau
│   ├── mx8/font5x7.py           5×7-Font als ASCII-Art, ASCII + Umlaute
│   └── mx8/patterns.py          Host-Animationen
├── tests/                       Kreuztest C ↔ Python, ohne Hardware
├── docs/wiring.html             Verdrahtungsplan als Grafik (im Browser öffnen)
├── PROTOCOL.md                  vollständige Protokollspezifikation
├── WIRING.md                    Verdrahtung, Strombudget, Stolperfallen
└── README.md
```

---

## Hardware

| | |
|---|---|
| Board | Arduino Nano Every (ATmega4809) |
| Panel | 8×8 WS2812B / NeoPixel-kompatibel (64 LEDs) |
| Daten | **D6** → DIN des Panels, möglichst mit 330 Ω in Serie |
| Masse | GND Nano **und** GND Netzteil **und** GND Panel zusammen |
| 5 V | siehe Warnung unten |

**→ Vollständiger Verdrahtungsplan mit Strombudget: [WIRING.md](WIRING.md)**
(grafisch: [`docs/wiring.html`](docs/wiring.html) im Browser öffnen)

> **Stromversorgung.** 64 WS2812B ziehen bei vollem Weiß rund **3,8 A**. Der
> 5V-Pin des Nano Every kann das nicht — er hängt am USB und ist bei ~500 mA
> am Ende. Zwei saubere Optionen:
>
> 1. **Externes 5V-Netzteil** ans Panel, GND mit dem Nano verbinden. Ein
>    1000 µF-Elko über die Panel-Versorgung dämpft den Einschaltstrom.
> 2. **Helligkeit deckeln** (Auslieferungszustand): `MX_BRIGHTNESS_DEFAULT 40`
>    und `MX_MAX_MILLIAMPS 450` lassen FastLED automatisch so weit
>    runterregeln, dass USB-Speisung reicht.
>
> Der Auslieferungszustand ist bewusst Option 2 — es soll auf Anhieb
> funktionieren, ohne dass du dir den USB-Port grillst.

---

## Firmware flashen

Benötigt in der Arduino IDE:

* Boardpaket **„Arduino megaAVR Boards"** (Werkzeuge → Board → Boardverwalter)
* Bibliothek **FastLED ≥ 3.6** — ältere Versionen kompilieren für den
  ATmega4809 nicht. Getestet gegen 3.9.x.

Dann: Ordner `FW/` öffnen, Board *Arduino Nano Every* wählen, Registers
*ATMEGA4809* (Standard), hochladen.

### Anderes Panel / anderer LED-Typ

Alles Nötige steht in `FW/mx8_config.h`:

```c
#define LED_CHIPSET      WS2812B   // SK6812, WS2811, APA102, WS2801, LPD8806 …
#define LED_COLOR_ORDER  GRB       // RGB, BGR, … je nach Panel
#define LED_DATA_PIN     6
// #define LED_CLOCK_PIN 7         // nur für getaktete Chips (APA102 & Co.)

#define MX_WIDTH         8
#define MX_HEIGHT        8
#define MX_SERPENTINE    0         // 1 = Zeilen abwechselnd verdrahtet
#define MX_ORIGIN        0         // 0=oben-links 1=oben-rechts 2=u-l 3=u-r
```

Der Host muss dabei **nichts** ändern: Geometrie und Layout kommen über das
`READY`-Token mit, und das Wire-Format ist immer plain RGB, row-major,
oben-links zuerst. Umsortieren und Panel-Mapping macht die Firmware.

**Typische Fehlerbilder:**

| Symptom | Ursache |
|---|---|
| Rot und Grün vertauscht | `LED_COLOR_ORDER` — `GRB` ↔ `RGB` tauschen |
| Jede zweite Zeile spiegelverkehrt | `MX_SERPENTINE` auf `1` |
| Bild steht auf dem Kopf / seitenverkehrt | `MX_ORIGIN` durchprobieren |
| Erste LED flackert, Rest ok | Pegelproblem: 330 Ω in die Datenleitung, kürzeres Kabel |

---

## Python-Seite

```bash
cd script
pip install pyserial          # Pflicht
pip install pillow            # optional, nur für den Bild-/GIF-Modus
```

### Die zwei Testprogramme

```bash
python hello_rainbow.py                    # "Hello World" als Lauftext, Regenbogen
python hello_rainbow.py --preview          # dasselbe im Terminal, ohne Hardware
python hello_rainbow.py --text "Grüße!" --speed 12 --loops 3

python speedtest.py                        # Latenz + Durchsatz messen
python speedtest.py --flood                # zusätzlich der Stresstest ohne ACK
python speedtest.py --preview              # nur die Baudraten-Rechnung
```

**`hello_rainbow.py`** scrollt Text durch die Matrix. Der Farbton hängt an der
*Textspalte*, nicht an der Displayspalte — der Regenbogen wandert also mit den
Buchstaben mit, statt dass die Buchstaben durch einen stehenden Verlauf laufen.
Standardmäßig wird zwischen zwei Textspalten übergeblendet (`--hard` schaltet
das ab): auf 8×8 gleitet der Text dadurch sichtbar flüssiger, statt in
Pixelschritten zu springen. Der 5×7-Font in `mx8/font5x7.py` deckt ASCII plus
Umlaute und ß ab und steht als lesbare ASCII-Art im Quelltext.

**`speedtest.py`** misst in drei Stufen: Latenz (PING/ACK), Durchsatz
(Vollbilder mit Quittung) und optional Flood (ohne Quittung). Jeder Messwert
wird gegen das theoretische Baudraten-Limit gestellt, sodass man sieht, ob die
Leitung bremst oder die USB-Latenz. Der Flood-Modus zeigt vor allem, *warum*
die ACK-Flusskontrolle da ist — ohne sie gehen Frames verloren, sobald FastLED
in `show()` die Interrupts sperrt.

### Weitere Kommandos

```bash
python mx8_cli.py ports                              # welche Ports gibt es?
python mx8_cli.py monitor                            # nur zuhören, Demo läuft weiter
python mx8_cli.py demo --pattern plasma --fps 30     # Animation aufs Panel
python mx8_cli.py demo --pattern fire --seconds 20 --release
python mx8_cli.py image bild.png --seconds 10        # Bild oder animiertes GIF
python mx8_cli.py preview --pattern spiral           # ohne Hardware, im Terminal
```

Patterns: `rainbow` `plasma` `comet` `spiral` `fire` `checker` `bounce`

`--release` schickt am Ende `RELEASE`, der Nano zeigt dann wieder sein eigenes
Testpattern statt eines schwarzen Displays.

### Als Bibliothek

```python
from mx8 import MatrixLink, Framebuffer

with MatrixLink() as link:              # Port wird automatisch gesucht
    geo = link.wait_ready()             # blockt bis zum READY-Token
    print(geo)                          # 8x8x3 (GRB, progressiv) proto v1 fw 1.0 …

    link.set_brightness(60)
    fb = Framebuffer(geo.width, geo.height)

    for x in range(8):
        fb.set(x, x, 255, 0, 0)         # rote Diagonale
    link.send_frame(fb.to_bytes())      # wartet auf das ACK
```

`send_frame()` kehrt erst zurück, wenn der Nano quittiert hat. Das ist Absicht
und die eigentliche Flusskontrolle: FastLED sperrt während `show()` die
Interrupts (~1,9 ms bei 64 LEDs), ungebremstes Senden würde RX-Bytes verlieren.

Die Obergrenze setzt dabei die Baudrate: 199 B Frame + 8 B ACK sind bei 115200
Baud **17,97 ms**, also **55,7 fps** theoretisch. Praktisch landet man wegen
USB- und OS-Latenz bei 35–50 fps — für 8×8 mehr als genug. Wer mehr will, setzt
`MX_SERIAL_BAUD` auf 250000 (→ 120 fps theoretisch); `speedtest.py` zeigt, ob
sich das bei deinem Aufbau überhaupt lohnt.

---

## Protokoll in Kurzform

```
A5 5A | CMD | LEN(16 LE) | PAYLOAD | CRC16(16 LE)
```

CRC-16/CCITT-FALSE (Poly `0x1021`, Init `0xFFFF`) über `CMD ‖ LEN ‖ PAYLOAD` —
das Start-Token zählt nicht mit. Ein Vollbild ist `7 + 192 = 199` Bytes.

| PC → Nano | | Nano → PC | |
|---|---|---|---|
| `0x01 FRAME` | 192 B RGB | `0x81 READY` | 8 B Geometrie |
| `0x02 PING` | – | `0x82 ACK` | quittierter CMD |
| `0x03 SET_BRIGHTNESS` | 1 B | `0x83 NACK` | CMD + Fehlercode |
| `0x04 CLEAR` | – | `0x84 LOG` | ASCII-Text |
| `0x05 HELLO` | – | | |
| `0x06 RELEASE` | – | | |

Details, Geometrie-Descriptor, Fehlercodes und Resync-Regeln: **[PROTOCOL.md](PROTOCOL.md)**

---

## Tests

```bash
python tests/test_protocol.py
```

Braucht **keine Hardware**. Der Test kompiliert `FW/mx8_protocol.h` und
`FW/mx8_rx.h` unverändert mit dem PC-Compiler und lässt exakt dieselbe
Parser-Logik laufen, die auch auf dem Nano läuft — es wird also nicht gegen
eine Nachbildung getestet, sondern gegen den echten Firmware-Code. Geprüft
wird unter anderem:

* CRC gegen den kanonischen Check-Wert `0x29B1` für `"123456789"`
* CRC und Frame-Encoder C ↔ Python über 500 Zufallsvektoren
* Resync über Müll zwischen den Frames, beide Seiten identisch
* Start-Token `A5 5A` **im Payload** bringt den Parser nicht aus dem Tritt
* alle 591 Einzelbitfehler in einem 199-B-Frame: keiner wird akzeptiert
* Überlange Payload → `NACK ERR_TOO_LONG`, kein Pufferüberlauf
* abgeschnittener Frame → `NACK ERR_RX_TIMEOUT` und sauberer Resync

Erwartete Ausgabe: `24/24 Checks bestanden.`

---

## Fehlersuche

| Problem | Ansatz |
|---|---|
| `Kein Arduino-Port gefunden` | `python mx8_cli.py ports`, Port mit `-p COM5` erzwingen |
| Kein `READY` trotz Verbindung | Serieller Monitor der IDE offen? Nur ein Programm pro Port. |
| `TimeoutError` beim ersten Frame | Nano resettet beim Portöffnen; `MatrixLink(reset_wait=3.0)` |
| Viele `NACK CRC-Fehler` | Baudrate auf beiden Seiten gleich? Kabel/Hub tauschen. |
| Demo läuft, aber Frames kommen nicht an | `monitor` mitlaufen lassen — `LOG`-Zeilen zeigen die Zustandswechsel |

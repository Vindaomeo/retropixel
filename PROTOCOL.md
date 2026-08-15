# MX8 — Binärprotokoll (PC ⇄ Arduino Nano Every)

Version 1. Transport: USB-CDC (Virtual COM Port), **115200 8N1**, keine Flusskontrolle.
Byte-Reihenfolge für Mehrbyte-Felder: **Little Endian**.

## 1. Frame-Format (identisch in beide Richtungen)

```
+--------+--------+--------+--------+--------+---------//---------+--------+--------+
| SYNC0  | SYNC1  |  CMD   | LEN_LO | LEN_HI |      PAYLOAD       | CRC_LO | CRC_HI |
| 0xA5   | 0x5A   | 1 Byte |     uint16 LE   |      LEN Bytes     |    uint16 LE    |
+--------+--------+--------+--------+--------+---------//---------+--------+--------+
```

| Feld    | Größe | Bedeutung                                                        |
|---------|-------|------------------------------------------------------------------|
| SYNC    | 2     | Start-Token `0xA5 0x5A`. Resync-Punkt nach Fehlern.               |
| CMD     | 1     | Kommando-Code (siehe §2). Bit 7 = 1 ⇒ Nachricht kommt vom Nano.   |
| LEN     | 2     | Länge des Payload in Bytes, `0 … 512`.                            |
| PAYLOAD | LEN   | Nutzdaten.                                                        |
| CRC     | 2     | CRC-16/CCITT-FALSE über **CMD ‖ LEN_LO ‖ LEN_HI ‖ PAYLOAD**.      |

**CRC-16/CCITT-FALSE:** Polynom `0x1021`, Init `0xFFFF`, kein Reflect, kein Final-XOR.
Das SYNC-Token geht **nicht** in die CRC ein.

Overhead: 7 Bytes. Ein Vollbild-Frame ist damit `7 + 192 = 199` Bytes.

## 2. Kommando-Codes

### PC → Nano (Bit 7 = 0)

| Code | Name             | Payload                                | Antwort        |
|------|------------------|----------------------------------------|----------------|
| 0x01 | `FRAME`          | 192 B = 64 Pixel × (R,G,B)             | `ACK` / `NACK` |
| 0x02 | `PING`           | –                                      | `ACK`          |
| 0x03 | `SET_BRIGHTNESS` | 1 B, 0…255 (globale Helligkeit)        | `ACK`          |
| 0x04 | `CLEAR`          | –                                      | `ACK`          |
| 0x05 | `HELLO`          | – (fordert sofortiges `READY` an)      | `READY`        |
| 0x06 | `RELEASE`        | – (PC gibt Display frei → Testpattern) | `ACK`          |

### Nano → PC (Bit 7 = 1)

| Code | Name    | Payload                                                     |
|------|---------|-------------------------------------------------------------|
| 0x81 | `READY` | 8 B Geometrie-Descriptor (siehe §3)                          |
| 0x82 | `ACK`   | 1 B: der quittierte CMD-Code                                 |
| 0x83 | `NACK`  | 2 B: quittierter CMD-Code (0xFF = unbekannt), Fehlercode §4  |
| 0x84 | `LOG`   | n B ASCII-Text (Debug, darf ignoriert werden)                |

### 3. `READY` Geometrie-Descriptor (8 Byte)

| Offset | Typ  | Feld       | Wert im Auslieferungszustand              |
|--------|------|------------|-------------------------------------------|
| 0      | u8   | `PROTO`    | `0x01` — Protokollversion                  |
| 1      | u8   | `WIDTH`    | `8`                                        |
| 2      | u8   | `HEIGHT`   | `8`                                        |
| 3      | u8   | `BPP`      | `3` — Bytes pro Pixel                      |
| 4      | u8   | `ORDER`    | 0=RGB 1=RBG 2=GRB 3=GBR 4=BRG 5=BGR (info) |
| 5      | u8   | `LAYOUT`   | `0` = row-major progressiv, 1 = serpentine |
| 6      | u8   | `FW_MAJOR` | `1`                                        |
| 7      | u8   | `FW_MINOR` | `0`                                        |

**Das Wire-Format ist immer plain RGB**, row-major, Zeile 0 = oben, Spalte 0 = links.
`ORDER` meldet nur, welche physische Kanalreihenfolge das angeschlossene Panel hat
(WS2812B = GRB, APA102 = BGR …) — das Umsortieren erledigt FastLED in der Firmware.
Ebenso mappt die Firmware ein evtl. serpentines Panel-Layout selbst; `LAYOUT` ist
rein informativ, z. B. damit ein Host-Tool das im UI anzeigen kann.

Pixel-Index `i = y * WIDTH + x`, Payload-Offset `i * 3`.

## 4. Fehlercodes (`NACK`)

| Code | Bedeutung                                        |
|------|--------------------------------------------------|
| 0x01 | CRC-Fehler                                       |
| 0x02 | Payload-Länge passt nicht zum Kommando           |
| 0x03 | Unbekanntes Kommando                             |
| 0x04 | Frame zu lang (> `MAX_PAYLOAD`), verworfen       |
| 0x05 | Timeout während Frame-Empfang (Resync erzwungen) |

## 5. Zustandsautomat der Firmware

```
        Reset
          |
          v
   +--------------+   gültiges FRAME/CLEAR      +-----------+
   |    BOOT      | --------------------------> |  RUNNING  |
   | 5 s Fenster  |                             | PC steuert|
   | READY @1 Hz  |                             | kein READY|
   +--------------+                             +-----------+
          |                                        |     ^
          | 5 s ohne Daten                RELEASE  |     | gültiges FRAME
          v                        oder 5 s ohne   |     |
   +--------------+                Frame (Timeout) |     |
   | TESTPATTERN  | <------------------------------+     |
   | Demo läuft   | -------------------------------------+
   | READY @1 Hz  |
   +--------------+
```

* **BOOT** — nach dem Reset 5 s lang: Display schwarz, jede Sekunde ein `READY`.
* **TESTPATTERN** — Demo-Animationen laufen, `READY` weiterhin jede Sekunde.
  Der Nano bleibt jederzeit empfangsbereit.
* **RUNNING** — erster gültiger `FRAME` beendet Testpattern **und** den
  `READY`-Broadcast. Kommt 5 s lang kein `FRAME` mehr (`PC_TIMEOUT_MS`) oder
  sendet der PC `RELEASE`, fällt der Nano nach **TESTPATTERN** zurück.

`PING` und `HELLO` lösen **keinen** Wechsel nach RUNNING aus — der PC kann also
gefahrlos anklopfen, ohne die Demo abzuwürgen.

## 6. Flusskontrolle

FastLED sperrt während `show()` die Interrupts (~1,9 ms bei 64 LEDs), währenddessen
können RX-Bytes verloren gehen. Deshalb gilt: **der PC sendet den nächsten `FRAME`
erst, nachdem der `ACK` des vorigen eingetroffen ist** (oder nach 200 ms Timeout).
Das begrenzt die Framerate sauber auf das, was der Nano wirklich schafft.

Die Obergrenze folgt direkt aus der Baudrate. Ein Vollbild sind 199 B auf der
Leitung, die ACK-Antwort 8 B, bei 8N1 also `(199 + 8) × 10 = 2070` Bit:

| Baudrate | Zeit je Frame | max. fps (mit ACK) |
|---------:|--------------:|-------------------:|
| 115200   | 17,97 ms      | **55,7**           |
| 250000   |  8,28 ms      | 120,8              |
| 500000   |  4,14 ms      | 241,5              |

Praktisch liegt man wegen USB-CDC- und OS-Latenz darunter — bei 115200 typisch
bei 35–50 fps. `script/speedtest.py` misst das nach und stellt den Messwert
neben das theoretische Limit, damit sichtbar wird, ob die Leitung oder die
Latenz bremst.

## 7. Resync-Regeln

Der Empfänger sucht byteweise nach `0xA5 0x5A`. Bricht ein Frame ab (kein
vollständiger Empfang innerhalb von `RX_FRAME_TIMEOUT_MS` = 250 ms), wird der
Parser zurückgesetzt und ein `NACK 0x05` gesendet. Ein `0xA5 0x5A` im Payload
ist unkritisch, weil die Länge vorab bekannt ist.

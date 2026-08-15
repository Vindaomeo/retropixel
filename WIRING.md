# Verdrahtung — 8×8 WS2812B am Arduino Nano Every

> **Kurze Antwort auf die Kernfrage:** Nein, der Nano kann 64 RGB-LEDs **nicht**
> über den 5V-Pin am USB versorgen — jedenfalls nicht bei voller Helligkeit.
> 64 WS2812B ziehen bei Vollweiß rund **3,8 A**, ein USB-2.0-Port liefert 500 mA.
> Das ist Faktor 8 zu wenig. Für **gedimmten** Betrieb reicht USB dagegen gut aus.

Eine grafische Fassung mit beiden Verdrahtungsvarianten liegt in
[`docs/wiring.html`](docs/wiring.html) — einfach im Browser öffnen.

---

## 1. Strombudget

### Was das Panel zieht

| Zustand | pro LED | 64 LEDs |
|---|---:|---:|
| Alles aus (Ruhestrom der Controller-ICs) | ≈ 1 mA | 64 mA |
| Eine Farbe voll (z. B. reines Rot) | ≈ 20 mA | 1,28 A |
| Vollweiß — Datenblatt, 3 × 20 mA | 60 mA | **3,84 A** |
| Vollweiß — gemessen, „Typ B" (PJRC) | 52,5 mA | 3,36 A |
| Vollweiß — gemessen, „Typ A" (PJRC) | 33,5 mA | 2,14 A |

Die Streuung ist real: PJRC hat zwei Chargen vermessen, beide als „WS2812B"
verkauft, und kam auf 33,5 mA bzw. 52,5 mA pro LED. Rechne mit dem
Datenblattwert von 60 mA, dann bist du auf der sicheren Seite.

### Was USB liefert

| Quelle | Budget | für LEDs¹ | Vollweiß möglich | max. Helligkeit² |
|---|---:|---:|---:|---:|
| USB 2.0 Port | 500 mA | 470 mA | 7 von 64 | 31/255 |
| USB 3.0 Port | 900 mA | 870 mA | 14 von 64 | 58/255 |
| Netzteil 5 V / 4 A | 4000 mA | 4000 mA | alle 64 | 255/255 |

¹ Budget minus ≈ 30 mA für den Nano Every selbst.
² Helligkeit, bei der selbst ein komplett weißes Bild noch im Budget bleibt.

---

## 2. Variante 1 — nur USB

Für den Schreibtisch, zum Entwickeln, für gedimmte Anzeigen.

```
   PC                Nano Every                    8×8 WS2812B
 ┌──────┐          ┌─────────────┐               ┌──────────────┐
 │      │   USB    │             │  D6   ┌───┐   │ DIN          │
 │      ├──────────┤             ├───────┤330├───┤              │
 │      │          │             │       └───┘   │              │
 └──────┘          │             │  5V           │ +5V          │
                   │             ├───────────────┤              │
                   │             │  GND          │ GND          │
                   │             ├───────────────┤              │
                   └─────────────┘               └──────────────┘
```

> ⚠ Nur mit gedeckelter Helligkeit betreiben:
> `MX_BRIGHTNESS_DEFAULT ≤ 30` und `MX_MAX_MILLIAMPS 450` müssen gesetzt bleiben.

---

## 3. Variante 2 — externes 5-V-Netzteil (empfohlen)

Volle Helligkeit, stabile Farben, kein Risiko für den USB-Port des Rechners.
Der Nano bleibt am USB — er braucht es ohnehin für die Daten.

```
   PC                Nano Every                    8×8 WS2812B
 ┌──────┐          ┌─────────────┐               ┌──────────────┐
 │      │   USB    │             │  D6   ┌───┐   │ DIN          │
 │      ├──────────┤             ├───────┤330├───┤              │
 └──────┘          │             │       └───┘   │              │
                   │             │  5V  ✗ frei   │ +5V          │
                   │             │        ┌──────┤              │
                   │             │  GND   │      │ GND          │
                   │             ├────┐   │  ┌───┤              │
                   └─────────────┘    │   │  │   └──────────────┘
                                      │   │  │
 ┌──────────────────┐                 │   │  │
 │ Netzteil 5 V DC  │  +5V            │  ═╪═ 1000 µF
 │    ≥ 4 A         ├─────────────────┼───┘  │
 │                  │  GND            │      │
 │                  ├─────────────────┴──────┘
 └──────────────────┘
```

**Der 5V-Pin des Nano bleibt frei.** Er wird bei dieser Variante *nicht* mit dem
Panel verbunden — sonst speist das Netzteil über den Nano zurück in den USB-Port
des Rechners. Im Nano Every sitzt zwar eine Diode in diesem Pfad, aber darauf
sollte man sich nicht verlassen.

**Warum die Masse zusammen muss:** das Datensignal ist eine Spannung *gegenüber
GND*. Ohne gemeinsamen Bezugspunkt sieht das Panel kein sauberes High/Low.
Typisches Symptom sind wild flackernde oder gar keine LEDs, obwohl alles richtig
verkabelt aussieht — das ist der mit Abstand häufigste Fehler bei diesem Aufbau.

---

## 4. Stückliste

| Teil | Wert | Wozu |
|---|---|---|
| Widerstand | 330 Ω (220–470 Ω) | dämpft Reflexionen auf der Datenleitung, schützt den ersten LED-Eingang. Möglichst nah ans Panel setzen. |
| Elko | 1000 µF, ≥ 10 V | puffert Stromspitzen beim Farbwechsel, direkt an die Panel-Anschlüsse |
| Netzteil | 5 V DC, ≥ 4 A | nur bei Variante 2. 4 A deckt Vollweiß mit Reserve. |
| Litze | ≥ 0,5 mm² für 5 V/GND | bei 3,8 A fällt an dünnem Draht spürbar Spannung ab |

---

## 5. Kein Levelshifter nötig

Der Nano Every arbeitet mit **5 V Logik**. WS2812B brauchen für ein sauberes High
mindestens 0,7 × V_DD ≈ 3,5 V — mit 5 V vom Pin passt das mit Reserve.

Das ist ein echter Vorteil gegenüber den 3,3-V-Boards der Nano-33-Familie, wo man
einen Pegelwandler braucht oder die erste LED als Opfer-Pixel missbraucht.

---

## 6. Sechs Dinge, die schiefgehen

1. **Panel-5V an den 5V-Pin des Nano, obwohl ein Netzteil dranhängt.** Rückspeisung
   in den USB-Port. Bei Variante 2 bleibt der 5V-Pin frei.
2. **Masse vergessen.** Siehe oben. Immer alle drei GND verbinden.
3. **Helligkeit hochdrehen bei USB-Betrieb.** Der Port geht in die Strombegrenzung,
   die Spannung bricht ein, der Nano resettet mitten im Frame. Sieht aus wie ein
   Software-Bug, ist aber Elektrik.
4. **Spannungsabfall am 5V-Pin.** Bei USB-Speisung liegen am 5V-Pin oft nur ≈ 4,7 V
   an. WS2812B laufen damit, aber Weiß wird leicht gelbstichig.
5. **Am falschen Ende eingespeist.** Die Datenleitung gehört an `DIN`. An `DOUT`
   passiert schlicht nichts.
6. **Netzteil erst nach dem USB anstecken.** Besser umgekehrt oder gleichzeitig;
   der Elko dämpft den Einschaltstoß.

---

## 7. Passende Firmware-Einstellung

In [`FW/mx8_config.h`](FW/mx8_config.h):

| Aufbau | `MX_BRIGHTNESS_DEFAULT` | `MX_MAX_MILLIAMPS` |
|---|---:|---:|
| USB 2.0, ohne Netzteil | 30 | 450 |
| USB 3.0, ohne Netzteil | 58 | 850 |
| Netzteil 5 V / 4 A | 255 | 3800 |
| Netzteil, aber Panel soll leise bleiben | 128 | 2000 |

`MX_MAX_MILLIAMPS` ist das härtere der beiden Limits: FastLED rechnet vor jedem
`show()` den Strombedarf des Bildes aus und regelt die Helligkeit automatisch
herunter, wenn es drüber wäre. Deshalb ist die Kombination „Helligkeit 40 +
Limit 450 mA" im Auslieferungszustand auch dann sicher, wenn ein Bild komplett
weiß ist.

---

## Quellen

- Strommessungen je LED: [PJRC — How Much Current Do WS2812 / NeoPixel LEDs Really Use?](https://www.pjrc.com/how-much-current-do-ws2812-neopixel-leds-really-use/)
- Nano Every Spannungsversorgung: [Arduino Nano Every Datenblatt ABX00028](https://docs.arduino.cc/resources/datasheets/ABX00028-datasheet.pdf)
- 5V-Pin / Rückspeisung: [Arduino Forum — Nano Every power considerations](https://forum.arduino.cc/t/arduino-nano-every-power-considerations/620470),
  [Nano Every Power Output 5V pin](https://forum.arduino.cc/t/arduino-nano-every-power-output-5v-pin/935861)

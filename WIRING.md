# Wiring — WS2812B panels

**§1–§7 describe the 8×8 build on the Arduino Nano Every.
§8 (below) describes the 16×32 expansion on the Pico.**

> **Short answer to the key question:** No, the Nano **cannot** power 64 RGB LEDs
> from the 5V pin on USB — at least not at full brightness.
> At full white, 64 WS2812B draw about **3.8 A**; a USB 2.0 port delivers 500 mA.
> That is a factor of 8 short. For **dimmed** operation, on the other hand, USB is plenty.

A graphical version with both wiring variants lives in
[`docs/wiring.html`](docs/wiring.html) — just open it in a browser.

---

## 1. Power budget

### What the panel draws

| State | per LED | 64 LEDs |
|---|---:|---:|
| Everything off (quiescent current of the controller ICs) | ≈ 1 mA | 64 mA |
| One color at full (e.g. pure red) | ≈ 20 mA | 1.28 A |
| Full white — datasheet, 3 × 20 mA | 60 mA | **3.84 A** |
| Full white — measured, "type B" (PJRC) | 52.5 mA | 3.36 A |
| Full white — measured, "type A" (PJRC) | 33.5 mA | 2.14 A |

The spread is real: PJRC measured two batches, both sold as "WS2812B",
and got 33.5 mA and 52.5 mA per LED respectively. Plan with the
datasheet value of 60 mA and you are on the safe side.

### What USB delivers

| Source | Budget | for LEDs¹ | Full white possible | max. brightness² |
|---|---:|---:|---:|---:|
| USB 2.0 port | 500 mA | 470 mA | 7 of 64 | 31/255 |
| USB 3.0 port | 900 mA | 870 mA | 14 of 64 | 58/255 |
| Power supply 5 V / 4 A | 4000 mA | 4000 mA | all 64 | 255/255 |

¹ Budget minus ≈ 30 mA for the Nano Every itself.
² Brightness at which even an all-white frame still stays within budget.

---

## 2. Variant 1 — USB only

For the desk, for development, for dimmed displays.

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

> ⚠ Only run with capped brightness:
> `MX_BRIGHTNESS_DEFAULT ≤ 30` and `MX_MAX_MILLIAMPS 450` must stay set.

---

## 3. Variant 2 — external 5 V power supply (recommended)

Full brightness, stable colors, no risk to the computer's USB port.
The Nano stays on USB — it needs it for the data anyway.

```
   PC                Nano Every                    8×8 WS2812B
 ┌──────┐          ┌─────────────┐               ┌──────────────┐
 │      │   USB    │             │  D6   ┌───┐   │ DIN          │
 │      ├──────────┤             ├───────┤330├───┤              │
 └──────┘          │             │       └───┘   │              │
                   │             │  5V  ✗ free   │ +5V          │
                   │             │        ┌──────┤              │
                   │             │  GND   │      │ GND          │
                   │             ├────┐   │  ┌───┤              │
                   └─────────────┘    │   │  │   └──────────────┘
                                      │   │  │
 ┌──────────────────┐                 │   │  │
 │ PSU 5 V DC       │  +5V            │  ═╪═ 1000 µF
 │    ≥ 4 A         ├─────────────────┼───┘  │
 │                  │  GND            │      │
 │                  ├─────────────────┴──────┘
 └──────────────────┘
```

**The Nano's 5V pin stays unconnected.** In this variant it is *not* connected to the
panel — otherwise the power supply feeds back through the Nano into the computer's
USB port. The Nano Every does have a diode in that path, but you
shouldn't rely on it.

**Why the grounds must be tied together:** the data signal is a voltage *relative
to GND*. Without a common reference point the panel sees no clean high/low.
The typical symptom is wildly flickering LEDs or none at all, even though everything
looks correctly wired — this is by far the most common mistake with this build.

---

## 4. Bill of materials

| Part | Value | Purpose |
|---|---|---|
| Resistor | 330 Ω (220–470 Ω) | damps reflections on the data line, protects the first LED input. Place it as close to the panel as possible. |
| Electrolytic cap | 1000 µF, ≥ 10 V | buffers current spikes on color changes, directly at the panel terminals |
| Power supply | 5 V DC, ≥ 4 A | variant 2 only. 4 A covers full white with headroom. |
| Stranded wire | ≥ 0.5 mm² for 5 V/GND | at 3.8 A a thin wire drops a noticeable voltage |

---

## 5. No level shifter needed

The Nano Every runs on **5 V logic**. For a clean high, WS2812B need
at least 0.7 × V_DD ≈ 3.5 V — with 5 V from the pin that fits with headroom.

That is a real advantage over the 3.3 V boards of the Nano 33 family, where you
need a level shifter or abuse the first LED as a sacrificial pixel.

---

## 6. Six things that go wrong

1. **Panel 5V to the Nano's 5V pin even though a power supply is attached.** Back-feeding
   into the USB port. In variant 2 the 5V pin stays unconnected.
2. **Forgetting ground.** See above. Always connect all three GNDs.
3. **Cranking up brightness on USB power.** The port goes into current limiting,
   the voltage collapses, the Nano resets in the middle of a frame. Looks like a
   software bug, but it's electrical.
4. **Voltage drop at the 5V pin.** On USB power the 5V pin often only sees ≈ 4.7 V.
   WS2812B run on that, but white gets a slight yellow tint.
5. **Feeding in at the wrong end.** The data line belongs on `DIN`. On `DOUT`
   simply nothing happens.
6. **Plugging in the power supply only after USB.** Better the other way round or at the same time;
   the electrolytic cap damps the inrush.

---

## 7. Matching firmware settings

In [`FW/mx8_config.h`](FW/mx8_config.h):

| Setup | `MX_BRIGHTNESS_DEFAULT` | `MX_MAX_MILLIAMPS` |
|---|---:|---:|
| USB 2.0, no power supply | 30 | 450 |
| USB 3.0, no power supply | 58 | 850 |
| Power supply 5 V / 4 A | 255 | 3800 |
| Power supply, but panel should stay subdued | 128 | 2000 |

`MX_MAX_MILLIAMPS` is the harder of the two limits: before every
`show()` FastLED computes the frame's current draw and automatically turns the
brightness down if it would exceed it. That is why the combination "brightness 40 +
limit 450 mA" in the factory configuration is safe even when a frame is completely
white.

---

## 8. Expansion: 16×32 from two 8×32 panels

Two 8×32 panels stacked give a display area of 32×16 = **512 LEDs**. That
is a different order of magnitude — the setup from §2/§3 no longer carries it.

### Power

| | |
|---|---|
| Full white, full brightness | 512 × 60 mA = **30.7 A** |
| typical colorful frame (~35 % average load) | ≈ 10 A |
| Factory configuration (`MX_BRIGHTNESS_DEFAULT 40`, `MX_MAX_MILLIAMPS 2000`) | ≤ 2 A |

30.7 A is no longer a power supply issue but a wiring issue: at 0.5 mm²
and 30 A, over half a volt drops across 50 cm. In practice nobody builds this out for
full white — you cap it via `MX_MAX_MILLIAMPS`, and before every `show()` FastLED
automatically turns the brightness down far enough for the value to hold.

| Power supply | sensible `MX_MAX_MILLIAMPS` | gives you |
|---|---:|---|
| 5 V / 3 A | 2500 | good for text, icons, animations |
| 5 V / 5 A | 4500 | full-area images too |
| 5 V / 10 A | 9000 | practically no restrictions anymore |

**Power injection, not daisy-chaining.** Each panel gets its own
5 V/GND feed directly from the power supply, not routed through the first panel. With
long panels an additional injection point at the far end is worthwhile. One
1000 µF electrolytic cap per panel, directly at the terminals.

### Data — one pin per panel

```
                  ┌──── 330 Ω ──── DIN  top panel    (tile 0)
   Pico GP14 ─────┘
                  ┌──── 330 Ω ──── DIN  bottom panel (tile 1)
   Pico GP17 ─────┘

   GND Pico  ─────┴──── GND power supply ──── GND both panels
```

The panels are **not** chained in series. Two separate strands are the
reason why `show()` takes only 7.98 ms instead of 15.66 ms — FastLED clocks both
out at the same time. In `FW/mx8_config.h`:

```c
#define MX_STRANDS      2
#define LED_DATA_PIN    14     // tile 0 -- GPIO number, not package pin
#define LED_DATA_PIN_2  17     // tile 1
```

Which panel is which tile is decided solely by which pin it hangs on:
pin 1 serves the first tile half of the buffer, pin 2 the second. If the
image is upside down or the halves are swapped — swap the panels between the pins
or set `MX_TILE_FLIP_ALT_ROWS`; `script/mapcheck.py` tells you which of
the two.

### Level shifter on the Pico

The RP2040 outputs **3.3 V**. Per the datasheet, WS2812B want V_IH ≥ 0.7 × V_DD,
so 3.5 V at 5.0 V — on paper too little. In practice it usually works
anyway, because 0.7 × V_DD is a **guaranteed** value, not a measured one,
and real parts switch considerably earlier. On top of that: if the
panel supply sags to 4.6 V under load, V_IH drops to 3.22 V and is then
below the Pico's 3.3 V.

So this is not a design but a coincidence that depends on the batch. With
512 LEDs, longer wires and a clean 5.0 V power supply, it can tip over.

| | |
|---|---|
| **Right** | 74AHCT125 or 74HCT245 — TTL input levels, switches at 2.0 V, drives cleanly to 5 V |
| **Also good enough** | the first LED as a sacrificial pixel: it gets 3.3 V, its `DOUT` delivers 5 V to the rest |
| **No** | resistor voltage divider — that only goes *down* |
| **No** | TXS0108E — auto direction sensing with ~4 kΩ pull-up after a 30 ns one-shot, marginal for 800 kHz WS2812B |

### What else the Pico brings

Native USB CDC instead of a UART: the baud rate no longer matters, `1543 B` per
frame are gone in just over 1 ms. And on the RP2040 FastLED clocks out via PIO+DMA,
with interrupts left enabled — so receiving and LED output run at the same time.
All that remains is the WS2812B protocol: **125 fps** with two data pins.
Derivation in [PROTOCOL.md §6](PROTOCOL.md).

### Bill of materials 16×32

| Part | Value | Purpose |
|---|---|---|
| Power supply | 5 V DC, ≥ 5 A | see table above; 10 A if it's supposed to get bright |
| Resistor | 2 × 330 Ω | one per data line, close to the panel |
| Electrolytic cap | 2 × 1000 µF, ≥ 10 V | one per panel, directly at the terminals |
| Stranded wire | ≥ 1.0 mm² for 5 V/GND | for double-digit amps |
| Level shifter | 74AHCT125 | on the Pico, see above |

---

## 9. Raspberry Pi Pico 2 W as the controller

### Is it up to the job?

Yes — and with more headroom than the Pico 1 in every respect. Measured, not
estimated: `tools/build_check.sh` compiles `FW.ino` against real FastLED
(3.10.5) for `rp2350 / rpipico2w` as well, cleanly and without warnings.

| | Pico (RP2040) | **Pico 2 W (RP2350A)** |
|---|---|---|
| SRAM | 264 kB | **520 kB** |
| Flash | 2 MB | **4 MB** |
| PIO state machines | 2 × 4 = 8 | **3 × 4 = 12** |
| DMA channels | 12 | **16** |
| Usable GPIO | GP0–GP22, GP26–GP28 | the same |
| Firmware needs (16×32) | 12186 B flash, 3581 B RAM | 11818 B / 3597 B |

The FastLED driver uses the same mechanism on both, confirmed at the compiler:
`FASTLED_RP2040_CLOCKLESS_PIO` active, `FASTLED_ALLOW_INTERRUPTS = 1`. FastLED
3.10 even has its own platform directory for the RP2350
(`platforms/arm/rp/rp2350/`).

**What it doesn't bring: more frames per second.** The limit is the
WS2812B protocol — 30 µs per LED, with 256 LEDs on a strand 7.98 ms → 125 fps.
That is the same on both chips. The gain is headroom, PIO channels and Wi-Fi.

### What Wi-Fi costs

The CYW43439 hangs on four GPIOs, which are thereby gone:

| GPIO | Function |
|---|---|
| GP23 | WL_ON |
| GP24 | WL_D |
| GP25 | WL_CS |
| GP29 | WL_CLK (also ADC3) |

On top of that the driver occupies **one PIO state machine** — it calls
`pio_claim_free_sm_and_add_program_for_gpio_range()`, so it picks a free one
by itself. With 12 state machines and at most 4 LED channels that is no
bottleneck.

If you don't need Wi-Fi, take the **Pico 2** without the W: same chip, GP23–GP25 and
GP29 stay free, and one more state machine.

### Erratum RP2350-E9 — not critical here

The RP2350 has a known bug: a GPIO **in input mode** can get stuck at ~2.2 V due to
leakage current (up to 120 µA), where the internal pull-down is too
weak to pull it down. **Outputs are not affected** — so neither are our
data lines.

It becomes relevant as soon as you connect buttons: then provide external pull-downs
≤ 8.2 kΩ instead of relying on the internal ones.

### Pin choice: must be consecutive

FastLED 3.10 ships an automatic parallel driver for RP2040/RP2350
(`FASTLED_RP2040_CLOCKLESS_PIO_AUTO 1`). It detects **consecutive** GPIOs and
combines them into a 2-, 4- or 8-lane group that a single
state machine outputs bit-interleaved. Non-consecutive pins fall back to
sequential output — one after the other instead of simultaneously.

This is hardware, not a library whim: the PIO instruction `out pins, N`
can only drive a contiguous block of pins.

**Therefore: always put LED pins next to each other.**

| Channels | Recommendation | why |
|---|---|---|
| 2 | GP14, GP15 | 2-lane group |
| 4 | GP14 – GP17 | 4-lane group |
| 8 | GP10 – GP17 | 8-lane group |

The previous assignment GP14 + GP17 works, but isn't adjacent —
with the auto driver it would be the worst case. Moving one panel from GP17 to
GP15 costs nothing and completes the group.

> **Four channels means four panels.** Each panel has exactly one data input;
> a panel cannot be split across two channels. For your 16×32 made of two
> panels, two channels is the maximum. Four pay off from four panels upward
> (e.g. 32×32) — the firmware is prepared for that, `MX_STRANDS 4` with
> `LED_DATA_PIN_3` / `_4` is compiled and tested.

---

## 10. Level shifter for 2–4 channels

The Pico outputs **3.3 V**; per the datasheet WS2812B want
V_IH ≥ 0.7 × V_DD, so 3.5 V at 5.0 V. That it has worked for you without one so far
is luck (see §8) — with more channels, more LEDs and longer
wires, the luck wears thinner.

### The right part

**74AHCT125** — quad buffer, 3.3 V in, 5 V out. One package covers
**all four channels**.

| | |
|---|---|
| Input threshold at 5 V VCC | V_IH ≥ **2.0 V**, V_IL ≤ 0.8 V (TTL) |
| Propagation delay | typ. **3.0 ns**, max. 5.5 ns at 15 pF |
| Supply | 4.5 – 5.5 V |
| Channels | 4, each with its own output enable (1OE … 4OE) |

The 2.0 V is the point: 3.3 V sits **1.3 V above** it — no longer
a borderline case, but within spec. And 3 ns of delay is completely uncritical
against a 1.25 µs bit time.

### Alternative: 74HCT245

The **74HCT245** is an octal bus transceiver and works just as well here —
the decisive property is the same for both: **TTL input threshold,
V_IH = 2.0 V**. That is the whole reason why 3.3 V is enough.

| | 74AHCT125 | 74HCT245 | 74AHCT245 |
|---|---|---|---|
| Channels | 4 | **8** | **8** |
| V_IH at 5 V | 2.0 V | 2.0 V | 2.0 V |
| Delay t_PD typ./max. | 3.0 / 5.5 ns | 12 / 22 ns | 3.5 / 10 ns |
| Output current | ±8 mA | ±6 mA | ±8 mA |
| Enable | **per channel** (1OE…4OE) | one for all (OE) | one for all |
| Direction | fixed | DIR for all 8 | DIR for all 8 |
| Package | DIP-14 | DIP-20 | DIP-20 |

**Is the 245 too slow?** No — and you can work it out. What bothers WS2812B
is not the delay itself (that merely shifts the signal), but
the difference between rising and falling edge. The timing window for
T0H/T1H is **±150 ns** wide:

| | Worst case | Share of tolerance budget |
|---|---:|---:|
| 74AHCT125 | 5.5 ns | 3.7 % |
| 74AHCT245 | 10 ns | 6.7 % |
| 74HCT245 | 22 ns | **14.7 %** |

And the point that settles the question for good: the **330 Ω series resistor**
makes the edge slower anyway than any of the three parts could
deliver it. With the panel's input (~15 pF) and a typical 100 pF/m of cable:

| Cable length | Load | Edge (10–90 %) |
|---|---:|---:|
| 10 cm | 25 pF | 18 ns |
| 30 cm | 45 pF | 33 ns |
| 100 cm | 115 pF | 84 ns |

So from about 10 cm of cable onward, the series resistor dominates, not the driver.
Speed is therefore **not a selection criterion** — what counts is channel count,
package and what's in your drawer.

Incidentally: the slower HCT edges actually ring *less* on long wires
than the sharp AHCT edges.

#### Wiring the 245

```
   DIR  (Pin 1)  ──► +5 V     H = A to B  (A is input, B is output)
   OE   (Pin 19) ──► GND      active low
   VCC  (Pin 20) ──► +5 V     same 5 V as the LEDs
   GND  (Pin 10) ──► GND      100 nF right between pins 20 and 10

   GP14 ──► A0 (Pin 2)     B0 (Pin 18) ──── 330 Ω ──► DIN 1
   GP15 ──► A1 (Pin 3)     B1 (Pin 17) ──── 330 Ω ──► DIN 2
   GP16 ──► A2 (Pin 4)     B2 (Pin 16) ──── 330 Ω ──► DIN 3
   GP17 ──► A3 (Pin 5)     B3 (Pin 15) ──── 330 Ω ──► DIN 4

   A4..A7 (Pin 6-9) ──► GND   don't leave unused inputs floating
```

Two pitfalls that don't exist with the '125:

1. **The B pins run backwards.** B0 is pin 18, B7 is pin 11 — so on the
   board they run opposite to A0…A7 (pins 2…9). If you stubbornly wire "straight across",
   you reverse the channel order.
2. **DIR applies to all eight channels.** With one 245 you can't route four channels
   out and four in. Irrelevant for our case — everything goes in
   the same direction.

**If you have a 74HCT245 lying around: use it.** For 2–4 channels both are
correct; the '125 is just more compact, the '245 has four channels in reserve.

### Wiring

```
   Pico 3.3 V                    74AHCT125                    Panel
   ─────────────                 ─────────                    ─────
   GP14 ──────────────► 1A    1Y ├──── 330 Ω ────────────────► DIN 1
   GP15 ──────────────► 2A    2Y ├──── 330 Ω ────────────────► DIN 2
   GP16 ──────────────► 3A    3Y ├──── 330 Ω ────────────────► DIN 3
   GP17 ──────────────► 4A    4Y ├──── 330 Ω ────────────────► DIN 4

                     1OE,2OE,3OE,4OE ──┬── GND     (active low = always on)
                              VCC ─────┼── +5 V    (same 5 V as the LEDs!)
                              GND ─────┴── GND
                              100 nF right between VCC and GND
```

Five spots where it otherwise gets stuck:

1. **Shifter VCC to the same 5 V as the LEDs.** The output level is
   always VCC — if you power it from a different source, the
   threshold shifts relative to the LEDs again.
2. **All four OE to GND.** They are active low; left floating, the output is
   high-impedance and nothing happens at all.
3. **Don't leave unused inputs floating**; tie them to GND. Otherwise CMOS inputs
   oscillate and draw needless current.
4. **100 nF directly at the package** between VCC and GND, short leads.
5. **330 Ω in series at the output**, as close to the panel as possible — damps reflections
   on the data line.

### What does NOT work

| | why |
|---|---|
| **Resistor voltage divider** | Dividers only go *down*. They won't turn 3.3 V into 5 V. |
| **TXS0108E** | Auto direction sensing: a ~30 ns one-shot drives hard, after that only a 4 kΩ pull-up holds the line. Marginal for 800 kHz WS2812B with their sharp edges. The part is meant for I²C, not for fast one-way signals. |
| **74HC125 (without the T)** | HC has CMOS thresholds: V_IH = 0.7 × VCC = 3.5 V at 5 V. Exactly the problem you wanted to solve. **The T in AHCT is the whole point.** |

### The zero-parts emergency fix

First LED as a **sacrificial pixel**: it gets the 3.3 V and is allowed to
twitch now and then; its `DOUT` delivers a clean 5 V to the rest. Costs one pixel per channel
and with four channels is uglier than a 40-cent IC — but it works
and needs nothing.


## Sources

- Current measurements per LED: [PJRC — How Much Current Do WS2812 / NeoPixel LEDs Really Use?](https://www.pjrc.com/how-much-current-do-ws2812-neopixel-leds-really-use/)
- Nano Every power supply: [Arduino Nano Every datasheet ABX00028](https://docs.arduino.cc/resources/datasheets/ABX00028-datasheet.pdf)
- 5V pin / back-feeding: [Arduino Forum — Nano Every power considerations](https://forum.arduino.cc/t/arduino-nano-every-power-considerations/620470),
  [Nano Every Power Output 5V pin](https://forum.arduino.cc/t/arduino-nano-every-power-output-5v-pin/935861)
- WS2812B V_IH = 0.7 × V_DD: [WS2812B datasheet (Worldsemi)](https://cdn-shop.adafruit.com/datasheets/WS2812B.pdf)
- Level shifting for WS2812B: [Adafruit NeoPixel Überguide — Powering & Logic Level](https://learn.adafruit.com/adafruit-neopixel-uberguide/basic-connections)
- RP2040 driver in FastLED (PIO + DMA, up to 8 strands): [FastLED PR #1261](https://github.com/FastLED/FastLED/pull/1261),
  [`led_sysdefs_arm_rp2040.h`](https://github.com/FastLED/FastLED/blob/master/src/platforms/arm/rp2040/led_sysdefs_arm_rp2040.h)

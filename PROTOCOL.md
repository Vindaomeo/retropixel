# MX8 — Binary protocol (PC ⇄ microcontroller)

Version 1. Transport: USB CDC (virtual COM port), **115200 8N1**, no flow control.
Byte order for multi-byte fields: **little endian**.

## 1. Frame format (identical in both directions)

```
+--------+--------+--------+--------+--------+---------//---------+--------+--------+
| SYNC0  | SYNC1  |  CMD   | LEN_LO | LEN_HI |      PAYLOAD       | CRC_LO | CRC_HI |
| 0xA5   | 0x5A   | 1 byte |     uint16 LE   |      LEN bytes     |    uint16 LE    |
+--------+--------+--------+--------+--------+---------//---------+--------+--------+
```

| Field   | Size  | Meaning                                                           |
|---------|-------|-------------------------------------------------------------------|
| SYNC    | 2     | Start token `0xA5 0x5A`. Resync point after errors.               |
| CMD     | 1     | Command code (see §2). Bit 7 = 1 ⇒ message comes from the Nano.   |
| LEN     | 2     | Payload length in bytes, `0 … 4096` (`MX_PROTO_MAX_PAYLOAD`).     |
| PAYLOAD | LEN   | Payload data.                                                     |
| CRC     | 2     | CRC-16/CCITT-FALSE over **CMD ‖ LEN_LO ‖ LEN_HI ‖ PAYLOAD**.      |

**CRC-16/CCITT-FALSE:** polynomial `0x1021`, init `0xFFFF`, no reflect, no final XOR.
The SYNC token is **not** included in the CRC.

Overhead: 7 bytes. A full-frame message is therefore `7 + 3 · W · H` bytes:

| Panel | Pixels | Image   | on the wire     |
|-------|-------:|--------:|----------------:|
| 8×8   |     64 |   192 B |         **199 B** |
| 8×32  |    256 |   768 B |         **775 B** |
| 16×32 |    512 |  1536 B |        **1543 B** |

`LEN` is 16 bits wide, so technically 65535 B would be possible. The 4096 B limit
protects the static receive buffer in MCU RAM; it lives as
`MX_PROTO_MAX_PAYLOAD` in `FW/mx8_protocol.h` and as `MAX_PAYLOAD` in
`script/mx8/protocol.py` — when changing it, update **both**.

The buffer the firmware actually allocates is only as large as needed:
`MX_MAX_PAYLOAD = 3 · W · H + 8`.

## 2. Command codes

### PC → Nano (bit 7 = 0)

| Code | Name             | Payload                                    | Response       |
|------|------------------|--------------------------------------------|----------------|
| 0x01 | `FRAME`          | 3 · W · H B, row-major (R,G,B)             | `ACK` / `NACK` |
| 0x02 | `PING`           | –                                          | `ACK`          |
| 0x03 | `SET_BRIGHTNESS` | 1 B, 0…255 (global brightness)             | `ACK`          |
| 0x04 | `CLEAR`          | –                                          | `ACK`          |
| 0x05 | `HELLO`          | – (requests an immediate `READY`)          | `READY`        |
| 0x06 | `RELEASE`        | – (PC releases the display → test pattern) | `ACK`          |
| 0x07 | `RAW`            | 2 B start index + 3 · n B (see §2.1)       | `ACK` / `NACK` |

#### 2.1 `RAW` — straight into the chain without pixel mapping

Payload: `START_LO START_HI` (uint16 LE, chain index of the first pixel), followed by
`3 · n` bytes of RGB. All remaining LEDs go black.

`RAW` bypasses `xyToIndex()` completely. What the panel shows is therefore the
**physical** wiring — exactly what you need to measure it instead of
guessing it. `script/mapcheck.py` builds on this and at the end produces the finished
config block for `FW/mx8_config.h`. Images are `FRAME`'s job.

`NACK 0x02` if `LEN < 2`, `(LEN − 2)` is not a multiple of 3, or
`START + n` extends past the panel.

### Nano → PC (bit 7 = 1)

| Code | Name    | Payload                                                     |
|------|---------|-------------------------------------------------------------|
| 0x81 | `READY` | 8 B geometry descriptor (see §3)                            |
| 0x82 | `ACK`   | 1 B: the acknowledged CMD code                              |
| 0x83 | `NACK`  | 2 B: acknowledged CMD code (0xFF = unknown), error code §4  |
| 0x84 | `LOG`   | n B ASCII text (debug, may be ignored)                      |

### 3. `READY` geometry descriptor (8 bytes)

| Offset | Type | Field      | Value in factory configuration             |
|--------|------|------------|--------------------------------------------|
| 0      | u8   | `PROTO`    | `0x01` — protocol version                  |
| 1      | u8   | `WIDTH`    | `8` or `32` — total width, max. 255        |
| 2      | u8   | `HEIGHT`   | `8` or `16` — total height, max. 255       |
| 3      | u8   | `BPP`      | `3` — bytes per pixel                      |
| 4      | u8   | `ORDER`    | 0=RGB 1=RBG 2=GRB 3=GBR 4=BRG 5=BGR (info) |
| 5      | u8   | `LAYOUT`   | `0` = progressive, 1 = serpentine (info)   |
| 6      | u8   | `FW_MAJOR` | `1`                                        |
| 7      | u8   | `FW_MINOR` | `0`                                        |

**The wire format is always plain RGB**, row-major, row 0 = top, column 0 = left.
`ORDER` only reports which physical channel order the attached panel has
(WS2812B = GRB, APA102 = BGR …) — FastLED does the reordering in the firmware.
Likewise, the firmware maps the panel layout itself — even when the display area
consists of several tiles on several data pins. The host always sees one
contiguous `WIDTH × HEIGHT` area and needs to know nothing about the wiring;
`LAYOUT` is purely informational, e.g. for display in a UI.

Pixel index `i = y * WIDTH + x`, payload offset `i * 3`.

## 4. Error codes (`NACK`)

| Code | Meaning                                          |
|------|--------------------------------------------------|
| 0x01 | CRC error                                        |
| 0x02 | Payload length doesn't match the command         |
| 0x03 | Unknown command                                  |
| 0x04 | Frame too long (> `MAX_PAYLOAD`), discarded      |
| 0x05 | Timeout during frame reception (resync forced)   |

## 5. Firmware state machine

```
  Reset ──> BOOT ───5 s no data────> TESTPATTERN
              │                           │
              └────────────┬──────────────┘
                           │  valid FRAME / CLEAR
                           v
                        RUNNING ───1 min no FRAME───> DARK
                           ^                           │
                           └─────── valid FRAME ───────┘

  RELEASE (from RUNNING or DARK) ──> TESTPATTERN
```

| from | event | to |
|---|---|---|
| BOOT | 5 s without data (`MX_BOOT_WINDOW_MS`) | TESTPATTERN |
| BOOT · TESTPATTERN · DARK | valid `FRAME` or `CLEAR` | RUNNING |
| RUNNING | 1 min without `FRAME` (`MX_PC_TIMEOUT_MS`) | **DARK** |
| RUNNING · DARK | `RELEASE` | TESTPATTERN |
| — | `PING`, `HELLO`, `SET_BRIGHTNESS` | no change |

* **BOOT** — for 5 s after reset: display black, one `READY` every second.
* **TESTPATTERN** — demo animations run, `READY` still every second.
  The controller stays ready to receive at all times.
* **RUNNING** — the first valid `FRAME` ends the test pattern **and** the
  `READY` broadcast.
* **DARK** — the PC had taken over and then stayed silent for `MX_PC_TIMEOUT_MS` = **1 min**.
  The display stays black, `READY` runs every
  second again. A valid `FRAME` brings it back immediately.

**Why DARK and not back to the demo.** As long as no PC has ever checked in,
the demo is the sensible sign of life — you can see that the hardware
is alive. But once the PC has been in control, its going silent is a
*fault*. A demo springing back to life would then look like normal operation and
would hide exactly what you want to see. A black display is the
more honest indication.

`RELEASE` is exempt from this: it is an orderly "I'm done" and
still leads to TESTPATTERN.

`PING` and `HELLO` do **not** trigger a switch to RUNNING — so the PC can
safely knock without killing the demo.

## 6. Flow control and frame rate

**The PC sends the next `FRAME` only after the `ACK` of the previous one
has arrived** (or after a 200 ms timeout). On AVR this is mandatory: there FastLED
disables interrupts during `show()`, and RX bytes arriving meanwhile are
lost. On RP2040 it wouldn't be necessary — but it does no harm and keeps the
frame rate honestly at what the controller can really manage.

Two limits determine the ceiling, and they are independent of each other:

**a) The serial link.** A full frame is `7 + 3·W·H` B on the wire,
the ACK 8 B, at 8N1 10 bits per byte.

**b) The WS2812B protocol itself.** 800 kHz, 24 bits per LED = 30 µs per LED, plus
300 µs reset. That costs time no matter how fast the link is. If you spread the
LEDs across several data pins, FastLED clocks them out simultaneously and the time
is divided accordingly.

How a and b combine depends on the platform:

| Platform | `show()` | Result |
|-----------|----------|----------|
| AVR (Nano, Nano Every) | bit-bang with interrupts disabled | a **+** b |
| RP2040 (Pico) | PIO + DMA, interrupts stay on | **max**(a, b) |

On the RP2040 the clocking-out continues in the background while the
next frame is already coming in — what counts is the longer of the two paths, not the
sum. There, `showPixels()` only waits for the *previous* DMA transfer.
Verified at the compiler, not derived from the docs: `tools/build_check.sh`
compiles `FW.ino` against real FastLED (3.10.4 and 3.10.5) for the RP2040, and there
`FASTLED_RP2040_CLOCKLESS_PIO` and `FASTLED_ALLOW_INTERRUPTS` are both `1`.

### 8×8, one data pin, AVR

| Baud rate | serial   | `show()` | max. fps |
|---------:|---------:|---------:|---------:|
| 115200   | 17.97 ms |  2.22 ms | **49.5** |
| 250000   |  8.28 ms |  2.22 ms |     95.2 |
| 500000   |  4.14 ms |  2.22 ms |    157.2 |

### 16×32 = 512 LEDs, two data pins

| Platform | Link | serial    | `show()` | max. fps |
|-----------|--------:|----------:|---------:|---------:|
| Nano Every @ 115200 | UART | 134.6 ms | 7.98 ms | **7.0** |
| Nano Every @ 500000 | UART |  31.0 ms | 7.98 ms |   25.6 |
| Pico | native USB CDC | ~1 ms | 7.98 ms | **125** |

So on the Pico only the WS2812B protocol remains — 125 fps with two
data pins, 64 fps with one. A third and fourth pin would scale
further.

In practice you land below that because of USB CDC and OS latency. `script/speedtest.py`
measures it and puts the measured value next to the theoretical limit; with
`--strands 2 --rp2040` it runs through the model above, with `--preview` even
without any hardware at all.

## 7. Resync rules

The receiver searches byte by byte for `0xA5 0x5A`. If a frame breaks off (no
complete reception within `RX_FRAME_TIMEOUT_MS` = 250 ms), the
parser is reset and a `NACK 0x05` is sent. A `0xA5 0x5A` inside the payload
is harmless, because the length is known in advance.

---

## 8. MX8 over Wi-Fi instead of USB

On the Pico 2 W it's tempting to drop the cable. What that costs breaks down
into three items — and only one of them is really expensive.

### a) Bytes: 8 %

An MX8 frame sits inside UDP/IP and an 802.11 frame. Fixed per packet:
20 B IP + 8 B UDP + 8 B LLC/SNAP + 24 B 802.11 header + 4 B FCS = **64 B**.

| Panel | MX8 frame | UDP packets | over the air | Overhead |
|-------|----------:|-----------:|-------------:|---------:|
| 8×8   |    199 B  | 1 | 263 B  | **+32 %** |
| 8×32  |    775 B  | 1 | 839 B  | **+8 %**  |
| 16×32 |   1543 B  | 2 | 1671 B | **+8 %**  |
| 32×32 |   3079 B  | 3 | 3271 B | **+6 %**  |

TCP costs 12 B more per packet. With large frames, that's noise.

> **One packet per panel.** 1543 B don't fit in one MTU (max. 1472 B UDP payload)
> and become two packets. *Half* a frame — exactly one panel — is 775 B
> and fits into a single one. So the panel boundary is also the natural
> packet boundary, and it coincides with the strand split.

### b) Airtime: negligible

Every 802.11 packet costs a fixed time budget regardless of its size:
DIFS 28 µs + average backoff ~67 µs + preamble 20 µs + SIFS 16 µs +
ACK ~30 µs ≈ **160 µs**.

For a 16×32 frame (2 packets) that is ~320 µs plus data time, together
0.5–0.8 ms per frame. Purely on paper that would allow over 1000 fps — the
air interface is not the bottleneck. `show()` at 7.98 ms is ten times
slower.

### c) Throughput: enough with headroom

Measured on the Pico W with the C SDK: **8.15 Mbit/s** (iperf2). The Pico 2 W uses
the same CYW43439 on the same PIO SPI.

| 16×32 at | Required | Share |
|-----------|-------:|-------:|
|  30 fps | 0.40 Mbit/s | 5 %  |
|  60 fps | 0.80 Mbit/s | 10 % |
| 125 fps | 1.67 Mbit/s | 21 % |

Even at the WS2812B limit, almost 80 % of the link stays free.

### d) Latency — and that's the whole point

Everything above is harmless. What gets expensive is the **round-trip time**, because the protocol
currently waits for an `ACK` per frame (§6). Over USB the RTT is negligible,
over Wi-Fi it isn't:

| RTT | 16×32, two data pins | |
|-----|----------------------:|---|
|  0 ms (fire-and-forget) | **125 fps** | only the WS2812B limit remains |
|  3 ms (Wi-Fi, power save off) | **125 fps** | `show()` still dominates |
| 35 ms (Wi-Fi, power save on) | **27 fps** | the link just sits around idle |

The 35 ms is not a horror scenario: measured pings to a Pico W come in
at 35–77 ms, the first one even at 187 ms. The cause is the CYW43439's
power-saving feature, which parks the radio between beacons.

**On the arduino-pico core this is already taken care of:** `WiFiClass::begin()` itself calls
`noLowPowerMode()` after connecting — in both station and
AP mode. If you use the Pico SDK directly, you have to set `cyw43_wifi_pm()`
yourself.

### Recommendation

1. **UDP instead of TCP**, fire-and-forget with a sequence number in the frame. A lost
   frame on a display isn't data loss, just a frame that stays up 8 ms
   longer — nobody sees that. This takes the RTT out of the equation entirely, and
   Wi-Fi jitter (retries, other traffic, beacons) no longer hits
   the frame rate either.
2. **One packet per panel** — 775 B, no IP fragmentation.
3. **Power save off.** Automatic on the Arduino core, otherwise by hand.
4. `READY`/`ACK`/`NACK` remain useful, but as an *occasional* knock
   (fetch geometry, check the connection) — not per frame.

With TCP, additionally set `TCP_NODELAY`, otherwise Nagle's algorithm collects
small packets and waits for the other side's ACK.

> **Do the math instead of taking it on faith:** `script/speedtest.py` contains the model as
> `theory_net()` / `net_frame_bytes()`; the tests in `tests/test_protocol.py`
> check every number in this section.

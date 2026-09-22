// ===========================================================================
//  MX8 -- RGB matrix driven by the PC over USB-CDC
//
//  Geometry, chipset and wiring all live in mx8_config.h.
//  Tested setups: 1x 8x8 on one data pin,
//                 2x 8x32 as 16x32 on two data pins.
//
//  Sequence after reset:
//    BOOT (5 s)   display black, one READY token with geometry every second
//    TESTPATTERN  demo animations, READY keeps going every second
//    RUNNING      first valid FRAME takes over -> demo + READY stop
//    DARK         the PC had taken over and then went silent for 1 min
//                 -> display stays black, READY keeps going
//
//  The difference between TESTPATTERN and DARK is deliberate: as long as no
//  PC has ever shown up, the demo is the sensible sign of life. But once the
//  PC has been in control, its silence is a fault -- a demo springing back
//  to life would then be misleading. The display comes back at any time
//  with a valid FRAME.
//
//  Protocol: see PROTOCOL.md
//  Boards:   Arduino Nano / Nano Every (AVR)  -- fine for up to ~256 LEDs
//            Raspberry Pi Pico (RP2040)       -- the better choice from 256 LEDs:
//                                                native USB, PIO+DMA instead of
//                                                bit-banging with interrupts off
//  Requires: FastLED >= 3.6  (older versions don't compile for megaAVR;
//            the RP2040 PIO driver arrived with FastLED 3.6.0)
// ===========================================================================

#include "mx8_config.h"

// On AVR, FastLED disables interrupts during show() -- ~1.9 ms for 64 LEDs,
// and every byte arriving at the UART in the meantime is lost. That's why the
// host works with ACK handshakes (see PROTOCOL.md §6).
// On RP2040, a PIO state machine with DMA does the clocking out; interrupts
// may stay on there (and have to, otherwise USB-CDC dies).
#if defined(__AVR__)
#define FASTLED_ALLOW_INTERRUPTS 0
#endif
#define FASTLED_INTERNAL                 // silence version pragmas at compile time
#include <FastLED.h>

#include "mx8_protocol.h"
#include "mx8_rx.h"
#include "mx8_map.h"                     // xyToIndex()

// ===========================================================================
//  Globals
// ===========================================================================
static CRGB leds[MX_NUM_LEDS];

enum State : uint8_t { ST_BOOT, ST_TESTPATTERN, ST_RUNNING, ST_DARK };
static State    g_state          = ST_BOOT;
static uint32_t g_stateEnteredMs = 0;
static uint32_t g_lastReadyMs    = 0;
static uint32_t g_lastFrameMs    = 0;
static uint8_t  g_brightness     = MX_BRIGHTNESS_DEFAULT;

// ===========================================================================
//  TX -- sending frames to the PC
// ===========================================================================
static void sendFrame(uint8_t cmd, const uint8_t *payload, uint16_t len) {
  uint8_t  hdr[3] = { cmd, (uint8_t)(len & 0xFF), (uint8_t)(len >> 8) };
  uint16_t crc    = mx_crc16(hdr, 3);
  for (uint16_t i = 0; i < len; i++) crc = mx_crc16_update(crc, payload[i]);

  Serial.write(MX_SYNC0);
  Serial.write(MX_SYNC1);
  Serial.write(hdr, 3);
  if (len) Serial.write(payload, len);
  Serial.write((uint8_t)(crc & 0xFF));
  Serial.write((uint8_t)(crc >> 8));
}

// FastLED's EOrder is octal-encoded (RGB=0012, GRB=0102, ...). For the
// READY token we want a compact 0..5 code -- purely informational.
static uint8_t colorOrderCode() {
  switch ((int)LED_COLOR_ORDER) {
    case 0012: return 0;   // RGB
    case 0021: return 1;   // RBG
    case 0102: return 2;   // GRB
    case 0120: return 3;   // GBR
    case 0201: return 4;   // BRG
    case 0210: return 5;   // BGR
    default:   return 0xFF;
  }
}

static void sendReady() {
  const uint8_t d[8] = {
    MX_PROTO_VERSION,
    MX_WIDTH,
    MX_HEIGHT,
    3,                                   // BPP
    colorOrderCode(),                    // physical panel order (info)
    MX_TILE_SERPENTINE,
    MX_FW_MAJOR,
    MX_FW_MINOR
  };
  sendFrame(MSG_READY, d, sizeof(d));
}

static inline void sendAck(uint8_t cmd) {
  sendFrame(MSG_ACK, &cmd, 1);
}

static inline void sendNack(uint8_t cmd, uint8_t err) {
  const uint8_t d[2] = { cmd, err };
  sendFrame(MSG_NACK, d, 2);
}

static void sendLog(const char *msg) {
#if MX_ENABLE_LOG
  sendFrame(MSG_LOG, (const uint8_t *)msg, (uint16_t)strlen(msg));
#else
  (void)msg;
#endif
}

// ===========================================================================
//  State transitions
// ===========================================================================
static void enterState(State s) {
  if (g_state == s) return;
  g_state          = s;
  g_stateEnteredMs = millis();

  if (s == ST_RUNNING) {
    sendLog("PC took over");
  } else if (s == ST_TESTPATTERN) {
    g_lastReadyMs = 0;                   // push out a READY right away
    sendLog("testpattern");
  } else if (s == ST_DARK) {
    // Clear and show once; after that the loop leaves the LEDs alone.
    FastLED.clear();
    FastLED.show();
    g_lastReadyMs = 0;
    sendLog("dark -- pc gone");
  }
}

// ===========================================================================
//  RX -- the state machine itself lives in mx8_rx.h (hardware-free and
//  tested against the Python side, see tests/). Only the two callbacks
//  are hooked up here.
// ===========================================================================
static MxRx g_rx;

static void handleCommand(uint8_t cmd, const uint8_t *data, uint16_t len);

static void onRxMessage(uint8_t cmd, const uint8_t *data, uint16_t len, void *) {
  handleCommand(cmd, data, len);
}

static void onRxError(uint8_t cmd, uint8_t err, void *) {
  sendNack(cmd, err);
}

static void pollSerial() {
  const uint32_t now = millis();
  // Drain everything sitting in the UART buffer -- not just one byte per loop().
  while (Serial.available() > 0) {
    mxrx_byte(&g_rx, (uint8_t)Serial.read(), now);
  }
  mxrx_tick(&g_rx, millis());            // half a frame got stuck?
}

// ===========================================================================
//  Command handling
// ===========================================================================
static void applyFrame(const uint8_t *data) {
  const uint8_t *p = data;
  for (uint8_t y = 0; y < MX_HEIGHT; y++) {
    for (uint8_t x = 0; x < MX_WIDTH; x++) {
      // The wire format is always plain RGB; FastLED reorders the physical
      // channel order itself based on LED_COLOR_ORDER.
      leds[xyToIndex(x, y)] = CRGB(p[0], p[1], p[2]);
      p += 3;
    }
  }
  FastLED.show();
}

static void handleCommand(uint8_t cmd, const uint8_t *data, uint16_t len) {
  switch (cmd) {

    case CMD_FRAME:
      if (len != MX_FRAME_BYTES) { sendNack(cmd, ERR_LENGTH); return; }
      applyFrame(data);
      g_lastFrameMs = millis();
      enterState(ST_RUNNING);
      sendAck(cmd);
      break;

    case CMD_RAW: {
      // Straight into the chain without mapping -- this lets you measure the
      // panel's wiring instead of guessing it.
      if (len < 2 || ((len - 2) % 3) != 0) { sendNack(cmd, ERR_LENGTH); return; }
      const uint16_t start = (uint16_t)data[0] | ((uint16_t)data[1] << 8);
      const uint16_t n     = (uint16_t)((len - 2) / 3);
      if ((uint32_t)start + n > MX_NUM_LEDS) { sendNack(cmd, ERR_LENGTH); return; }

      FastLED.clear();
      const uint8_t *p = data + 2;
      for (uint16_t i = 0; i < n; i++, p += 3) leds[start + i] = CRGB(p[0], p[1], p[2]);
      FastLED.show();

      g_lastFrameMs = millis();
      enterState(ST_RUNNING);
      sendAck(cmd);
      break;
    }

    case CMD_CLEAR:
      if (len != 0) { sendNack(cmd, ERR_LENGTH); return; }
      FastLED.clear();
      FastLED.show();
      g_lastFrameMs = millis();
      enterState(ST_RUNNING);
      sendAck(cmd);
      break;

    case CMD_SET_BRIGHTNESS:
      if (len != 1) { sendNack(cmd, ERR_LENGTH); return; }
      g_brightness = data[0];
      FastLED.setBrightness(g_brightness);
      FastLED.show();
      sendAck(cmd);
      break;

    case CMD_PING:
      // Deliberately NO switch to RUNNING -- the PC may knock on the door
      // without killing the test pattern.
      if (len != 0) { sendNack(cmd, ERR_LENGTH); return; }
      sendAck(cmd);
      break;

    case CMD_HELLO:
      if (len != 0) { sendNack(cmd, ERR_LENGTH); return; }
      sendReady();
      break;

    case CMD_RELEASE:
      // Deliberately back to the demo, not to DARK: RELEASE is an orderly
      // "I'm done", not a failure. If you want black, send CLEAR and then
      // let the minute run out.
      if (len != 0) { sendNack(cmd, ERR_LENGTH); return; }
      sendAck(cmd);
      FastLED.clear();
      enterState(ST_TESTPATTERN);
      break;

    default:
      sendNack(cmd, ERR_UNKNOWN_CMD);
      break;
  }
}

// ===========================================================================
//  Test pattern -- four demos, switching every 6 s
// ===========================================================================
static void patRainbow(uint8_t t) {
  for (uint8_t y = 0; y < MX_HEIGHT; y++)
    for (uint8_t x = 0; x < MX_WIDTH; x++)
      leds[xyToIndex(x, y)] = CHSV((uint8_t)(t + x * 8 + y * 8), 255, 255);
}

static void patPlasma(uint8_t t) {
  for (uint8_t y = 0; y < MX_HEIGHT; y++)
    for (uint8_t x = 0; x < MX_WIDTH; x++) {
      uint8_t v = inoise8((uint16_t)x * 40, (uint16_t)y * 40, (uint16_t)t * 12);
      leds[xyToIndex(x, y)] = CHSV((uint8_t)(v + t), 220, qadd8(v, 40));
    }
}

// A dot running along the rows, with an afterglow.
static void patComet(uint8_t t) {
  (void)t;
  fadeToBlackBy(leds, MX_NUM_LEDS, 48);
  // Own counter instead of t: with 512 LEDs a uint8_t overflows long before.
  static uint16_t pos = 0;
  if (++pos >= MX_NUM_LEDS) pos = 0;
  leds[xyToIndex((uint8_t)(pos % MX_WIDTH), (uint8_t)(pos / MX_WIDTH))] =
      CHSV((uint8_t)(pos >> 1), 200, 255);
}

// Checkerboard pulsing between two complementary colors.
static void patCheck(uint8_t t) {
  uint8_t puls = sin8(t * 3);
  for (uint8_t y = 0; y < MX_HEIGHT; y++)
    for (uint8_t x = 0; x < MX_WIDTH; x++) {
      bool a = ((x ^ y) & 1);
      leds[xyToIndex(x, y)] = a ? CHSV(t, 255, puls)
                                : CHSV((uint8_t)(t + 128), 255, (uint8_t)(255 - puls));
    }
}

static void runTestpattern() {
  static uint32_t lastMs  = 0;
  static uint8_t  t       = 0;
  if (millis() - lastMs < 25) return;    // ~40 fps
  lastMs = millis();
  t++;

  switch ((millis() / 6000UL) & 0x03) {
    case 0: patRainbow(t); break;
    case 1: patPlasma(t);  break;
    case 2: patComet(t);   break;
    default: patCheck(t);  break;
  }
  FastLED.show();
}

// ===========================================================================
//  setup / loop
// ===========================================================================
void setup() {
  Serial.begin(MX_SERIAL_BAUD);
  mxrx_init(&g_rx, onRxMessage, onRxError, nullptr);

  // One controller per data pin. The addLeds(leds, offset, count) form gives
  // each strand a window into the shared buffer; FastLED.show() then clocks
  // out all strands simultaneously -- two strands = half the time.
#ifdef LED_CLOCK_PIN
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN, LED_CLOCK_PIN, LED_COLOR_ORDER>(
      leds, MX_NUM_LEDS);
#elif MX_STRANDS == 1
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN, LED_COLOR_ORDER>(leds, MX_NUM_LEDS);
#else
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN, LED_COLOR_ORDER>(
      leds, 0 * MX_LEDS_PER_STRAND, MX_LEDS_PER_STRAND);
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN_2, LED_COLOR_ORDER>(
      leds, 1 * MX_LEDS_PER_STRAND, MX_LEDS_PER_STRAND);
  #if MX_STRANDS >= 3
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN_3, LED_COLOR_ORDER>(
      leds, 2 * MX_LEDS_PER_STRAND, MX_LEDS_PER_STRAND);
  #endif
  #if MX_STRANDS >= 4
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN_4, LED_COLOR_ORDER>(
      leds, 3 * MX_LEDS_PER_STRAND, MX_LEDS_PER_STRAND);
  #endif
#endif

  FastLED.setBrightness(g_brightness);
#if MX_MAX_MILLIAMPS > 0
  FastLED.setMaxPowerInVoltsAndMilliamps(MX_PSU_VOLTS, MX_MAX_MILLIAMPS);
#endif
  FastLED.clear();
  FastLED.show();

  g_state          = ST_BOOT;
  g_stateEnteredMs = millis();
  g_lastReadyMs    = 0;                  // first READY immediately
}

void loop() {
  pollSerial();

  const uint32_t now = millis();

  // --- READY broadcast: in BOOT and TESTPATTERN, not during RUNNING ---
  if (g_state != ST_RUNNING && (now - g_lastReadyMs) >= MX_READY_INTERVAL_MS) {
    g_lastReadyMs = now;
    sendReady();
  }

  switch (g_state) {

    case ST_BOOT:
      // Boot window expired and nobody showed up -> demo on.
      if (now - g_stateEnteredMs >= MX_BOOT_WINDOW_MS) enterState(ST_TESTPATTERN);
      break;

    case ST_TESTPATTERN:
      runTestpattern();
      break;

    case ST_RUNNING:
      // PC went silent -> go dark after MX_PC_TIMEOUT_MS. Deliberately NOT
      // back to the demo: a test pattern springing back to life looks like
      // normal operation and hides the fact that the feed is stuck.
      if (now - g_lastFrameMs >= MX_PC_TIMEOUT_MS) {
        enterState(ST_DARK);
      }
      break;

    case ST_DARK:
      // Nothing to do -- the display stays black until a FRAME arrives.
      break;
  }
}

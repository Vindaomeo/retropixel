// ===========================================================================
//  MX8 -- 8x8 RGB-Matrix am Arduino Nano Every, gesteuert per USB-CDC
//
//  Ablauf nach dem Reset:
//    BOOT (5 s)   Display schwarz, jede Sekunde ein READY-Token mit Geometrie
//    TESTPATTERN  Demo-Animationen, READY laeuft weiter jede Sekunde
//    RUNNING      erster gueltiger FRAME uebernimmt -> Demo + READY stoppen
//                 5 s ohne FRAME oder RELEASE -> zurueck nach TESTPATTERN
//
//  Protokoll: siehe PROTOCOL.md
//  Board:     Arduino Nano Every (ATmega4809, megaAVR)
//  Benoetigt: FastLED >= 3.6  (aeltere Versionen kompilieren nicht fuer megaAVR)
// ===========================================================================

#include "mx8_config.h"

// FastLED sperrt waehrend show() die Interrupts. Bei 64 LEDs sind das ~1,9 ms.
// Deshalb arbeitet der Host ACK-quittiert (siehe PROTOCOL.md §6).
#define FASTLED_ALLOW_INTERRUPTS 0
#define FASTLED_INTERNAL                 // Versions-Pragmas beim Kompilieren aus
#include <FastLED.h>

#include "mx8_protocol.h"
#include "mx8_rx.h"

// ===========================================================================
//  Globals
// ===========================================================================
static CRGB leds[MX_NUM_LEDS];

enum State : uint8_t { ST_BOOT, ST_TESTPATTERN, ST_RUNNING };
static State    g_state          = ST_BOOT;
static uint32_t g_stateEnteredMs = 0;
static uint32_t g_lastReadyMs    = 0;
static uint32_t g_lastFrameMs    = 0;
static uint8_t  g_brightness     = MX_BRIGHTNESS_DEFAULT;

// ===========================================================================
//  Pixel-Mapping:  (x,y) im Host-Koordinatensystem -> Index in der LED-Kette
//  Host: Zeile 0 = oben, Spalte 0 = links, row-major.
// ===========================================================================
static inline uint16_t xyToIndex(uint8_t x, uint8_t y) {
#if MX_ORIGIN == 1                       // oben-rechts
  x = MX_WIDTH  - 1 - x;
#elif MX_ORIGIN == 2                     // unten-links
  y = MX_HEIGHT - 1 - y;
#elif MX_ORIGIN == 3                     // unten-rechts
  x = MX_WIDTH  - 1 - x;
  y = MX_HEIGHT - 1 - y;
#endif

#if MX_SERPENTINE
  if (y & 0x01) x = MX_WIDTH - 1 - x;    // ungerade Zeilen rueckwaerts
#endif

  return (uint16_t)y * MX_WIDTH + x;
}

// ===========================================================================
//  TX -- Frames zum PC senden
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

// FastLEDs EOrder ist oktal kodiert (RGB=0012, GRB=0102, ...). Fuer das
// READY-Token wollen wir eine kompakte 0..5-Kennung -- rein informativ.
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
    colorOrderCode(),                    // physische Panel-Reihenfolge (Info)
    MX_SERPENTINE,
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
//  State-Wechsel
// ===========================================================================
static void enterState(State s) {
  if (g_state == s) return;
  g_state          = s;
  g_stateEnteredMs = millis();

  if (s == ST_RUNNING) {
    sendLog("PC took over");
  } else if (s == ST_TESTPATTERN) {
    g_lastReadyMs = 0;                   // sofort ein READY nachschieben
    sendLog("testpattern");
  }
}

// ===========================================================================
//  RX -- die State-Machine selbst steckt in mx8_rx.h (hardwarefrei und
//  gegen die Python-Seite getestet, siehe tests/). Hier haengen nur die
//  beiden Callbacks dran.
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
  // Alles abholen, was im UART-Puffer liegt -- nicht nur ein Byte pro loop().
  while (Serial.available() > 0) {
    mxrx_byte(&g_rx, (uint8_t)Serial.read(), now);
  }
  mxrx_tick(&g_rx, millis());            // halber Frame haengengeblieben?
}

// ===========================================================================
//  Kommando-Auswertung
// ===========================================================================
static void applyFrame(const uint8_t *data) {
  const uint8_t *p = data;
  for (uint8_t y = 0; y < MX_HEIGHT; y++) {
    for (uint8_t x = 0; x < MX_WIDTH; x++) {
      // Wire-Format ist immer plain RGB; die physische Kanalreihenfolge
      // sortiert FastLED anhand von LED_COLOR_ORDER selbst um.
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
      // Bewusst KEIN Wechsel nach RUNNING -- der PC darf anklopfen,
      // ohne das Testpattern abzuwuergen.
      if (len != 0) { sendNack(cmd, ERR_LENGTH); return; }
      sendAck(cmd);
      break;

    case CMD_HELLO:
      if (len != 0) { sendNack(cmd, ERR_LENGTH); return; }
      sendReady();
      break;

    case CMD_RELEASE:
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
//  Testpattern -- vier Demos, Wechsel alle 6 s
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

// Laufender Punkt entlang der Zeilen, mit Nachleuchten.
static void patComet(uint8_t t) {
  fadeToBlackBy(leds, MX_NUM_LEDS, 48);
  uint8_t pos = (uint8_t)((t >> 1) % MX_NUM_LEDS);
  leds[xyToIndex(pos % MX_WIDTH, pos / MX_WIDTH)] = CHSV(t, 200, 255);
}

// Schachbrett, das zwischen zwei Komplementaerfarben pulst.
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

#ifdef LED_CLOCK_PIN
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN, LED_CLOCK_PIN, LED_COLOR_ORDER>(
      leds, MX_NUM_LEDS);
#else
  FastLED.addLeds<LED_CHIPSET, LED_DATA_PIN, LED_COLOR_ORDER>(leds, MX_NUM_LEDS);
#endif

  FastLED.setBrightness(g_brightness);
#if MX_MAX_MILLIAMPS > 0
  FastLED.setMaxPowerInVoltsAndMilliamps(MX_PSU_VOLTS, MX_MAX_MILLIAMPS);
#endif
  FastLED.clear();
  FastLED.show();

  g_state          = ST_BOOT;
  g_stateEnteredMs = millis();
  g_lastReadyMs    = 0;                  // erstes READY sofort
}

void loop() {
  pollSerial();

  const uint32_t now = millis();

  // --- READY-Broadcast: in BOOT und TESTPATTERN, nicht waehrend RUNNING ---
  if (g_state != ST_RUNNING && (now - g_lastReadyMs) >= MX_READY_INTERVAL_MS) {
    g_lastReadyMs = now;
    sendReady();
  }

  switch (g_state) {

    case ST_BOOT:
      // Wartefenster abgelaufen und niemand hat sich gemeldet -> Demo an.
      if (now - g_stateEnteredMs >= MX_BOOT_WINDOW_MS) enterState(ST_TESTPATTERN);
      break;

    case ST_TESTPATTERN:
      runTestpattern();
      break;

    case ST_RUNNING:
      // PC verstummt -> Display nicht einfrieren lassen, zurueck zur Demo.
      if (now - g_lastFrameMs >= MX_PC_TIMEOUT_MS) {
        sendLog("pc timeout");
        enterState(ST_TESTPATTERN);
      }
      break;
  }
}

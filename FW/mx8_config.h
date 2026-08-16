// ===========================================================================
//  mx8_config.h  --  Hardware- und Verhaltens-Konfiguration
//  Das ist die EINZIGE Datei, die du anfassen musst, wenn du ein anderes
//  Panel / einen anderen LED-Chipsatz anschliesst.
// ===========================================================================
#pragma once

// ---------------------------------------------------------------------------
// 1) LED-Chipsatz
// ---------------------------------------------------------------------------
// Einzeldraht-Chips (nur Data-Pin):
//   NEOPIXEL WS2811 WS2812 WS2812B WS2813 WS2815 SK6812 SK6822
//   TM1809 TM1804 TM1803 UCS1903 UCS1904 GS1903 GW6205 PL9823 ...
// Getaktete Chips (Data + Clock):
//   APA102 APA102HD SK9822 DOTSTAR WS2801 LPD8806 LPD6803 P9813 SM16716
//
// -> Fuer getaktete Chips zusaetzlich LED_CLOCK_PIN definieren (unten).
#define LED_CHIPSET      WS2812B

// Physische Kanalreihenfolge des Panels. Typisch:
//   WS2812B / WS2813 -> GRB      WS2811 -> RGB (oft auch BRG)
//   APA102 / SK9822  -> BGR      WS2801 -> RGB
// Wenn Rot und Gruen vertauscht erscheinen: hier GRB <-> RGB tauschen.
#define LED_COLOR_ORDER  GRB

#define LED_DATA_PIN     6
// #define LED_CLOCK_PIN 7          // NUR fuer APA102/WS2801/LPD8806 & Co.
// #define LED_SPI_MHZ   12         // optional, Default 12 MHz bei getakteten

// ---------------------------------------------------------------------------
// 2) Panel-Geometrie
// ---------------------------------------------------------------------------
#define MX_WIDTH         8
#define MX_HEIGHT        8

// Verdrahtung der Pixelkette auf dem Panel:
//   0 = progressiv  (jede Zeile links -> rechts, klassisches 8x8-NeoPixel-Board)
//   1 = serpentine  (Zeile 0 l->r, Zeile 1 r->l, ...)
#define MX_SERPENTINE    0

// Erste Pixel-Ecke. 0 = oben-links (Normalfall).
// 1 = oben-rechts, 2 = unten-links, 3 = unten-rechts.
#define MX_ORIGIN        0

// ---------------------------------------------------------------------------
// 3) Strom / Helligkeit
// ---------------------------------------------------------------------------
// 64 WS2812B auf voller Weiss-Helligkeit ziehen ~3,8 A. Der 5V-Pin des Nano
// Every kann das NICHT. Entweder externes 5V-Netzteil (GND verbinden!) oder
// Helligkeit deckeln. Das Power-Limit laesst FastLED automatisch runterregeln.
#define MX_BRIGHTNESS_DEFAULT   40      // 0..255
#define MX_MAX_MILLIAMPS        450     // 0 = Limit aus
#define MX_PSU_VOLTS            5

// ---------------------------------------------------------------------------
// 4) Timing / Protokoll
// ---------------------------------------------------------------------------
// Muss zur Host-Seite passen (-b bzw. Umgebungsvariable MX8_BAUD).
// Wer beides aendert, bekommt bei 250000 etwa die doppelte Framerate.
#define MX_SERIAL_BAUD          115200UL
#define MX_BOOT_WINDOW_MS       5000UL  // Wartefenster nach Reset
#define MX_READY_INTERVAL_MS    1000UL  // "ich bin ready" Takt
#define MX_PC_TIMEOUT_MS        5000UL  // ohne FRAME -> zurueck zum Testpattern
#define MX_RX_FRAME_TIMEOUT_MS  250UL   // halber Frame -> Parser-Resync

// Debug-LOG-Frames (0x84) senden. Fuer den Produktivbetrieb auf 0.
#define MX_ENABLE_LOG           1

// ---------------------------------------------------------------------------
// 5) Abgeleitete Konstanten -- nicht aendern
// ---------------------------------------------------------------------------
#define MX_NUM_LEDS      (MX_WIDTH * MX_HEIGHT)
#define MX_FRAME_BYTES   (MX_NUM_LEDS * 3)
#define MX_MAX_PAYLOAD   (MX_FRAME_BYTES + 8)

#if MX_MAX_PAYLOAD > 512
#error "MX_MAX_PAYLOAD > 512 -- Protokoll erlaubt maximal 512 Byte Payload"
#endif

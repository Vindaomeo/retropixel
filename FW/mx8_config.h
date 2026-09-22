// ===========================================================================
//  mx8_config.h  --  hardware and behavior configuration
//  This is the ONLY file you need to touch when you hook up a different
//  panel / a different LED chipset.
// ===========================================================================
#pragma once
#include "mx8_protocol.h"        // framing constants, incl. MX_PROTO_MAX_PAYLOAD

// ---------------------------------------------------------------------------
// 0) Which display is attached?  -- change exactly one line
// ---------------------------------------------------------------------------
#define MX_BOARD_8X8      1     // one 8x8 NeoPixel board, one data pin
#define MX_BOARD_16X32    2     // two 8x32 panels stacked, two data pins
#define MX_BOARD_CUSTOM  99     // everything via -D... from outside (used by tests/)

#ifndef MX_BOARD
// #define MX_BOARD  MX_BOARD_8X8
#define MX_BOARD  MX_BOARD_16X32
#endif

// ---------------------------------------------------------------------------
// 1) LED chipset
// ---------------------------------------------------------------------------
// Single-wire chips (data pin only):
//   NEOPIXEL WS2811 WS2812 WS2812B WS2813 WS2815 SK6812 SK6822
//   TM1809 TM1804 TM1803 UCS1903 UCS1904 GS1903 GW6205 PL9823 ...
// Clocked chips (data + clock):
//   APA102 APA102HD SK9822 DOTSTAR WS2801 LPD8806 LPD6803 P9813 SM16716
//
// -> For clocked chips, additionally define LED_CLOCK_PIN (below).
//    Clocked chips only work with MX_STRANDS 1.
#define LED_CHIPSET      WS2812B

// Physical channel order of the panel. Typical:
//   WS2812B / WS2813 -> GRB      WS2811 -> RGB (often BRG too)
//   APA102 / SK9822  -> BGR      WS2801 -> RGB
// If red and green appear swapped: swap GRB <-> RGB here.
#define LED_COLOR_ORDER  GRB

// #define LED_CLOCK_PIN 7          // ONLY for APA102/WS2801/LPD8806 & co.
// #define LED_SPI_MHZ   12         // optional, default 12 MHz when clocked

// ===========================================================================
// 2) Panel geometry -- described as tiles
// ===========================================================================
//  The display area is made up of MX_TILES_X * MX_TILES_Y identical tiles.
//  A single panel is simply the special case 1x1.
//
//         MX_TILES_X = 1, MX_TILES_Y = 2, tile 32x8:
//         +--------------------------------+  tile 0  (strand 1)
//         +--------------------------------+  tile 1  (strand 2)
//
//  Order of the tiles in the chain: row by row, tile 0 top left.
//  Within a tile, MX_TILE_VERTICAL / MX_TILE_SERPENTINE / MX_TILE_ORIGIN
//  describe how the chain runs.
//
//  The wiring of a store-bought panel is rarely in the datasheet.
//  Don't guess -- measure:   python script/mapcheck.py
// ===========================================================================

#if MX_BOARD == MX_BOARD_8X8
  // --- a classic 8x8 NeoPixel board -----------------------------------------
  #define MX_TILE_W               8
  #define MX_TILE_H               8
  #define MX_TILES_X              1
  #define MX_TILES_Y              1

  #define MX_TILE_VERTICAL        0   // chain runs row by row
  #define MX_TILE_SERPENTINE      0   // every row in the same direction
  #define MX_TILE_ORIGIN          0   // first pixel top left
  #define MX_TILE_FLIP_ALT_ROWS   0

  #define MX_STRANDS              1
  #define LED_DATA_PIN            6

  #define MX_BRIGHTNESS_DEFAULT   40
  #define MX_MAX_MILLIAMPS        450

#elif MX_BOARD == MX_BOARD_16X32
  // --- two 8x32 panels, stacked into 32x16 ----------------------------------
  //
  //  Measured on the panel: the chain runs COLUMN BY COLUMN, eight pixels per
  //  column, serpentine. The first column of the display area consists of the
  //  first eight chain pixels of panel 1 (top) and those of panel 2 (bottom).
  //
  //     Chain:  0  15 16 31 ...        top panel    = tile 0 = strand 1
  //             1  14 17 30                 32 wide, 8 high
  //             .. .. .. ..
  //             7   8 23 24
  //             ----------------
  //             256 ...                bottom panel = tile 1 = strand 2
  //
  //  Column 0 runs down, column 1 back up, and so on.
  #define MX_TILE_W               32
  #define MX_TILE_H               8
  #define MX_TILES_X              1
  #define MX_TILES_Y              2

  #define MX_TILE_VERTICAL        1   // chain runs column by column, not row by row
  #define MX_TILE_SERPENTINE      1   // every other column reversed
  #define MX_TILE_ORIGIN          0   // first pixel top left
  #define MX_TILE_FLIP_ALT_ROWS   0
  #define MX_TILE_ORDER_SERPENTINE 0

  #define MX_STRANDS              2
  // Pico GPIO numbers (GP14 / GP17), NOT the pin numbers on the package.
  #define LED_DATA_PIN            14  // top panel    (tile 0)
  #define LED_DATA_PIN_2          17  // bottom panel (tile 1)

  // No effect on the Pico -- native USB-CDC always runs at full speed.
  // Set high here anyway, in case the same configuration ever ends up on
  // a UART board.
  #define MX_SERIAL_BAUD          1000000UL

  // 512 WS2812B draw 30.7 A at full white. Without an external 5V power
  // supply nothing burns here, because FastLED throttles first -- but the
  // picture gets dark. Connect a power supply and raise the value.
  #define MX_BRIGHTNESS_DEFAULT   40
  #define MX_MAX_MILLIAMPS        2000

#elif MX_BOARD == MX_BOARD_CUSTOM
  // Preset nothing -- the defaults below apply, everything else comes via -D.

#else
  #error "Unknown MX_BOARD -- choose MX_BOARD_8X8 or MX_BOARD_16X32"
#endif

// ---------------------------------------------------------------------------
// Defaults -- only apply if the chosen preset doesn't set the value.
// ---------------------------------------------------------------------------
#ifndef MX_TILE_W
  #define MX_TILE_W  8                   // tile width in pixels
#endif
#ifndef MX_TILE_H
  #define MX_TILE_H  8                   // tile height in pixels
#endif
#ifndef MX_TILES_X
  #define MX_TILES_X 1
#endif
#ifndef MX_TILES_Y
  #define MX_TILES_Y 1
#endif
#ifndef MX_TILE_VERTICAL
  #define MX_TILE_VERTICAL      0        // 0 = chain row by row, 1 = column by column
#endif
#ifndef MX_TILE_SERPENTINE
  #define MX_TILE_SERPENTINE    0        // 1 = every other row/column reversed
#endif
#ifndef MX_TILE_ORIGIN
  #define MX_TILE_ORIGIN        0        // 0=top left     1=top right
#endif                                   // 2=bottom left  3=bottom right
#ifndef MX_TILE_FLIP_ALT_ROWS
  #define MX_TILE_FLIP_ALT_ROWS 0        // 1 = every other tile row rotated 180 degrees
#endif
// Chaining SEVERAL tiles side by side: 0 = every tile row starts at the
// left again, 1 = serpentine (shorter cable with MX_TILES_X > 1).
#ifndef MX_TILE_ORDER_SERPENTINE
  #define MX_TILE_ORDER_SERPENTINE 0
#endif
#ifndef MX_STRANDS
  #define MX_STRANDS   1
#endif
#ifndef LED_DATA_PIN
  #define LED_DATA_PIN 6
#endif
#ifndef MX_BRIGHTNESS_DEFAULT
  #define MX_BRIGHTNESS_DEFAULT 40
#endif
#ifndef MX_MAX_MILLIAMPS
  #define MX_MAX_MILLIAMPS      450
#endif

// ---------------------------------------------------------------------------
// 3) Current / brightness
// ---------------------------------------------------------------------------
// The power limit makes FastLED throttle the brightness automatically so
// the configured milliamps are not exceeded.
// MX_MAX_MILLIAMPS = 0 disables the limit.
#define MX_PSU_VOLTS            5

// ---------------------------------------------------------------------------
// 4) Timing / protocol
// ---------------------------------------------------------------------------
// Must match the host side (-b or the MX8_BAUD environment variable). The host
// does try the usual rates by itself, though, if no READY arrives.
//
// On boards with native USB (Pico, ESP32-S2/S3, Teensy) the value DOESN'T
// MATTER -- USB-CDC always runs at full speed there (12 Mbit/s Full Speed),
// whatever baud rate host and firmware report. A higher number buys you
// nothing there; the bottleneck is the WS2812B output (PROTOCOL.md §6).
//
// On UART boards the value really counts. The classic Nano with a CH340
// reliably manages 500000; 1000000 doesn't work on every converter.
#ifndef MX_SERIAL_BAUD
#define MX_SERIAL_BAUD          115200UL
#endif
#define MX_BOOT_WINDOW_MS       5000UL  // boot window after reset
#define MX_READY_INTERVAL_MS    1000UL  // "I'm ready" interval
// Once the PC has been in control and then goes silent: after this time the
// display goes BLACK -- the demo is not restarted. A test pattern springing
// back to life would look like normal operation and hide the fault.
#define MX_PC_TIMEOUT_MS        60000UL // 1 min without FRAME -> dark
#define MX_RX_FRAME_TIMEOUT_MS  250UL   // half a frame -> parser resync

// Send debug LOG frames (0x84). Set to 0 for production use.
#define MX_ENABLE_LOG           1

// ---------------------------------------------------------------------------
// 5) Derived constants -- do not change
// ---------------------------------------------------------------------------
#define MX_WIDTH            (MX_TILE_W * MX_TILES_X)
#define MX_HEIGHT           (MX_TILE_H * MX_TILES_Y)
#define MX_TILE_LEDS        (MX_TILE_W * MX_TILE_H)
#define MX_NUM_TILES        (MX_TILES_X * MX_TILES_Y)
#define MX_NUM_LEDS         (MX_WIDTH * MX_HEIGHT)
#define MX_FRAME_BYTES      (MX_NUM_LEDS * 3)
#define MX_MAX_PAYLOAD      (MX_FRAME_BYTES + 8)
#define MX_LEDS_PER_STRAND  (MX_NUM_LEDS / MX_STRANDS)

// READY reports the geometry as one byte each -> 255 is the hard limit.
#if MX_WIDTH > 255 || MX_HEIGHT > 255
#error "MX_WIDTH/MX_HEIGHT > 255 -- the READY token only has one byte for that"
#endif

// Protocol limit: LEN is 16 bits, the receive buffer lives in RAM.
#if MX_MAX_PAYLOAD > MX_PROTO_MAX_PAYLOAD
#error "Frame too large -- raise MX_PROTO_MAX_PAYLOAD in mx8_protocol.h"
#endif

// A strand boundary must fall on a tile boundary, otherwise one data pin
// ends up driving two half panels.
#if (MX_NUM_TILES % MX_STRANDS) != 0
#error "MX_STRANDS must divide MX_NUM_TILES"
#endif
#if MX_STRANDS > 4
#error "More than 4 strands are not wired up in FW.ino"
#endif
#if defined(LED_CLOCK_PIN) && MX_STRANDS > 1
#error "Clocked chipsets (LED_CLOCK_PIN) only with MX_STRANDS 1"
#endif

#if defined(__AVR__) && MX_NUM_LEDS > 256
#warning "AVR with >256 LEDs: RAM gets tight and show() blocks for long -- use an RP2040"
#endif

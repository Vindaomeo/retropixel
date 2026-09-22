// ===========================================================================
//  mx8_map.h  --  pixel mapping (x,y) -> index in the LED chain
//
//  Deliberately free of Arduino dependencies, so that exactly this code also
//  compiles on the PC and can be tested against the Python reference
//  (tests/c_map_test.c). Firmware and test thus share the same
//  calculation instead of a copy of it -- just like mx8_rx.h.
//
//  Host coordinates: row 0 = top, column 0 = left, row-major.
//
//  Three stages:
//    1. Which tile?                 -> tile number in the chaining
//    2. Coordinate within that tile -> local index 0 .. MX_TILE_LEDS-1
//    3. Add the tile offset
//
//  The tile number doubles as the strand assignment: strand s serves a
//  contiguous block of MX_LEDS_PER_STRAND chain indices.
// ===========================================================================
#pragma once
#include "mx8_config.h"

static inline uint16_t xyToIndex(uint8_t x, uint8_t y) {
  // --- 1) Determine the tile --------------------------------------------
  uint8_t tx = (uint8_t)(x / MX_TILE_W);
  uint8_t ty = (uint8_t)(y / MX_TILE_H);
  uint8_t lx = (uint8_t)(x - tx * MX_TILE_W);
  uint8_t ly = (uint8_t)(y - ty * MX_TILE_H);

#if MX_TILE_ORDER_SERPENTINE
  // Tiles chained in serpentine order: tile row 0 left->right,
  // tile row 1 right->left, ...
  const uint8_t tcol = (ty & 0x01) ? (uint8_t)(MX_TILES_X - 1 - tx) : tx;
#else
  const uint8_t tcol = tx;
#endif
  const uint16_t tile = (uint16_t)ty * MX_TILES_X + tcol;

  // --- 2) Normalize the local coordinate --------------------------------
#if MX_TILE_FLIP_ALT_ROWS
  if (ty & 0x01) {                       // tile mounted rotated 180 degrees
    lx = (uint8_t)(MX_TILE_W - 1 - lx);
    ly = (uint8_t)(MX_TILE_H - 1 - ly);
  }
#endif

#if MX_TILE_ORIGIN == 1                  // first pixel top right
  lx = (uint8_t)(MX_TILE_W - 1 - lx);
#elif MX_TILE_ORIGIN == 2                // bottom left
  ly = (uint8_t)(MX_TILE_H - 1 - ly);
#elif MX_TILE_ORIGIN == 3                // bottom right
  lx = (uint8_t)(MX_TILE_W - 1 - lx);
  ly = (uint8_t)(MX_TILE_H - 1 - ly);
#endif

  // --- 3) Local coordinate -> local chain index ------------------------
#if MX_TILE_VERTICAL
  // Chain runs column by column: all of column 0, then column 1, ...
  #if MX_TILE_SERPENTINE
  if (lx & 0x01) ly = (uint8_t)(MX_TILE_H - 1 - ly);   // odd columns reversed
  #endif
  const uint16_t local = (uint16_t)lx * MX_TILE_H + ly;
#else
  // Chain runs row by row
  #if MX_TILE_SERPENTINE
  if (ly & 0x01) lx = (uint8_t)(MX_TILE_W - 1 - lx);   // odd rows reversed
  #endif
  const uint16_t local = (uint16_t)ly * MX_TILE_W + lx;
#endif

  return (uint16_t)(tile * MX_TILE_LEDS + local);
}

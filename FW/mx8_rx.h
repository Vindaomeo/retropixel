// ===========================================================================
//  mx8_rx.h  --  receive state machine, hardware-free
//
//  Deliberately free of Arduino dependencies: no millis(), no Serial. The time
//  comes in as a parameter. That way exactly this code can be compiled on the
//  PC and tested against the Python implementation
//  (tests/c_parser_test.c) -- so the firmware and the test really share
//  the same logic instead of a copy of it.
// ===========================================================================
#pragma once
#include "mx8_protocol.h"

#ifndef MX_MAX_PAYLOAD
#define MX_MAX_PAYLOAD 200
#endif
#ifndef MX_RX_FRAME_TIMEOUT_MS
#define MX_RX_FRAME_TIMEOUT_MS 250UL
#endif

typedef void (*mx_msg_cb)(uint8_t cmd, const uint8_t *data, uint16_t len, void *ctx);
typedef void (*mx_err_cb)(uint8_t cmd, uint8_t err, void *ctx);

typedef enum {
  MXRX_SYNC0, MXRX_SYNC1, MXRX_CMD, MXRX_LEN_LO,
  MXRX_LEN_HI, MXRX_DATA, MXRX_CRC_LO, MXRX_CRC_HI
} MxRxState;

typedef struct {
  MxRxState state;
  uint8_t   cmd;
  uint16_t  len;
  uint16_t  idx;
  uint16_t  crc_calc;
  uint16_t  crc_recv;
  uint32_t  start_ms;
  uint8_t   drop;                        // payload too long -> only keep the CRC going
  uint8_t   buf[MX_MAX_PAYLOAD];
  mx_msg_cb on_msg;
  mx_err_cb on_err;
  void     *ctx;
} MxRx;

static inline void mxrx_reset(MxRx *r) {
  r->state = MXRX_SYNC0;
  r->drop  = 0;
  r->idx   = 0;
}

static inline void mxrx_init(MxRx *r, mx_msg_cb on_msg, mx_err_cb on_err, void *ctx) {
  memset(r, 0, sizeof(*r));
  r->on_msg = on_msg;
  r->on_err = on_err;
  r->ctx    = ctx;
  mxrx_reset(r);
}

// Feed in one received byte. ``now_ms`` = current milliseconds.
static inline void mxrx_byte(MxRx *r, uint8_t b, uint32_t now_ms) {
  switch (r->state) {

    case MXRX_SYNC0:
      if (b == MX_SYNC0) { r->state = MXRX_SYNC1; r->start_ms = now_ms; }
      break;

    case MXRX_SYNC1:
      // A second 0xA5 is allowed -- it could be the real start.
      if      (b == MX_SYNC1) r->state = MXRX_CMD;
      else if (b == MX_SYNC0) r->state = MXRX_SYNC1;
      else                    r->state = MXRX_SYNC0;
      break;

    case MXRX_CMD:
      r->cmd      = b;
      r->crc_calc = mx_crc16_update(0xFFFF, b);
      r->state    = MXRX_LEN_LO;
      break;

    case MXRX_LEN_LO:
      r->len      = b;
      r->crc_calc = mx_crc16_update(r->crc_calc, b);
      r->state    = MXRX_LEN_HI;
      break;

    case MXRX_LEN_HI:
      r->len     |= (uint16_t)b << 8;
      r->crc_calc = mx_crc16_update(r->crc_calc, b);
      r->idx      = 0;
      r->drop     = (r->len > MX_MAX_PAYLOAD) ? 1 : 0;
      r->state    = r->len ? MXRX_DATA : MXRX_CRC_LO;
      break;

    case MXRX_DATA:
      r->crc_calc = mx_crc16_update(r->crc_calc, b);
      if (!r->drop) r->buf[r->idx] = b;
      if (++r->idx >= r->len) r->state = MXRX_CRC_LO;
      break;

    case MXRX_CRC_LO:
      r->crc_recv = b;
      r->state    = MXRX_CRC_HI;
      break;

    case MXRX_CRC_HI:
      r->crc_recv |= (uint16_t)b << 8;
      if (r->crc_recv != r->crc_calc) {
        if (r->on_err) r->on_err(r->cmd, ERR_CRC, r->ctx);
      } else if (r->drop) {
        if (r->on_err) r->on_err(r->cmd, ERR_TOO_LONG, r->ctx);
      } else {
        if (r->on_msg) r->on_msg(r->cmd, r->buf, r->len, r->ctx);
      }
      mxrx_reset(r);
      break;
  }
}

// Call regularly: frees the parser from a half-received frame.
static inline void mxrx_tick(MxRx *r, uint32_t now_ms) {
  if (r->state != MXRX_SYNC0 && (now_ms - r->start_ms) > MX_RX_FRAME_TIMEOUT_MS) {
    if (r->on_err) r->on_err(0xFF, ERR_RX_TIMEOUT, r->ctx);
    mxrx_reset(r);
  }
}

// ---------------------------------------------------------------------------
//  TX helper: builds a frame into a buffer. Returns the total length.
//  ``out`` needs at least MX_OVERHEAD_BYTES + len bytes.
// ---------------------------------------------------------------------------
static inline uint16_t mxtx_build(uint8_t *out, uint8_t cmd,
                                  const uint8_t *payload, uint16_t len) {
  out[0] = MX_SYNC0;
  out[1] = MX_SYNC1;
  out[2] = cmd;
  out[3] = (uint8_t)(len & 0xFF);
  out[4] = (uint8_t)(len >> 8);
  if (len) memcpy(out + 5, payload, len);
  uint16_t crc = mx_crc16(out + 2, (uint16_t)(3 + len));
  out[5 + len] = (uint8_t)(crc & 0xFF);
  out[6 + len] = (uint8_t)(crc >> 8);
  return (uint16_t)(MX_OVERHEAD_BYTES + len);
}

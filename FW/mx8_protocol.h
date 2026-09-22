// ===========================================================================
//  mx8_protocol.h  --  framing constants and CRC-16/CCITT-FALSE
//  Mirror image of script/mx8/protocol.py -- always change BOTH files!
// ===========================================================================
// Deliberately no <Arduino.h>: this way the header can also be built with a
// host compiler and tested against the Python side
// (see tests/).
#pragma once
#include <stdint.h>
#include <string.h>

// ---- Framing --------------------------------------------------------------
#define MX_SYNC0            0xA5
#define MX_SYNC1            0x5A
#define MX_HEADER_BYTES     5       // SYNC0 SYNC1 CMD LEN_LO LEN_HI
#define MX_OVERHEAD_BYTES   7       // + CRC_LO CRC_HI

// Largest allowed payload. LEN is 16 bits, so technically 65535 would work --
// the limit here mainly protects the static receive buffer in MCU RAM.
// 4096 covers 32x32 RGB (3072 B). If you need more: raise it here AND in
// script/mx8/protocol.py (MAX_PAYLOAD) together.
#define MX_PROTO_MAX_PAYLOAD 4096

// ---- Commands PC -> Nano --------------------------------------------------
#define CMD_FRAME           0x01
#define CMD_PING            0x02
#define CMD_SET_BRIGHTNESS  0x03
#define CMD_CLEAR           0x04
#define CMD_HELLO           0x05
#define CMD_RELEASE         0x06
// RAW writes straight into the LED chain without pixel mapping. Only meant for
// measuring the panel wiring (script/mapcheck.py), not for images.
// Payload: START_LO START_HI, then 3*n bytes RGB. Everything else goes black.
#define CMD_RAW             0x07

// ---- Commands Nano -> PC (bit 7 set) --------------------------------------
#define MSG_READY           0x81
#define MSG_ACK             0x82
#define MSG_NACK            0x83
#define MSG_LOG             0x84

// ---- NACK error codes -----------------------------------------------------
#define ERR_CRC             0x01
#define ERR_LENGTH          0x02
#define ERR_UNKNOWN_CMD     0x03
#define ERR_TOO_LONG        0x04
#define ERR_RX_TIMEOUT      0x05

// ---- Protocol / firmware version ------------------------------------------
#define MX_PROTO_VERSION    0x01
#define MX_FW_MAJOR         1
#define MX_FW_MINOR         0

// ---------------------------------------------------------------------------
//  CRC-16/CCITT-FALSE : poly 0x1021, init 0xFFFF, no reflect, no XorOut
//  Bitwise variant -- no 512-byte table needed, 199 bytes in ~120 us.
// ---------------------------------------------------------------------------
static inline uint16_t mx_crc16_update(uint16_t crc, uint8_t b) {
  crc ^= (uint16_t)b << 8;
  for (uint8_t i = 0; i < 8; i++) {
    crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

static inline uint16_t mx_crc16(const uint8_t *data, uint16_t len) {
  uint16_t crc = 0xFFFF;
  while (len--) crc = mx_crc16_update(crc, *data++);
  return crc;
}

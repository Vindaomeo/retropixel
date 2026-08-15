// ===========================================================================
//  mx8_protocol.h  --  Framing-Konstanten und CRC-16/CCITT-FALSE
//  Spiegelbild von host/mx8/protocol.py -- Aenderungen immer in BEIDEN Dateien!
// ===========================================================================
// Bewusst kein <Arduino.h>: so laesst sich dieser Header auch mit einem
// Host-Compiler uebersetzen und gegen die Python-Seite gegentesten
// (siehe tests/).
#pragma once
#include <stdint.h>
#include <string.h>

// ---- Framing --------------------------------------------------------------
#define MX_SYNC0            0xA5
#define MX_SYNC1            0x5A
#define MX_HEADER_BYTES     5       // SYNC0 SYNC1 CMD LEN_LO LEN_HI
#define MX_OVERHEAD_BYTES   7       // + CRC_LO CRC_HI

// ---- Kommandos PC -> Nano -------------------------------------------------
#define CMD_FRAME           0x01
#define CMD_PING            0x02
#define CMD_SET_BRIGHTNESS  0x03
#define CMD_CLEAR           0x04
#define CMD_HELLO           0x05
#define CMD_RELEASE         0x06

// ---- Kommandos Nano -> PC (Bit 7 gesetzt) ---------------------------------
#define MSG_READY           0x81
#define MSG_ACK             0x82
#define MSG_NACK            0x83
#define MSG_LOG             0x84

// ---- NACK-Fehlercodes -----------------------------------------------------
#define ERR_CRC             0x01
#define ERR_LENGTH          0x02
#define ERR_UNKNOWN_CMD     0x03
#define ERR_TOO_LONG        0x04
#define ERR_RX_TIMEOUT      0x05

// ---- Protokoll-/Firmware-Version -----------------------------------------
#define MX_PROTO_VERSION    0x01
#define MX_FW_MAJOR         1
#define MX_FW_MINOR         0

// ---------------------------------------------------------------------------
//  CRC-16/CCITT-FALSE : poly 0x1021, init 0xFFFF, kein Reflect, kein XorOut
//  Bitweise Variante -- keine 512-Byte-Tabelle noetig, 199 Byte in ~120 us.
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

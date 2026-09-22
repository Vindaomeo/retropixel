/* ===========================================================================
 *  c_parser_test.c -- host harness around the REAL firmware logic.
 *
 *  Compiles mx8_protocol.h and mx8_rx.h unmodified with a PC compiler
 *  and makes them reachable via stdin/stdout. tests/test_protocol.py
 *  compares the output byte for byte with the Python implementation.
 *
 *  build:  cc -O2 -Wall -Wextra -I../FW \
 *              c_parser_test.c -o c_parser_test
 *  usage:  ./c_parser_test crc   <hex>
 *          ./c_parser_test build <cmd_dec> <hex_payload>
 *          ./c_parser_test parse <hex_stream>
 * =========================================================================== */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Deliberately small and fixed: this checks the framing, not the
 * panel geometry. The real values come from mx8_config.h. */
#define MX_MAX_PAYLOAD 200
#include "mx8_protocol.h"
#include "mx8_rx.h"

/* -- Hex helpers --------------------------------------------------------- */
static int hex2bin(const char *hex, uint8_t *out, size_t cap) {
    size_t n = strlen(hex);
    if (n % 2) return -1;
    n /= 2;
    if (n > cap) return -1;
    for (size_t i = 0; i < n; i++) {
        unsigned v;
        if (sscanf(hex + 2 * i, "%2x", &v) != 1) return -1;
        out[i] = (uint8_t)v;
    }
    return (int)n;
}

static void print_hex(const uint8_t *d, uint16_t n) {
    for (uint16_t i = 0; i < n; i++) printf("%02x", d[i]);
}

/* -- Callbacks ----------------------------------------------------------- */
static void on_msg(uint8_t cmd, const uint8_t *data, uint16_t len, void *ctx) {
    (void)ctx;
    printf("MSG %u ", cmd);
    print_hex(data, len);
    printf("\n");
}

static void on_err(uint8_t cmd, uint8_t err, void *ctx) {
    (void)ctx;
    printf("ERR %u %u\n", cmd, err);
}

/* -- main ---------------------------------------------------------------- */
int main(int argc, char **argv) {
    static uint8_t buf[8192];

    if (argc < 2) {
        fprintf(stderr, "usage: %s crc|build|parse ...\n", argv[0]);
        return 2;
    }

    if (!strcmp(argv[1], "crc") && argc == 3) {
        int n = hex2bin(argv[2], buf, sizeof buf);
        if (n < 0) return 2;
        printf("%04x\n", mx_crc16(buf, (uint16_t)n));
        return 0;
    }

    if (!strcmp(argv[1], "build") && argc == 4) {
        static uint8_t payload[4096], out[8192];
        int n = hex2bin(argv[3], payload, sizeof payload);
        if (n < 0) return 2;
        uint16_t total = mxtx_build(out, (uint8_t)atoi(argv[2]), payload, (uint16_t)n);
        print_hex(out, total);
        printf("\n");
        return 0;
    }

    if (!strcmp(argv[1], "parse") && argc == 3) {
        int n = hex2bin(argv[2], buf, sizeof buf);
        if (n < 0) return 2;
        MxRx rx;
        mxrx_init(&rx, on_msg, on_err, NULL);
        for (int i = 0; i < n; i++) mxrx_byte(&rx, buf[i], 0);
        /* Timeout path: simulates "then nothing arrived for a long time". */
        mxrx_tick(&rx, MX_RX_FRAME_TIMEOUT_MS + 1);
        return 0;
    }

    fprintf(stderr, "bad arguments\n");
    return 2;
}

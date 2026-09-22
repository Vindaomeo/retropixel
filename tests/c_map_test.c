/* ===========================================================================
 *  c_map_test.c -- host harness around the firmware's REAL pixel mapping.
 *
 *  Compiles FW/mx8_map.h unmodified with a PC compiler and prints the
 *  complete mapping table. tests/test_protocol.py compares it cell by
 *  cell against script/mx8/mapping.py -- so the Python reference cannot
 *  drift away from the firmware.
 *
 *  The configuration comes from outside:
 *      cc -O2 -I../FW -DMX_BOARD=99 -DMX_TILE_W=32 -DMX_TILE_H=8 \
 *         -DMX_TILES_Y=2 -DMX_TILE_VERTICAL=1 -DMX_TILE_SERPENTINE=1 \
 *         c_map_test.c -o c_map_test
 *
 *  Output:
 *      line 1:   WIDTH HEIGHT NUM_LEDS TILE_LEDS LEDS_PER_STRAND
 *      then:     the chain indices for each image row, space-separated
 * =========================================================================== */

#include <stdio.h>
#include "mx8_map.h"

int main(void) {
    printf("%d %d %d %d %d\n",
           (int)MX_WIDTH, (int)MX_HEIGHT, (int)MX_NUM_LEDS,
           (int)MX_TILE_LEDS, (int)MX_LEDS_PER_STRAND);

    for (int y = 0; y < MX_HEIGHT; y++) {
        for (int x = 0; x < MX_WIDTH; x++) {
            printf("%u%s", (unsigned)xyToIndex((uint8_t)x, (uint8_t)y),
                   x + 1 == MX_WIDTH ? "" : " ");
        }
        printf("\n");
    }
    return 0;
}

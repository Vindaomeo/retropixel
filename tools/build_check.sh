#!/usr/bin/env bash
# ===========================================================================
#  build_check.sh -- build the firmware against REAL FastLED.
#
#  Fetches toolchain, Arduino core and FastLED and compiles FW.ino for both
#  board presets. No Arduino IDE needed; also runs on a bare Linux box
#  or in CI.
#
#      bash tools/build_check.sh            everything
#      bash tools/build_check.sh avr        AVR only (compile + link + size)
#      bash tools/build_check.sh pico       RP2040/RP2350 only (compile)
#
#  Working directory: $MX_BUILD_DIR (default /tmp/mx8-build). Once fetched,
#  it stays put -- the second run is fast.
# ===========================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="${MX_BUILD_DIR:-/tmp/mx8-build}"
WHAT="${1:-all}"
mkdir -p "$W"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# Only show messages from OUR files. FastLED-internal warnings are not our
# problem and would just clutter the result -- the continuation lines
# ("from ...") get filtered out along with them.
our_msgs() {
  grep -E "($HERE/(FW|tools)/[^:]+|/FW\.cpp):[0-9]+:" "$1" \
    | grep -vE "^ *(from|In file included from) " || true
}
fail=0

fetch() {   # fetch <target> <url> [commit]
  local dir="$W/$1" url="$2" commit="${3:-}"
  [ -d "$dir/.git" ] && return 0
  echo "  fetching $1 ..."
  git clone --depth 1 -q "$url" "$dir"
  if [ -n "$commit" ]; then
    git -C "$dir" fetch -q --depth 1 origin "$commit" && git -C "$dir" checkout -q FETCH_HEAD
  fi
}

# ---------------------------------------------------------------- FastLED --
fetch fastled https://github.com/FastLED/FastLED.git
FL="$W/fastled/src"

# -------------------------------------------------------------------- AVR --
build_avr() {   # build_avr <mcu> <variant> <MX_BOARD>
  local MCU=$1 VAR=$2 BOARD=$3
  local CORE="$W/core-avr/cores/arduino" VARI="$W/core-avr/variants/$2"
  local OUT="$W/out-avr-$MCU-b$BOARD"; rm -rf "$OUT"; mkdir -p "$OUT"
  local COMMON="-Os -ffunction-sections -fdata-sections -mmcu=$MCU -DF_CPU=16000000L
                -DARDUINO=10819 -DARDUINO_ARCH_AVR -DMX_BOARD=$BOARD
                -I$CORE -I$VARI -I$FL -I$HERE/FW"
  local CXX="-std=gnu++17 -fpermissive -fno-exceptions -fno-threadsafe-statics"

  for f in "$CORE"/*.c;   do avr-gcc -c $COMMON -std=gnu11 -w "$f" -o "$OUT/c_$(basename "$f").o"; done
  for f in "$CORE"/*.cpp; do avr-g++ -c $COMMON $CXX      -w "$f" -o "$OUT/x_$(basename "$f").o"; done
  for f in "$FL"/fl/build/*.cpp; do
    avr-g++ -c $COMMON $CXX -w "$f" -o "$OUT/fl_$(basename "$f" .cpp | tr '+.' '__').o"
  done

  cp "$HERE/FW/FW.ino" "$OUT/FW.cpp"
  set +e
  avr-g++ -c $COMMON $CXX -Wall -Wextra "$OUT/FW.cpp" -o "$OUT/FW.o" 2>"$OUT/log.txt"
  local rc=$?
  set -e
  # Only what comes from OUR files -- FastLED-internal warnings aren't ours.
  our_msgs "$OUT/log.txt"
  if [ $rc -ne 0 ]; then echo "  FAILED (compile)"; fail=1; return; fi

  avr-g++ $COMMON -Wl,--gc-sections -o "$OUT/FW.elf" "$OUT"/*.o -lm
  echo "  $MCU / MX_BOARD=$BOARD:  $(avr-size --mcu=$MCU -C "$OUT/FW.elf" |
        awk '/Program/{p=$2} /Data/{d=$2} END{print p" B flash, "d" B RAM"}')"
}

# ---------------------------------------------------------------- RP2040/50 --
build_pico() {   # build_pico <chip> <variant> <MX_BOARD> <flash-bytes> <mcu-flags...>
  local CHIP=$1 VAR=$2 BOARD=$3 FLASH=$4; shift 4
  local MCUFLAGS="$*" P="$W/core-pico"
  local OUT="$W/out-$CHIP-b$BOARD"; rm -rf "$OUT"; mkdir -p "$OUT"
  local FLAGS="-c $MCUFLAGS -ffunction-sections -fdata-sections -fno-exceptions
    -fno-rtti -std=gnu++17 -Os -Wno-psabi -Werror=return-type
    -DARDUINO=10607 -DARDUINO_ARCH_RP2040 -DBOARD_NAME=\"$VAR\"
    -DF_CPU=150000000L -DPICO_FLASH_SIZE_BYTES=$FLASH
    -DARDUINO_VARIANT=\"$VAR\"
    @$P/lib/platform_def.txt @$P/lib/$CHIP/platform_def.txt
    -iprefix $P/ @$P/lib/$CHIP/platform_inc.txt @$P/lib/core_inc.txt
    -I$P/cores/rp2040 -I$P/variants/$VAR -I$P/include -I$P/include/$CHIP
    -I$P/include/$CHIP/pico_base -I$FL -I$HERE/FW -DMX_BOARD=$BOARD"

  cp "$HERE/FW/FW.ino" "$OUT/FW.cpp"
  set +e
  arm-none-eabi-g++ $FLAGS -Wall -Wextra "$OUT/FW.cpp" -o "$OUT/FW.o" 2>"$OUT/log.txt"
  local rc=$?
  set -e
  our_msgs "$OUT/log.txt"
  if [ $rc -ne 0 ]; then
    echo "  FAILED (compile):"; grep -m4 error "$OUT/log.txt" || true; fail=1; return
  fi
  printf '  %-8s / %-9s / MX_BOARD=%s:  %s\n' "$CHIP" "$VAR" "$BOARD" \
    "$(arm-none-eabi-size "$OUT/FW.o" | awk 'NR==2{print $1" B text, "$3" B bss"}')"
}

if [ "$WHAT" = all ] || [ "$WHAT" = avr ]; then
  say "AVR -- real FastLED, compile + link"
  command -v avr-g++ >/dev/null || { echo "  avr-g++ missing: sudo apt-get install gcc-avr avr-libc"; exit 2; }
  fetch core-avr https://github.com/arduino/ArduinoCore-avr.git
  build_avr atmega328p eightanaloginputs 1     # classic Nano, 8x8
  build_avr atmega2560 mega             2      # 512 LEDs need more RAM than a 328p has
fi

if [ "$WHAT" = all ] || [ "$WHAT" = pico ]; then
  say "RP2040 / RP2350 -- real FastLED, compile"
  command -v arm-none-eabi-g++ >/dev/null || { echo "  arm-none-eabi-g++ missing: sudo apt-get install gcc-arm-none-eabi"; exit 2; }
  fetch core-pico https://github.com/earlephilhower/arduino-pico.git
  P="$W/core-pico"
  SDK_SHA=$(git -C "$P" ls-tree HEAD pico-sdk        | awk '{print $3}')
  API_SHA=$(git -C "$P" ls-tree HEAD ArduinoCore-API | awk '{print $3}')
  [ -L "$P/pico-sdk" ] || rm -rf "$P/pico-sdk"
  fetch pico-sdk https://github.com/raspberrypi/pico-sdk.git "$SDK_SHA"
  ln -sfn "$W/pico-sdk" "$P/pico-sdk"
  fetch core-pico/ArduinoCore-API https://github.com/earlephilhower/ArduinoCore-API.git "$API_SHA"
  fetch core-pico/lib/tlsf   https://github.com/earlephilhower/tlsf.git
  fetch pico-sdk/lib/lwip    https://github.com/lwip-tcpip/lwip.git
  fetch pico-sdk/lib/tinyusb https://github.com/hathach/tinyusb.git

  M0="-mcpu=cortex-m0plus -mthumb -march=armv6-m"
  M33="-mcpu=cortex-m33 -mthumb -march=armv8-m.main+fp+dsp -mfloat-abi=softfp -mcmse"
  build_pico rp2040 rpipico   1 2097152 $M0     # Pico     -- 8x8
  build_pico rp2040 rpipico   2 2097152 $M0     # Pico     -- 16x32
  build_pico rp2350 rpipico2w 1 4194304 $M33    # Pico 2 W -- 8x8
  build_pico rp2350 rpipico2w 2 4194304 $M33    # Pico 2 W -- 16x32
fi

say "Result"
[ $fail -eq 0 ] && echo "  all builds clean" || echo "  AT LEAST ONE BUILD FAILED"
exit $fail

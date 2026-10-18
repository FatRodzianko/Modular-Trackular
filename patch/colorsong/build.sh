#!/usr/bin/env bash
# Build colorsong into build/colorsong.{elf,bin,sym}.
# Variants: CS_DEFS='-DCOLORSONG_VERSION="p10" -DCOLORSONG_PASSTHROUGH' (diagnostic: hooks only call the originals).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# Toolchain: $ARM_GCC_BIN if set, else arm-none-eabi-gcc from PATH, else a toolchain
# unpacked next to this repo (../arm-gnu-toolchain-*/bin).
if [ -z "${ARM_GCC_BIN:-}" ]; then
    if command -v arm-none-eabi-gcc >/dev/null 2>&1; then
        ARM_GCC_BIN="$(dirname "$(command -v arm-none-eabi-gcc)")"
    else
        ARM_GCC_BIN="$(ls -d "$ROOT"/../arm-gnu-toolchain-*/bin 2>/dev/null | head -1 || true)"
    fi
fi
[ -x "$ARM_GCC_BIN/arm-none-eabi-gcc" ] || [ -x "$ARM_GCC_BIN/arm-none-eabi-gcc.exe" ] || {
    echo "arm-none-eabi-gcc not found: install the Arm GNU Toolchain or set ARM_GCC_BIN" >&2; exit 1; }
OUT="$HERE/build"
mkdir -p "$OUT"
CFLAGS=(-mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -Os -std=c11
        -mno-unaligned-access -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns -fno-common
        -ffunction-sections -Wall -Wextra -Werror ${CS_DEFS:-})
"$ARM_GCC_BIN/arm-none-eabi-gcc" "${CFLAGS[@]}" -c "$HERE/colorsong.c" -o "$OUT/colorsong.o"
"$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -c "$HERE/shim.S" -o "$OUT/shim.o"
"$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 \
    -nostdlib -nostartfiles -T "$HERE/link.ld" -Wl,--gc-sections \
    -Wl,--undefined=mt_usage_hook -Wl,--undefined=mt_color_hook_normal \
    -Wl,--undefined=mt_color_hook_flagged -Wl,--undefined=mt_version_fmt -Wl,--undefined=mt_song_enter -Wl,--undefined=mt_copy_hook -Wl,--undefined=mt_song_update -Wl,-Map="$OUT/colorsong.map" \
    "$OUT/shim.o" "$OUT/colorsong.o" -o "$OUT/colorsong.elf"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary "$OUT/colorsong.elf" "$OUT/colorsong.bin"
"$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/colorsong.elf" | grep -E ' [TtRr] ' > "$OUT/colorsong.sym"
"$ARM_GCC_BIN/arm-none-eabi-size" "$OUT/colorsong.elf"
cat "$OUT/colorsong.sym"

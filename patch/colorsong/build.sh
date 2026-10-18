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
# Python for tools/mkrel.py: $PYTHON if set, else the repo's .venv, else python3 / python.
if [ -z "${PYTHON:-}" ]; then
    for p in "$ROOT/.venv/Scripts/python" "$ROOT/.venv/bin/python" python3 python; do
        if command -v "$p" >/dev/null 2>&1; then PYTHON="$p"; break; fi
    done
fi
OUT="$HERE/build"
mkdir -p "$OUT"
CFLAGS=(-mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -Os -std=c11
        -mno-unaligned-access -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns -fno-common
        -ffunction-sections -Wall -Wextra -Werror ${CS_DEFS:-})
"$ARM_GCC_BIN/arm-none-eabi-gcc" "${CFLAGS[@]}" -c "$HERE/colorsong.c" -o "$OUT/colorsong.o"
"$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -c "$HERE/shim.S" -o "$OUT/shim.o"
link() {                               # link <linker script> <elf> <map>
    "$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 \
        -nostdlib -nostartfiles -T "$1" -Wl,--gc-sections \
        -Wl,--undefined=mt_usage_hook -Wl,--undefined=mt_color_hook_normal -Wl,--undefined=mt_color_hook_flagged -Wl,--undefined=mt_version_fmt -Wl,--undefined=mt_song_enter -Wl,--undefined=mt_copy_hook -Wl,--undefined=mt_song_update \
        -Wl,-Map="$3" "$OUT/shim.o" "$OUT/colorsong.o" -o "$2"
}
link "$HERE/link.ld" "$OUT/colorsong.elf" "$OUT/colorsong.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary "$OUT/colorsong.elf" "$OUT/colorsong.bin"
# Relocation table: link again 64 KB higher and compare, so tools/patch_ptf.py can place
# the patch anywhere (tools/mkrel.py).
BASE=$(sed -n 's/.*ORIGIN = \(0x[0-9A-Fa-f]*\).*/\1/p' "$HERE/link.ld")
SHIFT=0x10000
sed "s/ORIGIN = $BASE/ORIGIN = $(printf '0x%X' $((BASE + SHIFT)))/" "$HERE/link.ld" > "$OUT/link_shifted.ld"
link "$OUT/link_shifted.ld" "$OUT/colorsong_shifted.elf" "$OUT/colorsong_shifted.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary "$OUT/colorsong_shifted.elf" "$OUT/colorsong_shifted.bin"
"$PYTHON" "$ROOT/tools/mkrel.py" "$OUT/colorsong.bin" "$OUT/colorsong_shifted.bin" "$BASE" "$SHIFT" "$OUT/colorsong.rel"
"$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/colorsong.elf" | grep -E ' [TtRr] ' > "$OUT/colorsong.sym"
"$ARM_GCC_BIN/arm-none-eabi-size" "$OUT/colorsong.elf"
cat "$OUT/colorsong.sym"

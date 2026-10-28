#!/usr/bin/env bash
# Build tracklen into build/tracklen.{elf,bin,sym}.
# Variants: TL_DEFS='-DTRACKLEN_VERSION="tlX"' to change the version text.
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
        -ffunction-sections -Wall -Wextra -Werror ${TL_DEFS:-})
"$ARM_GCC_BIN/arm-none-eabi-gcc" "${CFLAGS[@]}" -c "$HERE/tracklen.c" -o "$OUT/tracklen.o"
REC_HOOKS=()                           # the recording hooks: 8 veneers, 18 note-off calls
for i in $(seq 1 8); do REC_HOOKS+=(-Wl,--undefined=mt_tl_rec_note$i); done
for i in $(seq 1 18); do REC_HOOKS+=(-Wl,--undefined=mt_tl_rec_off$i); done
link() {                               # link <linker script> <elf> <map>
    "$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 \
        -nostdlib -nostartfiles -T "$1" -Wl,--gc-sections \
        -Wl,--undefined=mt_tl_step_copy -Wl,--undefined=mt_tl_roll_a -Wl,--undefined=mt_tl_roll_b \
        -Wl,--undefined=mt_tl_bind -Wl,--undefined=mt_tl_label -Wl,--undefined=mt_tl_label2 \
        -Wl,--undefined=mt_tl_colors -Wl,--undefined=mt_tl_data -Wl,--undefined=mt_tl_jog \
        -Wl,--undefined=mt_tl_cell_note -Wl,--undefined=mt_tl_cell_instr3 -Wl,--undefined=mt_tl_cell_instr \
        -Wl,--undefined=mt_tl_cell_fx1 -Wl,--undefined=mt_tl_cell_fx2 \
        -Wl,--undefined=mt_tl_pattern_update -Wl,--undefined=mt_tl_config_update \
        -Wl,--undefined=mt_tl_save_name \
        "${REC_HOOKS[@]}" -Wl,--undefined=mt_version_fmt \
        -Wl,-Map="$3" "$OUT/tracklen.o" -o "$2"
}
link "$HERE/link.ld" "$OUT/tracklen.elf" "$OUT/tracklen.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary -j .text "$OUT/tracklen.elf" "$OUT/tracklen.bin"
# Relocation table: link again with the code 64 KB higher, and again with the RAM
# (.mt_ram) 64 KB higher, and compare, so tools/patch_ptf.py can place the code and
# the RAM anywhere (tools/mkrel.py).
BASE=$(sed -n 's/.*PATCH (rx) *: ORIGIN = \(0x[0-9A-Fa-f]*\).*/\1/p' "$HERE/link.ld")
SHIFT=0x10000
sed "s/ORIGIN = $BASE/ORIGIN = $(printf '0x%X' $((BASE + SHIFT)))/" "$HERE/link.ld" > "$OUT/link_shifted.ld"
link "$OUT/link_shifted.ld" "$OUT/tracklen_shifted.elf" "$OUT/tracklen_shifted.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary -j .text "$OUT/tracklen_shifted.elf" "$OUT/tracklen_shifted.bin"
RAM=$(sed -n 's/.*RAM (rw) *: ORIGIN = \(0x[0-9A-Fa-f]*\).*/\1/p' "$HERE/link.ld")
sed "s/ORIGIN = $RAM/ORIGIN = $(printf '0x%X' $((RAM + SHIFT)))/" "$HERE/link.ld" > "$OUT/link_ram_shifted.ld"
link "$OUT/link_ram_shifted.ld" "$OUT/tracklen_ram_shifted.elf" "$OUT/tracklen_ram_shifted.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary -j .text "$OUT/tracklen_ram_shifted.elf" "$OUT/tracklen_ram_shifted.bin"
RAM_START=0x$("$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/tracklen.elf" | sed -n 's/^\([0-9a-f]*\) . __mt_ram_start$/\1/p')
RAM_END=0x$("$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/tracklen.elf" | sed -n 's/^\([0-9a-f]*\) . __mt_ram_end$/\1/p')
"$PYTHON" "$ROOT/tools/mkrel.py" "$OUT/tracklen.bin" "$OUT/tracklen_shifted.bin" "$BASE" "$SHIFT" "$OUT/tracklen.rel" \
    --ram "$OUT/tracklen_ram_shifted.bin" "$RAM_START" $((RAM_END - RAM_START))
"$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/tracklen.elf" | grep -E ' [TtRr] ' > "$OUT/tracklen.sym"
"$ARM_GCC_BIN/arm-none-eabi-size" "$OUT/tracklen.elf"
cat "$OUT/tracklen.sym"

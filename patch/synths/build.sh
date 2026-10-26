#!/usr/bin/env bash
# Build synths into build/synths.{elf,bin,sym}.
# Variants: SYN_DEFS='-DSYNTHS_VERSION="syX"' to change the version text.
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
CFLAGS=(-mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 -O2 -std=c11
        -mno-unaligned-access -ffreestanding -fno-builtin -fno-tree-loop-distribute-patterns -fno-common
        -ffunction-sections -ffp-contract=fast -Wall -Wextra -Werror ${SYN_DEFS:-})
# The wavetables, generated from formulas (gen_wavetables.py needs numpy): 7 banks of
# 66.5 KB and the super saw's saw.
"$PYTHON" "$HERE/gen_wavetables.py" "$OUT/wt_tables.h"
# PHASE's cosine table (4 KB)
"$PYTHON" "$HERE/gen_phase_tables.py" "$OUT/ph_tables.h"
"$ARM_GCC_BIN/arm-none-eabi-gcc" "${CFLAGS[@]}" -I"$OUT" -c "$HERE/synths.c" -o "$OUT/synths.o"
link() {                               # link <linker script> <elf> <map>
    "$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 \
        -nostdlib -nostartfiles -T "$1" -Wl,--gc-sections \
        -Wl,--undefined=mt_syn_voice_delete -Wl,--undefined=mt_syn_voice_midi -Wl,--undefined=mt_syn_voice_set_param \
        -Wl,--undefined=mt_syn_voice_render -Wl,--undefined=mt_syn_voice_post -Wl,--undefined=mt_syn_voice_sounding -Wl,--undefined=mt_syn_settings_folder \
        -Wl,--undefined=mt_syn_last_item -Wl,--undefined=mt_syn_pattern_update -Wl,--undefined=mt_syn_config_update \
        -Wl,--undefined=mt_version_fmt -Wl,--undefined=mt_syn_ram_map -Wl,--undefined=mt_syn_registry \
        -Wl,--undefined=mt_syn_project_records -Wl,--undefined=mt_syn_list_data -Wl,--undefined=mt_syn_list_images \
        -Wl,--undefined=mt_syn_patch_label -Wl,--undefined=mt_syn_patch_load -Wl,--undefined=mt_syn_tiles -Wl,--undefined=mt_syn_save_name \
        -Wl,-Map="$3" "$OUT/synths.o" -o "$2"
}
link "$HERE/link.ld" "$OUT/synths.elf" "$OUT/synths.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary -j .text "$OUT/synths.elf" "$OUT/synths.bin"
# Relocation table: link again with the code 64 KB higher, and again with the RAM
# (.mt_ram) 64 KB higher, and compare, so tools/patch_ptf.py can place the code and
# the RAM anywhere (tools/mkrel.py).
BASE=$(sed -n 's/.*PATCH (rx) *: ORIGIN = \(0x[0-9A-Fa-f]*\).*/\1/p' "$HERE/link.ld")
SHIFT=0x10000
sed "s/ORIGIN = $BASE/ORIGIN = $(printf '0x%X' $((BASE + SHIFT)))/" "$HERE/link.ld" > "$OUT/link_shifted.ld"
link "$OUT/link_shifted.ld" "$OUT/synths_shifted.elf" "$OUT/synths_shifted.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary -j .text "$OUT/synths_shifted.elf" "$OUT/synths_shifted.bin"
RAM=$(sed -n 's/.*RAM (rw) *: ORIGIN = \(0x[0-9A-Fa-f]*\).*/\1/p' "$HERE/link.ld")
sed "s/ORIGIN = $RAM/ORIGIN = $(printf '0x%X' $((RAM + SHIFT)))/" "$HERE/link.ld" > "$OUT/link_ram_shifted.ld"
link "$OUT/link_ram_shifted.ld" "$OUT/synths_ram_shifted.elf" "$OUT/synths_ram_shifted.map"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary -j .text "$OUT/synths_ram_shifted.elf" "$OUT/synths_ram_shifted.bin"
RAM_START=0x$("$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/synths.elf" | sed -n 's/^\([0-9a-f]*\) . __mt_ram_start$/\1/p')
RAM_END=0x$("$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/synths.elf" | sed -n 's/^\([0-9a-f]*\) . __mt_ram_end$/\1/p')
"$PYTHON" "$ROOT/tools/mkrel.py" "$OUT/synths.bin" "$OUT/synths_shifted.bin" "$BASE" "$SHIFT" "$OUT/synths.rel" \
    --ram "$OUT/synths_ram_shifted.bin" "$RAM_START" $((RAM_END - RAM_START))
"$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/synths.elf" | grep -E ' [TtRr] ' > "$OUT/synths.sym"
"$ARM_GCC_BIN/arm-none-eabi-size" "$OUT/synths.elf"
cat "$OUT/synths.sym"

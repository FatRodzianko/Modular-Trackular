#!/usr/bin/env bash
# Build humanize into build/humanize.{elf,bin,sym}.
# Variants: HZ_DEFS='-DHUMANIZE_VERSION="hzX"' to change the version text.
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
        -ffunction-sections -Wall -Wextra -Werror ${HZ_DEFS:-})
"$ARM_GCC_BIN/arm-none-eabi-gcc" "${CFLAGS[@]}" -c "$HERE/humanize.c" -o "$OUT/humanize.o"
"$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -mfloat-abi=hard -mfpu=fpv5-d16 \
    -nostdlib -nostartfiles -T "$HERE/link.ld" -Wl,--gc-sections \
    -Wl,--undefined=mt_hz_bind -Wl,--undefined=mt_hz_label -Wl,--undefined=mt_hz_colors \
    -Wl,--undefined=mt_hz_data -Wl,--undefined=mt_version_fmt \
    -Wl,--undefined=mt_hz_settings_folder -Wl,--undefined=mt_hz_last_item \
    -Wl,--undefined=mt_hz_pattern_update -Wl,--undefined=mt_hz_config_update -Wl,-Map="$OUT/humanize.map" \
    "$OUT/humanize.o" -o "$OUT/humanize.elf"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary "$OUT/humanize.elf" "$OUT/humanize.bin"
"$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/humanize.elf" | grep -E ' [TtRr] ' > "$OUT/humanize.sym"
"$ARM_GCC_BIN/arm-none-eabi-size" "$OUT/humanize.elf"
cat "$OUT/humanize.sym"

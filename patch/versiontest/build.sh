#!/usr/bin/env bash
# Build versiontest into build/versiontest.{elf,bin,sym}.
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
"$ARM_GCC_BIN/arm-none-eabi-gcc" -mcpu=cortex-m7 -mthumb -c "$HERE/marker.S" -o "$OUT/marker.o"
"$ARM_GCC_BIN/arm-none-eabi-ld" -Ttext=0x601BB100 --section-start=.rodata=0x601BB100 \
    -e 0 "$OUT/marker.o" -o "$OUT/versiontest.elf"
"$ARM_GCC_BIN/arm-none-eabi-objcopy" -O binary -j .rodata "$OUT/versiontest.elf" "$OUT/versiontest.bin"
"$ARM_GCC_BIN/arm-none-eabi-nm" "$OUT/versiontest.elf" | grep ' mt_' > "$OUT/versiontest.sym"
cat "$OUT/versiontest.sym"
xxd "$OUT/versiontest.bin"

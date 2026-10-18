#!/usr/bin/env bash
# Run Ghidra headless against this repo's project.
#   tools/ghidra.sh import <version>          import work/tp_<version>.bin and auto-analyse
#   tools/ghidra.sh run <version> <Script.java> [args...]   run a script on the analysed program
# Set GHIDRA_HOME to your Ghidra install (default: a ghidra_*_PUBLIC folder next to
# this repo). JAVA_HOME is passed through if set; otherwise Ghidra finds Java itself.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${GHIDRA_HOME:-}" ]; then
    GHIDRA_HOME="$(ls -d "$ROOT"/../ghidra_*_PUBLIC*/ghidra_*_PUBLIC "$ROOT"/../ghidra_*_PUBLIC 2>/dev/null | head -1 || true)"
fi
[ -n "${JAVA_HOME:-}" ] && export JAVA_HOME
HEADLESS="$GHIDRA_HOME/support/analyzeHeadless"
[ -f "$HEADLESS.bat" ] && HEADLESS="$HEADLESS.bat"           # Windows
[ -e "$HEADLESS" ] || { echo "Ghidra not found: set GHIDRA_HOME" >&2; exit 1; }
PROJ="$ROOT/ghidra/projects"
SCRIPTS="$ROOT/ghidra/scripts"
mkdir -p "$PROJ"
cmd="$1"; ver="$2"; shift 2
case "$cmd" in
  import)
    "$HEADLESS" "$PROJ" TrackerPlus -import "$ROOT/work/tp_$ver.bin" \
      -loader BinaryLoader -loader-baseAddr 0x60030000 -processor ARM:LE:32:Cortex \
      -scriptPath "$SCRIPTS" -preScript SetupTrackerPlus.java -overwrite \
      -analysisTimeoutPerFile 3600
    ;;
  run)
    script="$1"; shift
    "$HEADLESS" "$PROJ" TrackerPlus -process "tp_$ver.bin" -noanalysis \
      -scriptPath "$SCRIPTS" -postScript "$script" "$@"
    ;;
  *) echo "unknown command $cmd"; exit 1 ;;
esac

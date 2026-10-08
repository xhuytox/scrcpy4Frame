#!/usr/bin/env bash
# Run scrcpy4Frame from source on a Linux or macOS desktop, without Flatpak.
# Uses a project venv plus tools from PATH or the Android SDK. Override them with
# SCRCPY4FRAME_ADB, SCRCPY4FRAME_SCRCPY, SCRCPY4FRAME_VR_TOAST and SCRCPY4FRAME_COMPANION_APK.
# Set SCRCPY4FRAME_NO_USB=1 to keep the app's adb server away from phones plugged in for other
# work (no "Allow USB debugging?" prompts for its key).
#
#   scripts/run-dev.sh              # windowed app
#   scripts/run-dev.sh --game-mode  # fullscreen, hides itself while mirroring (like on the Frame)
#   scripts/run-dev.sh cli discover # headless diagnostics (see src/scrcpy4frame/cli.py)
#   scripts/run-dev.sh --test       # run the test suite
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creating $VENV…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
fi
if ! "$VENV/bin/python" -c "import PySide6, zeroconf, jeepney, pytest" 2>/dev/null; then
  "$VENV/bin/pip" install -q "PySide6-Essentials>=6.9" "zeroconf>=0.130" "jeepney>=0.8" "pytest>=8"
fi

if [[ "${1:-}" == "--test" ]]; then
  shift
  exec "$VENV/bin/python" -m pytest "$REPO/tests" "$@"
fi

if ! command -v scrcpy >/dev/null && [[ -z "${SCRCPY4FRAME_SCRCPY:-}" ]]; then
  echo "note: scrcpy isn't installed, so mirroring won't start (macOS: brew install scrcpy)." >&2
fi
if [[ ! -f "$REPO/dist/companion.apk" && -z "${SCRCPY4FRAME_COMPANION_APK:-}" ]]; then
  echo "note: dist/companion.apk is missing, so the companion offer is skipped (build it with scripts/build-companion.sh)." >&2
fi

export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m scrcpy4frame "$@"

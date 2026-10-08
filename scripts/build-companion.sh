#!/usr/bin/env bash
# Builds the scrcpy4Frame companion APK and copies it to <repo>/dist/companion.apk.
# Usage: scripts/build-companion.sh [extra gradle args...]   (works from any directory)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPANION_DIR="$REPO_ROOT/companion"
DIST_DIR="$REPO_ROOT/dist"
APK_SRC="$COMPANION_DIR/app/build/outputs/apk/release/app-release.apk"
APK_DST="$DIST_DIR/companion.apk"

die() { echo "build-companion: $*" >&2; exit 1; }

find_sdk() {
  local candidate
  for candidate in "${ANDROID_HOME:-}" "$HOME/Library/Android/sdk" "$HOME/Android/Sdk"; do
    if [[ -n "$candidate" && -d "$candidate/platforms" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}
SDK_DIR="$(find_sdk)" || die "Android SDK not found (set ANDROID_HOME)"
export ANDROID_HOME="$SDK_DIR"

# Gradle needs JDK 17+. Without JAVA_HOME or java on PATH, use Android Studio's bundled JBR.
if [[ -z "${JAVA_HOME:-}" ]] && ! java -version >/dev/null 2>&1; then
  for jbr in \
    "/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
    "$HOME/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
    "/opt/android-studio/jbr" \
    "$HOME/android-studio/jbr"; do
    if [[ -x "$jbr/bin/java" ]]; then
      export JAVA_HOME="$jbr"
      break
    fi
  done
fi

echo "build-companion: Android SDK: $ANDROID_HOME"
[[ -n "${JAVA_HOME:-}" ]] && echo "build-companion: JAVA_HOME: $JAVA_HOME"

rm -f "$APK_SRC"
(cd "$COMPANION_DIR" && ./gradlew --console=plain :app:assembleRelease "$@")
[[ -f "$APK_SRC" ]] || die "Gradle finished but $APK_SRC is missing"

mkdir -p "$DIST_DIR"
cp "$APK_SRC" "$APK_DST"

if command -v sha256sum >/dev/null 2>&1; then
  SHA256="$(sha256sum "$APK_DST" | awk '{print $1}')"
else
  SHA256="$(shasum -a 256 "$APK_DST" | awk '{print $1}')"
fi

echo "build-companion: APK:    $APK_DST"
echo "build-companion: sha256: $SHA256"

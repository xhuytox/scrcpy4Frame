#!/usr/bin/env bash
# Build dist/scrcpy4frame-aarch64.flatpak for a release: set the versions, rebuild the companion APK,
# run the tests, then build the bundle on the Frame and check it. See -h.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT
readonly APP_ID="io.github.xhuytox.Scrcpy4Frame"
readonly PYPROJECT="pyproject.toml"
readonly INIT_PY="src/scrcpy4frame/__init__.py"
readonly METAINFO="flatpak/data/$APP_ID.metainfo.xml"
readonly GRADLE="companion/app/build.gradle.kts"
readonly APK="dist/companion.apk"
readonly BUNDLE="dist/scrcpy4frame-aarch64.flatpak"

usage() {
  cat <<EOF
Usage: $(basename "$0") [<ssh-host>]

Asks for the version to release, the new companion version (blank keeps the current one, for
either) and, if not given, the Frame's ssh host. It checks that it can ssh to the Frame, then:
  1. sets the version in $PYPROJECT, $INIT_PY and a new <release> in $METAINFO;
     with a companion version, sets versionName in $GRADLE and adds 1 to versionCode
  2. deletes $APK and rebuilds it with scripts/build-companion.sh
  3. runs the tests (scripts/run-dev.sh --test)
  4. builds $BUNDLE on the Frame: scripts/frame-build.sh --bundle --no-install <ssh-host>
  5. checks the bundle: the version in the build on the Frame, the companion APK it contains,
     and prints its size and sha256

It doesn't install anything on the Frame.

Arguments:
  <ssh-host>   ssh destination of the Frame, e.g. <user>@<frame-host-or-ip> or an ~/.ssh/config
               alias (default: \$FRAME_HOST; asked for if neither is set)

Environment:
  FRAME_HOST     ssh destination of the Frame, if no <ssh-host> is given
  FRAME_WORKDIR  work directory on the Frame, passed on to frame-build.sh (default: scrcpy4frame-build)

The Frame needs Developer Mode on, with a password set so SSH works. With only the password,
it's asked for twice: by this script and by frame-build.sh. To avoid that, install an SSH key on
the Frame once: ssh-copy-id <ssh-host>

Running it again with the same versions (or blank) doesn't bump them again.
EOF
}

die() { echo "release-build: error: $*" >&2; exit 1; }
log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { echo "    $*"; }

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# Succeeds if version $1 is lower than version $2 (both X.Y.Z).
version_lt() {
  local IFS=.
  local -a a=($1) b=($2)
  local i
  for i in 0 1 2; do
    ((10#${a[i]} < 10#${b[i]})) && return 0
    ((10#${a[i]} > 10#${b[i]})) && return 1
  done
  return 1
}

# Rewrite file $1 with sed expression $2 (portable, unlike sed -i).
sed_file() {
  local tmp
  tmp="$(mktemp)"
  sed -E "$2" "$1" >"$tmp"
  cat "$tmp" >"$1"
  rm -f "$tmp"
}

# Print the first <release> version in the build's metainfo and the sha256 of the companion APK
# in the build, read on the Frame from build-dir $1.
# shellcheck disable=SC2029  # arguments are quoted locally with printf %q
read_build() {
  ssh "${SSH_OPTS[@]}" "$HOST" "bash -s -- $(printf '%q ' "$1" "$APP_ID")" <<'REMOTE'
set -euo pipefail
files="$1/files"; app_id="$2"
sed -n '/<release version=/{s/.*<release version="\([^"]*\)".*/\1/p;q;}' "$files/share/metainfo/$app_id.metainfo.xml"
sha256sum "$files/share/scrcpy4frame/companion.apk" | awk '{print $1}'
REMOTE
}

# Run a command on the Frame without reading this script's stdin.
# shellcheck disable=SC2029
remote() { ssh -n "${SSH_OPTS[@]}" "$HOST" "$@"; }

HOST="${FRAME_HOST:-}"
while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option: $1 (see -h)" ;;
    *) [[ -z "${HOST_ARG:-}" ]] || die "unexpected argument: $1"; HOST_ARG="$1" ;;
  esac
  shift
done
HOST="${HOST_ARG:-$HOST}"
WORKDIR="${FRAME_WORKDIR:-scrcpy4frame-build}"

for tool in ssh sed awk; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool not found"
done
cd "$REPO_ROOT"

CURRENT="$(sed -nE 's/^version = "([^"]*)"/\1/p' "$PYPROJECT")"
[[ -n "$CURRENT" ]] || die "no version found in $PYPROJECT"

read -r -p "Version to release (current: $CURRENT, blank to keep it): " VERSION || true
VERSION="${VERSION#v}"
VERSION="${VERSION:-$CURRENT}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "'$VERSION' isn't a version like 1.2.3"
version_lt "$VERSION" "$CURRENT" && die "$VERSION is lower than the current version $CURRENT"
TODAY="$(date +%Y-%m-%d)"

COMPANION_CURRENT="$(sed -nE 's/^[[:space:]]*versionName = "([^"]*)".*/\1/p' "$GRADLE")"
COMPANION_CODE="$(sed -nE 's/^[[:space:]]*versionCode = ([0-9]+).*/\1/p' "$GRADLE")"
[[ -n "$COMPANION_CURRENT" && -n "$COMPANION_CODE" ]] || die "no versionName/versionCode found in $GRADLE"
read -r -p "Companion version (current: $COMPANION_CURRENT, blank to keep it): " COMPANION_VERSION || true
COMPANION_VERSION="${COMPANION_VERSION#v}"
if [[ -n "$COMPANION_VERSION" ]]; then
  [[ "$COMPANION_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    die "'$COMPANION_VERSION' isn't a version like 1.2.3"
  version_lt "$COMPANION_VERSION" "$COMPANION_CURRENT" &&
    die "$COMPANION_VERSION is lower than the current companion version $COMPANION_CURRENT"
fi

if [[ -z "$HOST" ]]; then
  read -r -p "Frame ssh host (<user>@<frame-host-or-ip> or an ~/.ssh/config alias): " HOST || true
  [[ -n "$HOST" ]] || die "no ssh host given (argument, FRAME_HOST or the prompt)"
fi
# Share one SSH connection so a password is asked for at most once by this script.
SSH_CTL_DIR="$(mktemp -d /tmp/s4f-release.XXXXXX)"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$SSH_CTL_DIR/%C" -o ControlPersist=yes
          -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=6)
cleanup() {
  ssh "${SSH_OPTS[@]}" -O exit "$HOST" >/dev/null 2>&1 || true
  rm -rf "$SSH_CTL_DIR"
}
trap cleanup EXIT

log "Checking the Frame"
info "Connecting to $HOST"
REMOTE_ARCH="$(remote 'uname -m')" || die "can't ssh to $HOST. Check the host, and that Developer Mode
is on with a password set on the Frame (see -h)"
[[ "$REMOTE_ARCH" == aarch64 ]] || die "$HOST is $REMOTE_ARCH, not aarch64 like the Steam Frame"
remote 'command -v flatpak' >/dev/null || die "flatpak isn't installed on $HOST"
info "$HOST is reachable ($REMOTE_ARCH, flatpak installed)"

echo
if [[ "$VERSION" == "$CURRENT" ]]; then
  echo "Version:   unchanged, $VERSION"
else
  echo "Version:   $VERSION (now $CURRENT)"
fi
if [[ -n "$COMPANION_VERSION" && "$COMPANION_VERSION" != "$COMPANION_CURRENT" ]]; then
  echo "Companion: $COMPANION_VERSION, versionCode $((COMPANION_CODE + 1)) (now $COMPANION_CURRENT, versionCode $COMPANION_CODE)"
else
  echo "Companion: unchanged, $COMPANION_CURRENT (versionCode $COMPANION_CODE)"
fi
echo "This sets the versions, rebuilds the companion APK, runs the tests and builds $BUNDLE on $HOST."
read -r -p "Continue? [y/N] " ANSWER || true
[[ "$ANSWER" == [yY] || "$ANSWER" == [yY][eE][sS] ]] || die "cancelled"

log "[1/5] Setting the versions"
if grep -qx "version = \"$VERSION\"" "$PYPROJECT"; then
  info "$PYPROJECT: keeping version $VERSION"
else
  info "$PYPROJECT: version $VERSION"
  sed_file "$PYPROJECT" "s/^version = \"[^\"]*\"/version = \"$VERSION\"/"
  grep -qx "version = \"$VERSION\"" "$PYPROJECT" || die "couldn't set the version in $PYPROJECT"
fi
if grep -qx "__version__ = \"$VERSION\"" "$INIT_PY"; then
  info "$INIT_PY: keeping __version__ $VERSION"
else
  info "$INIT_PY: __version__ $VERSION"
  sed_file "$INIT_PY" "s/^__version__ = \"[^\"]*\"/__version__ = \"$VERSION\"/"
  grep -qx "__version__ = \"$VERSION\"" "$INIT_PY" || die "couldn't set __version__ in $INIT_PY"
fi
if grep -q "<release version=\"$VERSION\"" "$METAINFO"; then
  info "$METAINFO: keeping <release version=\"$VERSION\">"
else
  info "$METAINFO: adding <release version=\"$VERSION\" date=\"$TODAY\"/>"
  tmp="$(mktemp)"
  awk -v v="$VERSION" -v d="$TODAY" '
    { print }
    /^[[:space:]]*<releases>[[:space:]]*$/ && !done {
      match($0, /^[[:space:]]*/)
      printf "%s  <release version=\"%s\" date=\"%s\"/>\n", substr($0, 1, RLENGTH), v, d
      done = 1
    }' "$METAINFO" >"$tmp"
  cat "$tmp" >"$METAINFO"
  rm -f "$tmp"
  grep -q "<release version=\"$VERSION\"" "$METAINFO" || die "no <releases> element in $METAINFO"
fi
if [[ -z "$COMPANION_VERSION" ]]; then
  info "$GRADLE: keeping companion $COMPANION_CURRENT (versionCode $COMPANION_CODE)"
elif [[ "$COMPANION_VERSION" == "$COMPANION_CURRENT" ]]; then
  info "$GRADLE: companion is already $COMPANION_VERSION (versionCode $COMPANION_CODE)"
else
  COMPANION_CODE=$((COMPANION_CODE + 1))
  info "$GRADLE: versionName $COMPANION_VERSION, versionCode $COMPANION_CODE"
  sed_file "$GRADLE" "s/^([[:space:]]*versionName = )\"[^\"]*\"/\\1\"$COMPANION_VERSION\"/"
  sed_file "$GRADLE" "s/^([[:space:]]*versionCode = )[0-9]+/\\1$COMPANION_CODE/"
  grep -qE "^[[:space:]]*versionName = \"$COMPANION_VERSION\"" "$GRADLE" &&
    grep -qE "^[[:space:]]*versionCode = $COMPANION_CODE\$" "$GRADLE" ||
    die "couldn't set the companion version in $GRADLE"
fi

log "[2/5] Rebuilding the companion APK"
info "Deleting $APK"
rm -f "$APK"
info "Running scripts/build-companion.sh"
"$REPO_ROOT/scripts/build-companion.sh"
[[ -f "$APK" ]] || die "scripts/build-companion.sh did not produce $APK"
APK_SHA="$(sha256 "$APK")"
info "$APK sha256: $APK_SHA"

log "[3/5] Running the tests"
info "Running scripts/run-dev.sh --test"
"$REPO_ROOT/scripts/run-dev.sh" --test || die "tests failed. Fix them and run this script again"

log "[4/5] Building the Flatpak bundle on $HOST"
info "Deleting the old $BUNDLE"
rm -f "$BUNDLE"
info "Running scripts/frame-build.sh --bundle --no-install $HOST"
"$REPO_ROOT/scripts/frame-build.sh" --bundle --no-install "$HOST"
[[ -s "$BUNDLE" ]] || die "scripts/frame-build.sh did not produce $BUNDLE"

log "[5/5] Checking the bundle"
case "$WORKDIR" in
  "~/"*) REMOTE_WORK="${WORKDIR#\~/}" ;;
  *) REMOTE_WORK="$WORKDIR" ;;   # absolute, or relative to the Frame's $HOME like ssh commands
esac
info "Reading the build on $HOST:$REMOTE_WORK/build-dir"
REMOTE_OUT="$(read_build "$REMOTE_WORK/build-dir")" || die "couldn't read the build on $HOST"
BUILT_VERSION="$(sed -n 1p <<<"$REMOTE_OUT")"
BUILT_APK_SHA="$(sed -n 2p <<<"$REMOTE_OUT")"
[[ "$BUILT_VERSION" == "$VERSION" ]] ||
  die "the build's metainfo says version '$BUILT_VERSION', expected $VERSION"
info "Version in the build: $BUILT_VERSION"
[[ "$BUILT_APK_SHA" == "$APK_SHA" ]] ||
  die "the companion APK in the build ($BUILT_APK_SHA) isn't the one just built ($APK_SHA)"
info "Companion APK in the build matches $APK"
BUNDLE_SHA="$(sha256 "$BUNDLE")"
BUNDLE_SIZE="$(du -h "$BUNDLE" | awk '{print $1}')"

log "Done: scrcpy4Frame $VERSION"
echo "    Bundle: $REPO_ROOT/$BUNDLE ($BUNDLE_SIZE)"
echo "    sha256: $BUNDLE_SHA"

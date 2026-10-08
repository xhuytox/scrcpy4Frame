#!/usr/bin/env bash
# Build and install the scrcpy4Frame Flatpak on the Steam Frame over SSH, using Flathub's
# org.flatpak.Builder. See -h.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT
readonly APP_ID="io.github.xhuytox.Scrcpy4Frame"
readonly MANIFEST="flatpak/$APP_ID.yml"
readonly FLATHUB_REPO="https://dl.flathub.org/repo/flathub.flatpakrepo"
# Keep in sync with runtime-version/base-version in the manifest.
readonly KDE_VERSION="6.11"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [<ssh-host>] [-- <extra flatpak-builder args>]

Builds scrcpy4Frame on a Steam Frame (Developer Mode with SSH) using the Flathub
org.flatpak.Builder Flatpak, then installs it there for the SSH user (--user).

Arguments:
  <ssh-host>         ssh destination, e.g. deck@192.168.1.42 or an ~/.ssh/config alias
                     (default: \$FRAME_HOST)

Options:
  --bundle           also export scrcpy4frame-<arch>.flatpak and copy it to dist/
  --no-install       build and export into the Frame's local repo only, don't install
  --workdir DIR      work directory on the Frame, absolute or relative to its \$HOME
                     (default: \$FRAME_WORKDIR or scrcpy4frame-build)
  -h, --help         show this help

Anything after "--" is passed to flatpak-builder, e.g. -- --keep-build-dirs

Steps:
  1. scripts/build-companion.sh, if dist/companion.apk is missing
  2. sync the repo to <workdir>/src on the Frame (no .git, .venv or build outputs)
  3. install org.flatpak.Builder, org.kde.Platform/Sdk//$KDE_VERSION and
     io.qt.PySide.BaseApp//$KDE_VERSION from Flathub (--user)
  4. flatpak run org.flatpak.Builder --user --install --force-clean
       --install-deps-from=flathub --repo=<workdir>/repo <workdir>/build-dir $MANIFEST
  5. with --bundle: flatpak build-bundle, then copy dist/scrcpy4frame-<arch>.flatpak back

The first build downloads about 2 GB of SDK and compiles abseil, protobuf, adb, FFmpeg,
scrcpy and OpenVR, which takes a while. Later builds reuse ccache and the build cache in <workdir>/state.
If the SSH session drops, the build stops; re-running resumes from the cache.
EOF
}

die() { echo "frame-build: error: $*" >&2; exit 1; }
log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

HOST="${FRAME_HOST:-}"
WORKDIR="${FRAME_WORKDIR:-scrcpy4frame-build}"
BUNDLE=0
INSTALL=1
EXTRA_ARGS=()

while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --bundle) BUNDLE=1 ;;
    --no-install) INSTALL=0 ;;
    --workdir) [[ $# -ge 2 ]] || die "--workdir needs a value"; WORKDIR="$2"; shift ;;
    --workdir=*) WORKDIR="${1#*=}" ;;
    --) shift; EXTRA_ARGS=("$@"); break ;;
    -*) die "unknown option: $1 (see -h)" ;;
    *) [[ -z "${HOST_ARG:-}" ]] || die "unexpected argument: $1"; HOST_ARG="$1" ;;
  esac
  shift
done
HOST="${HOST_ARG:-$HOST}"
[[ -n "$HOST" ]] || { usage >&2; echo >&2; die "no ssh host given (argument or FRAME_HOST)"; }

for tool in ssh scp; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool not found"
done

# Share one SSH connection so a password is asked for at most once.
SSH_CTL_DIR="$(mktemp -d /tmp/s4f-ssh.XXXXXX)"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$SSH_CTL_DIR/%C" -o ControlPersist=600
          -o ServerAliveInterval=30 -o ServerAliveCountMax=6)
cleanup() {
  ssh "${SSH_OPTS[@]}" -O exit "$HOST" >/dev/null 2>&1 || true
  rm -rf "$SSH_CTL_DIR"
}
trap cleanup EXIT

# Remote commands are quoted locally with printf %q.
# shellcheck disable=SC2029
remote() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }
# Run a bash script from stdin on the Frame with the given arguments.
# shellcheck disable=SC2029
remote_script() { ssh "${SSH_OPTS[@]}" "$HOST" "bash -s -- $(printf '%q ' "$@")"; }

if [[ ! -f "$REPO_ROOT/dist/companion.apk" ]]; then
  log "dist/companion.apk is missing; building it with scripts/build-companion.sh"
  [[ -x "$REPO_ROOT/scripts/build-companion.sh" ]] || die "scripts/build-companion.sh not found or not executable"
  "$REPO_ROOT/scripts/build-companion.sh"
  [[ -f "$REPO_ROOT/dist/companion.apk" ]] || die "scripts/build-companion.sh did not produce dist/companion.apk"
fi

log "Connecting to $HOST"
# shellcheck disable=SC2016  # $HOME must expand on the Frame
REMOTE_HOME="$(remote 'printf %s "$HOME"')" || die "ssh to $HOST failed"
# shellcheck disable=SC2088  # "~/" is matched literally and resolved against the remote $HOME
case "$WORKDIR" in
  /*) WORK="$WORKDIR" ;;
  "~/"*) WORK="$REMOTE_HOME/${WORKDIR#\~/}" ;;
  *) WORK="$REMOTE_HOME/$WORKDIR" ;;
esac
REMOTE_ARCH="$(remote 'uname -m')"
[[ "$REMOTE_ARCH" == aarch64 ]] || echo "frame-build: note: remote architecture is $REMOTE_ARCH, not aarch64" >&2
remote "mkdir -p $(printf '%q' "$WORK/src")"

log "Syncing the repository to $HOST:$WORK/src"
EXCLUDES=(
  --exclude=/.git/ --exclude=.venv/ --exclude=__pycache__/ --exclude='*.pyc'
  --exclude=.pytest_cache/ --exclude=.mypy_cache/ --exclude=.ruff_cache/
  --exclude=/build/ --exclude=/build-dir/ --exclude=/_build/ --exclude=/.flatpak-builder/
  --exclude=/repo/ --exclude=/vr-toast/build/
  --exclude=/companion/.gradle/ --exclude=/companion/.kotlin/ --exclude=/companion/build/
  --exclude='/companion/*/build/' --exclude=/companion/local.properties
  --exclude='/dist/*.flatpak' --exclude=.idea/ --exclude=.DS_Store
)
if command -v rsync >/dev/null 2>&1 && remote 'command -v rsync' >/dev/null 2>&1; then
  rsync -az --delete "${EXCLUDES[@]}" \
    -e "ssh $(printf '%q ' "${SSH_OPTS[@]}")" \
    "$REPO_ROOT/" "$HOST:$(printf '%q' "$WORK/src")/"
else
  echo "rsync is not available on both ends; falling back to tar over ssh" >&2
  TAR_EXCLUDES=()
  for e in "${EXCLUDES[@]}"; do
    pattern="${e#--exclude=}"; pattern="${pattern#/}"; pattern="${pattern%/}"
    TAR_EXCLUDES+=("--exclude=$pattern")
  done
  COPYFILE_DISABLE=1 tar -C "$REPO_ROOT" "${TAR_EXCLUDES[@]}" -czf - . |
    remote "rm -rf $(printf '%q' "$WORK/src") && mkdir -p $(printf '%q' "$WORK/src") && tar --warning=no-unknown-keyword -xzf - -C $(printf '%q' "$WORK/src")"
fi

log "Installing org.flatpak.Builder and the $KDE_VERSION runtime/SDK/BaseApp (--user)"
remote_script "$FLATHUB_REPO" "$KDE_VERSION" "$WORK" <<'REMOTE'
set -euo pipefail
flathub_repo="$1"; kde="$2"; work="$3"
command -v flatpak >/dev/null 2>&1 || { echo "flatpak is not installed on this device" >&2; exit 1; }
flatpak remote-add --user --if-not-exists flathub "$flathub_repo"
flatpak install --user --or-update -y --noninteractive flathub \
  org.flatpak.Builder \
  "org.kde.Platform//$kde" "org.kde.Sdk//$kde" "io.qt.PySide.BaseApp//$kde"
avail_kb="$(df -Pk "$work" | awk 'NR==2 {print $4}')"
if [ -n "$avail_kb" ] && [ "$avail_kb" -lt $((8 * 1024 * 1024)) ]; then
  echo "warning: only $((avail_kb / 1024)) MiB free under $work; the build needs roughly 8 GiB" >&2
fi
REMOTE

log "Building $APP_ID on the Frame"
# --disable-rofiles-fuse: the Flatpak'd builder can't mount rofiles-fuse on SteamOS ("fusermount:
# failed to access mountpoint"). It only guards against modules touching earlier modules' files.
BUILD_CMD=(flatpak run org.flatpak.Builder --user --force-clean --install-deps-from=flathub --ccache
           --disable-rofiles-fuse "--state-dir=$WORK/state" "--repo=$WORK/repo")
((INSTALL)) && BUILD_CMD+=(--install)
BUILD_CMD+=("${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}" "$WORK/build-dir" "$MANIFEST")
TTY_OPT=()
[[ -t 0 && -t 1 ]] && TTY_OPT=(-t)   # lets Ctrl-C stop the remote build
# shellcheck disable=SC2029
ssh "${SSH_OPTS[@]}" "${TTY_OPT[@]+"${TTY_OPT[@]}"}" "$HOST" \
  "cd $(printf '%q' "$WORK/src") && $(printf '%q ' "${BUILD_CMD[@]}")"

if ((BUNDLE)); then
  BUNDLE_NAME="scrcpy4frame-$REMOTE_ARCH.flatpak"
  log "Exporting $BUNDLE_NAME"
  remote "flatpak build-bundle --runtime-repo=$(printf '%q' "$FLATHUB_REPO") $(printf '%q' "$WORK/repo") $(printf '%q' "$WORK/$BUNDLE_NAME") $APP_ID"
  mkdir -p "$REPO_ROOT/dist"
  scp "${SSH_OPTS[@]}" "$HOST:$WORK/$BUNDLE_NAME" "$REPO_ROOT/dist/$BUNDLE_NAME"
  echo "Bundle: $REPO_ROOT/dist/$BUNDLE_NAME"
fi

if ((INSTALL)); then
  log "Done. On the Frame: flatpak run $APP_ID (or find scrcpy4Frame in the Desktop Mode app menu)"
else
  log "Done. The build is in $HOST:$WORK/repo (not installed)"
fi

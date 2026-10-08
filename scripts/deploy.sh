#!/usr/bin/env bash
# Copy a scrcpy4Frame Flatpak bundle to the Steam Frame and (re)install it there. See -h.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT
readonly APP_ID="io.github.xhuytox.Scrcpy4Frame"
readonly FLATHUB_REPO="https://dl.flathub.org/repo/flathub.flatpakrepo"

usage() {
  cat <<EOF
Usage: $(basename "$0") <ssh-host> [bundle]

Copies a Flatpak bundle to the Frame over scp and runs
  flatpak install --user -y --reinstall <bundle>
there. Runtime dependencies are fetched from Flathub on the Frame.

Arguments:
  <ssh-host>   ssh destination, e.g. deck@192.168.1.42 (default: \$FRAME_HOST)
  [bundle]     path to the .flatpak (default: dist/scrcpy4frame-aarch64.flatpak)

Options:
  -h, --help   show this help
EOF
}

die() { echo "deploy: error: $*" >&2; exit 1; }

POSITIONAL=()
while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option: $1 (see -h)" ;;
    *) POSITIONAL+=("$1") ;;
  esac
  shift
done
((${#POSITIONAL[@]} <= 2)) || die "too many arguments (see -h)"
HOST="${POSITIONAL[0]:-${FRAME_HOST:-}}"
BUNDLE="${POSITIONAL[1]:-$REPO_ROOT/dist/scrcpy4frame-aarch64.flatpak}"
[[ -n "$HOST" ]] || { usage >&2; echo >&2; die "no ssh host given"; }
[[ -f "$BUNDLE" ]] || die "bundle not found: $BUNDLE (build one with scripts/frame-build.sh --bundle or scripts/build.sh)"

SSH_CTL_DIR="$(mktemp -d /tmp/s4f-ssh.XXXXXX)"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$SSH_CTL_DIR/%C" -o ControlPersist=120)
cleanup() {
  ssh "${SSH_OPTS[@]}" -O exit "$HOST" >/dev/null 2>&1 || true
  rm -rf "$SSH_CTL_DIR"
}
trap cleanup EXIT

REMOTE_BUNDLE="/tmp/scrcpy4frame-deploy-$$.flatpak"
echo "==> Copying $(basename "$BUNDLE") to $HOST"
scp "${SSH_OPTS[@]}" "$BUNDLE" "$HOST:$REMOTE_BUNDLE"

echo "==> Installing on $HOST"
# shellcheck disable=SC2029  # arguments are quoted locally with printf %q
ssh "${SSH_OPTS[@]}" "$HOST" "bash -s -- $(printf '%q ' "$REMOTE_BUNDLE" "$FLATHUB_REPO" "$APP_ID")" <<'REMOTE'
set -euo pipefail
bundle="$1"; flathub="$2"; app_id="$3"
trap 'rm -f "$bundle"' EXIT
command -v flatpak >/dev/null 2>&1 || { echo "flatpak is not installed on this device" >&2; exit 1; }
flatpak remote-add --user --if-not-exists flathub "$flathub"
flatpak install --user -y --noninteractive --reinstall --bundle "$bundle"
flatpak info --user "$app_id" | sed -n '1,12p'
REMOTE

echo "==> Installed. Run it on the Frame with: flatpak run $APP_ID"

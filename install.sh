#!/usr/bin/env bash
# Install or update scrcpy4Frame on the Steam Frame from a GitHub release. Run in Desktop Mode:
#   curl -sSL https://raw.githubusercontent.com/xhuytox/scrcpy4Frame/main/install.sh | bash
# See -h (pass arguments with: ... | bash -s -- <args>).
set -euo pipefail

readonly REPO="xhuytox/scrcpy4Frame"
readonly APP_ID="io.github.xhuytox.Scrcpy4Frame"
readonly ASSET="scrcpy4frame-aarch64.flatpak"
readonly FLATHUB_REPO="https://dl.flathub.org/repo/flathub.flatpakrepo"

usage() {
  cat <<EOF
Usage: install.sh [<version>]

Downloads $ASSET from the latest release of github.com/$REPO (or from
release <version>, e.g. v1.0.0) and installs it for the current user:
  flatpak install --user --reinstall --bundle $ASSET
Its runtime comes from Flathub. Running it again updates scrcpy4Frame; your settings are kept.

With curl: curl -sSL https://raw.githubusercontent.com/$REPO/main/install.sh | bash -s -- v1.0.0

Uninstall with: flatpak uninstall --user $APP_ID
EOF
}

die() { echo "install.sh: error: $*" >&2; exit 1; }
log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

TMP_DIR=""
cleanup() { [[ -z "$TMP_DIR" ]] || rm -rf "$TMP_DIR"; }

# Everything runs from main, so a download cut short by curl never runs half a script.
main() {
  local version=""
  while (($#)); do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      -*) die "unknown option: $1 (see -h)" ;;
      *) [[ -z "$version" ]] || die "unexpected argument: $1"; version="${1#v}" ;;
    esac
    shift
  done
  [[ -z "$version" || "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    die "'$version' isn't a version like 1.2.3"

  ((EUID != 0)) || die "run this as your normal user, without sudo. It installs for that user only"
  local arch
  arch="$(uname -m)"
  [[ "$arch" == aarch64 ]] || die "scrcpy4Frame is built for the Steam Frame (aarch64); this machine is $arch"
  local tool
  for tool in curl flatpak; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool not found"
  done

  local url
  if [[ -n "$version" ]]; then
    url="https://github.com/$REPO/releases/download/v$version/$ASSET"
  else
    url="https://github.com/$REPO/releases/latest/download/$ASSET"
  fi
  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT

  log "Downloading scrcpy4Frame ${version:+v$version }from $url"
  curl -fL --retry 3 --progress-bar -o "$TMP_DIR/$ASSET" "$url" ||
    die "couldn't download $url"

  log "Installing $APP_ID (--user)"
  # stdin is this script when it's piped from curl, so flatpak mustn't read it.
  flatpak remote-add --user --if-not-exists flathub "$FLATHUB_REPO" </dev/null
  flatpak install --user -y --reinstall --bundle "$TMP_DIR/$ASSET" </dev/null

  local installed
  installed="$(flatpak info --user "$APP_ID" </dev/null | sed -nE 's/^[[:space:]]*Version:[[:space:]]*//p')"
  log "Installed scrcpy4Frame${installed:+ $installed}"
  echo "    Open it from Utilities in the application menu, or run: flatpak run $APP_ID"
}

main "$@"

#!/usr/bin/env bash
# Build dist/scrcpy4frame-aarch64.flatpak in an arm64 Linux container (docker or podman).
# The usual path is scripts/frame-build.sh, which builds on the Frame itself. See -h.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT
readonly APP_ID="io.github.xhuytox.Scrcpy4Frame"
readonly MANIFEST="flatpak/$APP_ID.yml"
readonly FLATHUB_REPO="https://dl.flathub.org/repo/flathub.flatpakrepo"
readonly PLATFORM="linux/arm64"
readonly IMAGE="scrcpy4frame-flatpak-builder:latest"
readonly BUNDLE_NAME="scrcpy4frame-aarch64.flatpak"
VOLUME="${S4F_BUILD_VOLUME:-scrcpy4frame-flatpak}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [-- <extra flatpak-builder args>]

Builds the scrcpy4Frame Flatpak for aarch64 in an arm64 Linux container and writes
dist/$BUNDLE_NAME. Install it with scripts/deploy.sh <ssh-host>.

Options:
  --rebuild-image   rebuild the builder image ($IMAGE)
  --clean           delete the build volume ($VOLUME) and exit
  -h, --help        show this help

Environment:
  CONTAINER_ENGINE  docker or podman (default: whichever is installed, docker first)
  S4F_BUILD_VOLUME  named volume holding the SDK, ccache, build state and a copy of the repo
                    (default: scrcpy4frame-flatpak)

How it works: the repo is copied into a named volume rather than bind-mounted, because
ostree/flatpak-builder needs hardlinks and xattrs that macOS bind mounts do not provide. The
container runs --privileged (bubblewrap needs it) with flatpak-builder --user and
--disable-rofiles-fuse, then writes the bundle into the bind-mounted dist/ directory.
EOF
}

die() { echo "build: error: $*" >&2; exit 1; }
log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

REBUILD_IMAGE=0
CLEAN=0
EXTRA_ARGS=()
while (($#)); do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --rebuild-image) REBUILD_IMAGE=1 ;;
    --clean) CLEAN=1 ;;
    --) shift; EXTRA_ARGS=("$@"); break ;;
    *) die "unknown argument: $1 (see -h)" ;;
  esac
  shift
done

ENGINE="${CONTAINER_ENGINE:-}"
if [[ -z "$ENGINE" ]]; then
  if command -v docker >/dev/null 2>&1; then
    ENGINE=docker
  elif command -v podman >/dev/null 2>&1; then
    ENGINE=podman
  else
    die "neither docker nor podman is installed.
  Install one (Docker Desktop, OrbStack, Colima, or 'brew install podman && podman machine init --rootful && podman machine start'),
  or build on the Frame itself instead: scripts/frame-build.sh <ssh-host>"
  fi
fi
command -v "$ENGINE" >/dev/null 2>&1 || die "$ENGINE not found"
"$ENGINE" info >/dev/null 2>&1 || die "$ENGINE is installed but not running (start Docker Desktop / 'podman machine start')"

if ((CLEAN)); then
  log "Removing volume $VOLUME"
  "$ENGINE" volume rm "$VOLUME"
  exit 0
fi

if [[ ! -f "$REPO_ROOT/dist/companion.apk" ]]; then
  log "dist/companion.apk is missing; building it with scripts/build-companion.sh"
  [[ -x "$REPO_ROOT/scripts/build-companion.sh" ]] || die "scripts/build-companion.sh not found or not executable"
  "$REPO_ROOT/scripts/build-companion.sh"
  [[ -f "$REPO_ROOT/dist/companion.apk" ]] || die "scripts/build-companion.sh did not produce dist/companion.apk"
fi

if ((REBUILD_IMAGE)) || ! "$ENGINE" image inspect "$IMAGE" >/dev/null 2>&1; then
  log "Building the builder image $IMAGE ($PLATFORM)"
  ctx="$(mktemp -d)"
  trap 'rm -rf "$ctx"' EXIT
  cat >"$ctx/Containerfile" <<'EOF'
FROM docker.io/library/fedora:latest
RUN dnf -y install flatpak flatpak-builder ccache git xz tar && dnf clean all
EOF
  "$ENGINE" build --platform "$PLATFORM" -t "$IMAGE" -f "$ctx/Containerfile" "$ctx"
fi

"$ENGINE" volume inspect "$VOLUME" >/dev/null 2>&1 || "$ENGINE" volume create "$VOLUME" >/dev/null
log "Copying the repository into volume $VOLUME"
# Unanchored patterns match at any directory depth.
COPYFILE_DISABLE=1 tar -C "$REPO_ROOT" \
  --exclude=.git --exclude=.venv --exclude=__pycache__ --exclude='*.pyc' --exclude=.pytest_cache \
  --exclude=.mypy_cache --exclude=.ruff_cache --exclude=.idea --exclude=.DS_Store \
  --exclude=.flatpak-builder --exclude=build-dir --exclude=vr-toast/build \
  --exclude=companion/.gradle --exclude=companion/.kotlin --exclude=companion/build \
  --exclude='companion/*/build' --exclude=companion/local.properties --exclude='*.flatpak' \
  -cf - . |
  "$ENGINE" run --rm -i --platform "$PLATFORM" -v "$VOLUME:/work" "$IMAGE" \
    sh -c 'rm -rf /work/src && mkdir -p /work/src && tar -xf - -C /work/src --no-same-owner --warning=no-unknown-keyword'

mkdir -p "$REPO_ROOT/dist"
log "Building $APP_ID in the container (the first run downloads the KDE SDK and takes a while)"
# shellcheck disable=SC2016  # the script below runs in the container and gets its values as "$@"
"$ENGINE" run --rm --privileged --platform "$PLATFORM" \
  -v "$VOLUME:/work" -v "$REPO_ROOT/dist:/out" \
  -e HOME=/work/home -e XDG_DATA_HOME=/work/home/.local/share \
  "$IMAGE" bash -euo pipefail -c '
    flathub="$1"; app_id="$2"; manifest="$3"; bundle="$4"; shift 4
    flatpak remote-add --user --if-not-exists flathub "$flathub"
    cd /work/src
    flatpak-builder --user --install-deps-from=flathub --force-clean --ccache \
      --disable-rofiles-fuse --state-dir=/work/state --repo=/work/repo "$@" \
      /work/build-dir "$manifest"
    flatpak build-bundle --runtime-repo="$flathub" /work/repo "/work/$bundle" "$app_id"
    cp "/work/$bundle" "/out/$bundle"
  ' build "$FLATHUB_REPO" "$APP_ID" "$MANIFEST" "$BUNDLE_NAME" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

log "Done: $REPO_ROOT/dist/$BUNDLE_NAME"
echo "Install on the Frame with: scripts/deploy.sh <ssh-host>"

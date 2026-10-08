"""Filesystem locations and lookup of the bundled tools.

The Flatpak only uses the binaries under /app, never the host PATH. Dev runs look in
SCRCPY4FRAME_* overrides, then PATH, then the Android SDK.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_ID = "io.github.xhuytox.Scrcpy4Frame"
APP_NAME = "scrcpy4Frame"
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent.parent

_BUNDLED = {
    "adb": Path("/app/bin/adb"),
    "scrcpy": Path("/app/bin/scrcpy"),
    "vr-toast": Path("/app/bin/vr-toast"),
    "vr-mirror": Path("/app/bin/vr-mirror"),
}
_BUNDLED_SERVER = Path("/app/share/scrcpy/scrcpy-server")
_BUNDLED_COMPANION = Path("/app/share/scrcpy4frame/companion.apk")
_BUNDLED_VR_MIRROR_MANIFEST = Path("/app/share/scrcpy4frame/vr-mirror.vrmanifest")
_BUNDLED_ICON = Path(f"/app/share/icons/hicolor/128x128/apps/{APP_ID}.png")


def in_flatpak() -> bool:
    return "FLATPAK_ID" in os.environ or Path("/.flatpak-info").exists()


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    base = Path(value) if value else Path.home() / fallback
    path = base / "scrcpy4frame"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def adb_home() -> Path:
    """HOME for adb children: adb keeps its key pair in $HOME/.android."""
    override = os.environ.get("SCRCPY4FRAME_ADB_HOME")
    path = Path(override) if override else data_dir() / "adb-home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _env_override(name: str) -> str | None:
    key = "SCRCPY4FRAME_" + name.upper().replace("-", "_")
    value = os.environ.get(key)
    return value if value and Path(value).exists() else None


def tool_path(name: str) -> str | None:
    """Path of a tool ("adb", "scrcpy", "vr-toast"), or None if it isn't available."""
    if in_flatpak():
        bundled = _BUNDLED[name]
        return str(bundled) if bundled.exists() else None
    override = _env_override(name)
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    if name == "adb":
        for sdk in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                    str(Path.home() / "Library/Android/sdk"), str(Path.home() / "Android/Sdk")):
            if sdk and (Path(sdk) / "platform-tools/adb").exists():
                return str(Path(sdk) / "platform-tools/adb")
    if name in ("vr-toast", "vr-mirror"):
        local = REPO_DIR / name / "build" / name
        if local.exists():
            return str(local)
    return None


def scrcpy_server_path() -> str | None:
    """Explicit scrcpy-server path, or None to let scrcpy use its compiled-in default."""
    if in_flatpak():
        return str(_BUNDLED_SERVER) if _BUNDLED_SERVER.exists() else None
    return _env_override("scrcpy-server")


def scrcpy_server_file() -> Path | None:
    """scrcpy-server file to push for our own sessions. Must match the scrcpy client version."""
    explicit = scrcpy_server_path() or os.environ.get("SCRCPY_SERVER_PATH")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    scrcpy = tool_path("scrcpy")
    if scrcpy:  # Homebrew layout: <prefix>/bin/scrcpy and <prefix>/share/scrcpy/scrcpy-server
        candidate = Path(scrcpy).resolve().parent.parent / "share/scrcpy/scrcpy-server"
        if candidate.exists():
            return candidate
    return None


def vr_mirror_manifest() -> Path | None:
    if in_flatpak():
        return _BUNDLED_VR_MIRROR_MANIFEST if _BUNDLED_VR_MIRROR_MANIFEST.exists() else None
    local = REPO_DIR / "vr-mirror/manifest/vr-mirror.vrmanifest"
    return local if local.exists() else None


VR_MIRROR_INPUT_FILES = ("vr-mirror-actions.json", "vr-mirror-bindings-frame-controller.json")


def vr_mirror_data_files() -> list[Path]:
    """The vrmanifest and its SteamVR Input files. They must sit together, and on the host next
    to the helper."""
    manifest = vr_mirror_manifest()
    if manifest is None:
        return []
    return [manifest] + [p for p in (manifest.parent / n for n in VR_MIRROR_INPUT_FILES) if p.exists()]


def companion_apk_path() -> Path | None:
    if in_flatpak():
        return _BUNDLED_COMPANION if _BUNDLED_COMPANION.exists() else None
    override = _env_override("companion-apk")
    if override:
        return Path(override)
    local = REPO_DIR / "dist/companion.apk"
    return local if local.exists() else None


def app_icon_path() -> Path | None:
    """The 128 px PNG app icon."""
    if in_flatpak():
        return _BUNDLED_ICON if _BUNDLED_ICON.exists() else None
    local = REPO_DIR / "flatpak/data/icons/128x128.png"
    return local if local.exists() else None


def qml_dir() -> Path:
    return PACKAGE_DIR / "qml"


def content_dir() -> Path:
    return PACKAGE_DIR / "content"

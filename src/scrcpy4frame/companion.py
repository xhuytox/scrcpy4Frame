"""Install, grant and verify the optional companion app over adb, with no taps on the phone."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .adb import Adb

log = logging.getLogger(__name__)

PACKAGE = "io.github.xhuytox.scrcpy4frame.companion"
LISTENER = f"{PACKAGE}/.NotifListener"
LISTENER_FULL = f"{PACKAGE}/{PACKAGE}.NotifListener"
SOCKET_NAME = "scrcpy4frame"
WRITE_SECURE_SETTINGS = "android.permission.WRITE_SECURE_SETTINGS"
MIN_SDK = 26
WIRELESS_DEBUGGING_SDK = 30

# Steps shown as a checklist in the UI, in order.
STEPS = ("install", "reconnect", "notifications", "battery")


@dataclass
class InstallReport:
    ok: bool = False
    steps: dict[str, str] = field(default_factory=dict)   # step -> ok | failed | skipped
    error: str = ""


def parse_install(text: str) -> tuple[bool, str]:
    """Return (ok, failure code) from `adb install` output."""
    if re.search(r"^Success\s*$", text, re.MULTILINE):
        return True, ""
    match = re.search(r"\[?(INSTALL_[A-Z_]+)", text)
    return False, match.group(1) if match else "UNKNOWN"


INSTALL_HINTS = {
    "INSTALL_FAILED_USER_RESTRICTED":
        "Your device blocked the install. On Xiaomi/Redmi/POCO open Developer options and turn on "
        "\"Install via USB\", then try again.",
    "INSTALL_FAILED_VERIFICATION_FAILURE":
        "Your device's app verification blocked the install. In Developer options turn off "
        "\"Verify apps over USB\", or approve the prompt on the device, then try again.",
    "INSTALL_FAILED_OLDER_SDK": "The companion needs Android 8 or newer.",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "Your device is out of storage space.",
    "INSTALL_FAILED_ABORTED": "The install was cancelled on the device. Try again and tap Install.",
}


def permission_granted(dumpsys: str, permission: str) -> bool:
    return re.search(re.escape(permission) + r": granted=true", dumpsys) is not None


def listener_enabled(setting: str) -> bool:
    entries = [e.strip() for e in setting.strip().split(":") if e.strip()]
    return LISTENER in entries or LISTENER_FULL in entries


def merged_listeners(existing: str, component: str = LISTENER_FULL) -> str:
    entries = [e for e in existing.strip().split(":") if e and e != "null"]
    if component not in entries:
        entries.append(component)
    return ":".join(entries)


_LIVE_LISTENERS = re.compile(r"^(\s*)Live notification listeners \(\d+\):")


def listener_live(dumpsys: str) -> bool | None:
    """Whether `dumpsys notification` lists the companion's listener as bound, or None if the dump
    has no list of live listeners."""
    lines = dumpsys.splitlines()
    for i, line in enumerate(lines):
        header = _LIVE_LISTENERS.match(line)
        if not header:
            continue
        indent = len(header.group(1))
        for entry in lines[i + 1:]:
            if len(entry) - len(entry.lstrip()) <= indent:
                break
            if f"{PACKAGE}/" in entry:
                return True
        return False
    return None


def installed_version(adb: Adb, serial: str) -> int | None:
    out = adb.shell(serial, f"dumpsys package {PACKAGE}").out
    match = re.search(r"versionCode=(\d+)", out)
    return int(match.group(1)) if match else None


def install_and_grant(adb: Adb, serial: str, apk: Path, sdk: int) -> InstallReport:
    report = InstallReport()
    if sdk and sdk < MIN_SDK:
        report.error = "The companion needs Android 8 or newer."
        return report

    # A signature mismatch (APK built on another machine) needs an uninstall first.
    result = adb.install(serial, apk)
    ok, code = parse_install(result.text)
    if not ok and code == "INSTALL_FAILED_UPDATE_INCOMPATIBLE":
        adb.uninstall(serial, PACKAGE)
        result = adb.install(serial, apk)
        ok, code = parse_install(result.text)
    if not ok:
        report.steps["install"] = "failed"
        report.error = INSTALL_HINTS.get(code, f"Installing the companion failed ({code}).")
        return report
    report.steps["install"] = "ok"

    # WRITE_SECURE_SETTINGS lets the companion turn Wireless debugging on and off (Android 11+).
    if sdk >= WIRELESS_DEBUGGING_SDK:
        adb.shell(serial, f"pm grant {PACKAGE} {WRITE_SECURE_SETTINGS}")
        dumpsys = adb.shell(serial, f"dumpsys package {PACKAGE}").out
        report.steps["reconnect"] = "ok" if permission_granted(dumpsys, WRITE_SECURE_SETTINGS) else "failed"
    else:
        report.steps["reconnect"] = "skipped"

    report.steps["notifications"] = "ok" if allow_listener(adb, serial) else "failed"

    # Keep Doze from killing the listener.
    adb.shell(serial, f"cmd deviceidle whitelist +{PACKAGE}")
    whitelist = adb.shell(serial, "cmd deviceidle whitelist").out
    report.steps["battery"] = "ok" if PACKAGE in whitelist else "failed"

    failed = [s for s in ("reconnect", "notifications") if report.steps.get(s) == "failed"]
    report.ok = not failed
    if failed:
        report.error = ("The companion is installed, but your device refused some permissions ("
                        + ", ".join(failed) + "). Some devices need \"USB debugging (Security "
                        "settings)\" turned on in Developer options.")
    return report


def allow_listener(adb: Adb, serial: str) -> bool:
    """Grant notification access, which makes Android bind the listener. True if it's granted."""
    adb.shell(serial, f"cmd notification allow_listener {LISTENER}")
    current = adb.shell(serial, "settings get secure enabled_notification_listeners").out
    if not listener_enabled(current):
        merged = merged_listeners(current)
        adb.shell(serial, f"settings put secure enabled_notification_listeners '{merged}'")
        current = adb.shell(serial, "settings get secure enabled_notification_listeners").out
    return listener_enabled(current)


def restart_listener(adb: Adb, serial: str) -> str:
    """Make Android bind the companion's listener again if it isn't bound, as reinstalling would.

    The listener hosts the companion's socket and wake port. When its process dies, Android tries
    to bind it again once, and some ROMs (Xiaomi's among them) block that, so it stays down while
    notification access still shows as granted. Taking access away and granting it again makes a
    fresh bind. Returns "running" if it was bound, "restarted", "failed", "revoked" if notification
    access was turned off (left as it is), "missing" if the companion isn't installed, or "error" if
    adb didn't answer.
    """
    listed = adb.shell(serial, f"pm list packages {PACKAGE}")
    if listed.rc != 0:
        return "error"
    if f"package:{PACKAGE}" not in listed.out.split():
        return "missing"
    if listener_live(adb.shell(serial, "dumpsys notification").out):
        return "running"
    if not listener_enabled(adb.shell(serial, "settings get secure enabled_notification_listeners").out):
        return "revoked"
    adb.shell(serial, f"cmd notification disallow_listener {LISTENER}")
    return "restarted" if allow_listener(adb, serial) else "failed"


def uninstall(adb: Adb, serial: str) -> bool:
    return "Success" in adb.uninstall(serial, PACKAGE).text


def turn_off_wireless_debugging(adb: Adb, serial: str) -> None:
    """What the companion would do after `bye`. It ends adb over Wi-Fi, so send it last."""
    adb.shell(serial, "settings put global adb_wifi_enabled 0", timeout=5)


TILE_COMPONENT = "com.android.settings/.development.qstile.DevelopmentTiles$WirelessDebugging"
TILE_SPEC_MARK = "DevelopmentTiles$WirelessDebugging"


def tile_present(sysui_qs_tiles: str) -> bool:
    return TILE_SPEC_MARK in sysui_qs_tiles


def add_tile(adb: Adb, serial: str) -> bool:
    """The developer tile is disabled by default: enable it, then add it to Quick Settings."""
    quoted = TILE_COMPONENT.replace("$", "\\$")
    adb.shell(serial, f"pm enable \"{quoted}\"")
    adb.shell(serial, f"cmd statusbar add-tile \"{quoted}\"")
    return check_tile(adb, serial)


def check_tile(adb: Adb, serial: str) -> bool:
    return tile_present(adb.shell(serial, "settings get secure sysui_qs_tiles").out)

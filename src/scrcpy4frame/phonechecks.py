"""Checks of phone-side settings that can be verified once adb is connected."""

from __future__ import annotations

from .adb import Adb

# Brands whose ROMs block input injection until an extra developer setting is enabled.
INPUT_GUARD_BRANDS = ("xiaomi", "redmi", "poco", "oppo", "realme", "oneplus")


def needs_input_check(manufacturer: str) -> bool:
    return manufacturer.strip().lower() in INPUT_GUARD_BRANDS


def input_blocked(adb: Adb, serial: str) -> bool:
    """KEYCODE_UNKNOWN (0) is harmless; MIUI/ColorOS reject it without the extra setting."""
    text = adb.shell(serial, "input keyevent 0").text
    return "INJECT_EVENTS" in text or "SecurityException" in text


def developer_options_enabled(adb: Adb, serial: str) -> bool:
    return adb.shell(serial, "settings get global development_settings_enabled").out.strip() == "1"

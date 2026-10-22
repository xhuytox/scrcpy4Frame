"""Saved phones, persisted as JSON in the config dir."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

from .discovery import same_phone, serial_from_guid

log = logging.getLogger(__name__)

# How the device is reached over Wi-Fi (SavedDevice.method).
WIRELESS = "wireless"   # Android 11+ Wireless debugging (paired key, port found over mDNS)
TCPIP = "tcpip"         # legacy `adb tcpip 5555` set up over USB (lasts until the phone reboots)
NONE = "none"           # not reachable over Wi-Fi yet: USB only

# How the device was set up (SavedDevice.setup).
SETUP_WIRELESS = "wireless"
SETUP_WIRED = "wired"


@dataclass
class SavedDevice:
    id: str                         # pairing guid, "tcpip-<serialno>" or "usb-<serialno>"; fixed once stored
    method: str = WIRELESS          # Wi-Fi kind: WIRELESS | TCPIP | NONE
    setup: str = SETUP_WIRELESS
    wireless_guid: str = ""         # pairing guid, to match its mDNS services
    usb_serial: str = ""            # adb serial over USB, when known
    serialno: str = ""
    model: str = ""
    manufacturer: str = ""
    android_release: str = ""
    sdk: int = 0
    last_host: str = ""
    last_port: int = 0
    nickname: str = ""
    companion: bool = False         # user opted in and the companion is installed
    wake_port: int = 0              # the companion's UDP wake port, from its hello. 0: can't be woken
    qs_tile: bool = False
    wifi_ok: bool = False           # reached over Wi-Fi at least once
    app_enabled_wireless_debugging: bool = False    # this app turned Wireless debugging on
    wd_asked_networks: list[str] = field(default_factory=list)  # asking to turn it on failed here
    added_at: float = field(default_factory=time.time)
    last_connected_at: float = 0.0

    @property
    def display_name(self) -> str:
        if self.nickname:
            return self.nickname
        if self.model:
            return self.model
        return self.serialno or self.id

    def matches_guid(self, guid: str) -> bool:
        if self.method != WIRELESS:
            return False
        known = self.wireless_guid or (self.id if serial_from_guid(self.id) else "")
        if known:
            return same_phone(known, guid)
        return bool(self.serialno) and serial_from_guid(guid) == self.serialno


def merge(old: SavedDevice, new: SavedDevice) -> SavedDevice:
    """`new` saved over `old`, the same phone: keeps old's id and what new doesn't know."""
    host, port = (new.last_host, new.last_port) if new.last_host else (old.last_host, old.last_port)
    return replace(
        new, id=old.id, added_at=old.added_at,
        method=new.method if new.method != NONE else old.method,
        wireless_guid=new.wireless_guid or old.wireless_guid, usb_serial=new.usb_serial or old.usb_serial,
        nickname=new.nickname or old.nickname, wake_port=new.wake_port or old.wake_port,
        last_host=host, last_port=port,
        companion=new.companion or old.companion, qs_tile=new.qs_tile or old.qs_tile,
        wifi_ok=new.wifi_ok or old.wifi_ok,
        app_enabled_wireless_debugging=new.app_enabled_wireless_debugging or old.app_enabled_wireless_debugging,
        wd_asked_networks=old.wd_asked_networks + [n for n in new.wd_asked_networks
                                                   if n not in old.wd_asked_networks])


def _migrate(item: dict) -> dict:
    """Fills in fields that files from before USB support lack."""
    item = dict(item)
    method = item.get("method", WIRELESS)
    item.setdefault("setup", SETUP_WIRED if method == TCPIP else SETUP_WIRELESS)
    if "wireless_guid" not in item:
        item["wireless_guid"] = item["id"] if method == WIRELESS and serial_from_guid(item["id"]) else ""
    item.setdefault("wifi_ok", bool(item.get("last_host")))
    return item


class DeviceStore:
    def __init__(self, path: Path):
        self.path = path
        self._devices: dict[str, SavedDevice] = {}
        self.load()

    def load(self) -> None:
        self._devices = {}
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            log.error("could not read %s: %s", self.path, exc)
            return
        known = {f.name for f in fields(SavedDevice)}
        loaded = [SavedDevice(**{k: v for k, v in _migrate(item).items() if k in known})
                  for item in raw.get("devices", [])]
        # One entry per phone: the oldest keeps its id, newer data goes on top.
        merged = False
        for device in sorted(loaded, key=lambda d: d.last_connected_at):
            same = next((d for d in self._devices.values() if device.serialno and d.serialno == device.serialno), None)
            if same is None:
                self._devices[device.id] = device
                continue
            older, newer = (same, device) if same.added_at <= device.added_at else (device, same)
            del self._devices[same.id]
            combined = merge(older, replace(newer, last_connected_at=max(same.last_connected_at,
                                                                         device.last_connected_at)))
            self._devices[combined.id] = combined
            merged = True
        if merged:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": [asdict(d) for d in self._devices.values()]}, indent=2))
        os.replace(tmp, self.path)

    def all(self) -> list[SavedDevice]:
        return sorted(self._devices.values(), key=lambda d: -d.last_connected_at)

    def get(self, device_id: str) -> SavedDevice | None:
        return self._devices.get(device_id)

    def upsert(self, device: SavedDevice) -> SavedDevice:
        """Stores the device and returns it as stored. Saving the same phone under another id (a
        re-pair, or a different setup) updates the existing entry, which keeps its id."""
        if device.id not in self._devices:
            same = next((d for d in self._devices.values()
                         if device.serialno and d.serialno == device.serialno), None)
            if same is not None:
                device = merge(same, device)
        self._devices[device.id] = device
        self.save()
        return device

    def add(self, device: SavedDevice) -> SavedDevice:
        """Stores a device the wizard just set up. Setting a phone up again keeps what was known about it,
        even when it's saved under the same id."""
        same = self._devices.get(device.id) or next(
            (d for d in self._devices.values() if device.serialno and d.serialno == device.serialno), None)
        return self.upsert(merge(same, device) if same is not None else device)

    def remove(self, device_id: str) -> None:
        if self._devices.pop(device_id, None):
            self.save()

    def find_by_guid(self, guid: str) -> SavedDevice | None:
        for device in self._devices.values():
            if device.matches_guid(guid):
                return device
        return None

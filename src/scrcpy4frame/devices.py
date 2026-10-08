"""Saved phones, persisted as JSON in the config dir."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .discovery import same_phone

log = logging.getLogger(__name__)

WIRELESS = "wireless"   # Android 11+ Wireless debugging (paired key, port found over mDNS)
TCPIP = "tcpip"         # legacy `adb tcpip 5555` set up over USB (lasts until the phone reboots)


@dataclass
class SavedDevice:
    id: str                         # pairing guid "adb-<serial>-<suffix>", or "tcpip-<serialno>"
    method: str = WIRELESS
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
        return self.method == WIRELESS and same_phone(self.id, guid)


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
        for item in raw.get("devices", []):
            device = SavedDevice(**{k: v for k, v in item.items() if k in known})
            self._devices[device.id] = device

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": [asdict(d) for d in self._devices.values()]}, indent=2))
        os.replace(tmp, self.path)

    def all(self) -> list[SavedDevice]:
        return sorted(self._devices.values(), key=lambda d: -d.last_connected_at)

    def get(self, device_id: str) -> SavedDevice | None:
        return self._devices.get(device_id)

    def upsert(self, device: SavedDevice) -> None:
        # A re-pair yields a new guid for the same phone; keep one entry per phone.
        for other in list(self._devices.values()):
            if other.id != device.id and other.serialno and other.serialno == device.serialno:
                device.companion = device.companion or other.companion
                device.qs_tile = device.qs_tile or other.qs_tile
                device.wake_port = device.wake_port or other.wake_port
                device.nickname = device.nickname or other.nickname
                del self._devices[other.id]
        self._devices[device.id] = device
        self.save()

    def remove(self, device_id: str) -> None:
        if self._devices.pop(device_id, None):
            self.save()

    def find_by_guid(self, guid: str) -> SavedDevice | None:
        for device in self._devices.values():
            if device.matches_guid(guid):
                return device
        return None

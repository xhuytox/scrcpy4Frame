"""User settings, persisted as JSON in the config dir."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Settings:
    max_size: int = 1920
    max_fps: int = 60
    bitrate_mbps: int = 12
    video_codec: str = "h264"
    audio_buffer_ms: int = 100             # scrcpy --audio-buffer for audio casting: one of AUDIO_BUFFERS_MS
    turn_screen_off: bool = False          # blank the phone's display. The phone keeps running.
    keep_active: bool = True
    panel_size: str = "medium"             # small | medium | large: the VR panel's size when it opens
    dashboard_button: bool = True          # button under the SteamVR dashboard that shows a hidden panel
    # Notifications (companion only)
    notifications: bool = True
    notify_display: str = "auto"           # auto | vr | desktop | app
    notify_sound: bool = True
    hide_content: bool = False
    suppress_while_visible: bool = True    # mute the phone whose mirror is on screen
    muted_packages: list[str] = field(default_factory=list)


# Renamed keys, old -> new. Old values are migrated on load.
RENAMED = {"suppress_while_mirroring": "suppress_while_visible"}

AUDIO_BUFFERS_MS = (50, 100, 150, 200)   # the choices in Settings › Audio


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.value = Settings()
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                for old, new in RENAMED.items():
                    if old in raw and new not in raw:
                        raw[new] = raw.pop(old)
                known = {f.name: f for f in fields(Settings)}
                if raw.get("audio_buffer_ms") not in AUDIO_BUFFERS_MS:
                    raw.pop("audio_buffer_ms", None)  # a buffer no longer offered: back to the default
                self.value = Settings(**{k: v for k, v in raw.items() if k in known})
            except (OSError, ValueError, TypeError) as exc:
                log.error("could not read %s: %s", path, exc)

    def update(self, key: str, value) -> bool:
        if not hasattr(self.value, key):
            return False
        current = getattr(self.value, key)
        if isinstance(current, bool):
            value = bool(value)
        elif isinstance(current, int):
            value = int(value)
        elif isinstance(current, float):
            value = float(value)
        elif isinstance(current, list):
            value = list(value)
        else:
            value = str(value)
        setattr(self.value, key, value)
        self.save()
        return True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.value), indent=2))
        os.replace(tmp, self.path)

"""Decides which forwarded phone notifications to show, and what the toast says. No Qt."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field

from ..settings import Settings

RECENT_LIMIT = 50
SAME_CONTENT_QUIET_S = 60.0   # re-posts with unchanged title+text inside this window stay quiet


@dataclass
class PhoneNotification:
    uid: str                 # "<device_id>|<key>", unique across phones
    device_id: str
    key: str
    pkg: str
    app_label: str
    title: str
    text: str
    when: int
    icon_b64: str | None = None
    received_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"uid": self.uid, "deviceId": self.device_id, "pkg": self.pkg,
                "appLabel": self.app_label, "title": self.title, "text": self.text,
                "when": self.when, "icon": self.icon_b64 or ""}


@dataclass(frozen=True)
class Toast:
    uid: str
    app_label: str
    title: str
    text: str
    icon_b64: str | None


class Router:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.recent: "OrderedDict[str, PhoneNotification]" = OrderedDict()
        self._last_shown: dict[str, tuple[str, str, float]] = {}

    def posted(self, device_id: str, message: dict, settings: Settings,
               visible_mirror_device_id: str = "") -> Toast | None:
        """Record a `posted` message and return the toast to display, or None to stay quiet.

        `visible_mirror_device_id` is the phone whose mirror (VR panel or window) is on screen.
        """
        key = str(message.get("key", ""))
        if not key:
            return None
        note = PhoneNotification(
            uid=f"{device_id}|{key}", device_id=device_id, key=key,
            pkg=str(message.get("pkg", "")),
            app_label=str(message.get("appLabel") or message.get("pkg", "")),
            title=str(message.get("title") or ""), text=str(message.get("text") or ""),
            when=int(message.get("when") or 0), icon_b64=message.get("icon") or None)
        self.recent.pop(note.uid, None)
        self.recent[note.uid] = note
        while len(self.recent) > RECENT_LIMIT:
            self.recent.popitem(last=False)

        if not settings.notifications or note.pkg in settings.muted_packages:
            return None
        if settings.suppress_while_visible and visible_mirror_device_id == device_id:
            return None          # the phone's own heads-up already shows in the mirror
        previous = self._last_shown.get(note.uid)
        now = self.clock()
        if previous and previous[:2] == (note.title, note.text) and now - previous[2] < SAME_CONTENT_QUIET_S:
            return None          # an update that didn't change what the user would read
        self._last_shown[note.uid] = (note.title, note.text, now)
        if settings.hide_content:
            return Toast(note.uid, note.app_label, note.app_label, "New notification", note.icon_b64)
        return Toast(note.uid, note.app_label, note.title or note.app_label, note.text, note.icon_b64)

    def removed(self, device_id: str, key: str) -> bool:
        uid = f"{device_id}|{key}"
        self._last_shown.pop(uid, None)
        return self.recent.pop(uid, None) is not None

    def forget_device(self, device_id: str) -> None:
        for uid in [u for u, n in self.recent.items() if n.device_id == device_id]:
            del self.recent[uid]
        for uid in [u for u in self._last_shown if u.startswith(f"{device_id}|")]:
            del self._last_shown[uid]

    def get(self, uid: str) -> PhoneNotification | None:
        return self.recent.get(uid)

    def recent_list(self) -> list[dict]:
        return [n.as_dict() for n in reversed(self.recent.values())]

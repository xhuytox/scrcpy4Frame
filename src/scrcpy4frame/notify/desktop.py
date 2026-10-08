"""Desktop notifications through org.freedesktop.Notifications (KDE Plasma in Desktop Mode)."""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import threading
from typing import Callable

from .. import paths
from .router import Toast

log = logging.getLogger(__name__)

_BUS = "org.freedesktop.Notifications"
_PATH = "/org/freedesktop/Notifications"


class DesktopNotifier:
    """`on_action(uid, action)` is called from a background thread, with action open|dismiss."""

    def __init__(self, on_action: Callable[[str, str], None]):
        self._on_action = on_action
        self._conn = None
        self._available: bool | None = None
        self._ids: dict[int, str] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        if self._available is None:
            self._available = self._connect()
        return self._available

    def _connect(self) -> bool:
        try:
            from jeepney import DBusAddress, new_method_call
            from jeepney.io.blocking import open_dbus_connection
            self._conn = open_dbus_connection(bus="SESSION")
            addr = DBusAddress(_PATH, bus_name=_BUS, interface=_BUS)
            info = self._conn.send_and_get_reply(new_method_call(addr, "GetServerInformation"), timeout=2)
            log.info("desktop notifications via %s", info.body[0])
        except Exception as exc:
            log.info("desktop notifications unavailable: %s", exc)
            self._conn = None
            return False
        threading.Thread(target=self._listen, name="notify-signals", daemon=True).start()
        return True

    def show(self, toast: Toast) -> None:
        if not self.available():
            return
        from jeepney import DBusAddress, new_method_call
        addr = DBusAddress(_PATH, bus_name=_BUS, interface=_BUS)
        body = html.escape(toast.text)
        actions = ["default", "Open on device", "open", "Open on device", "dismiss", "Dismiss"]
        hints = {"desktop-entry": ("s", paths.APP_ID), "category": ("s", "im.received"),
                 "suppress-sound": ("b", True)}       # we play our own chime (notify/sound.py)
        msg = new_method_call(addr, "Notify", "susssasa{sv}i",
                              (toast.app_label, 0, self._icon_path(toast), toast.title, body,
                               actions, hints, 8000))
        try:
            reply = self._conn.send_and_get_reply(msg, timeout=2)
            with self._lock:
                self._ids[int(reply.body[0])] = toast.uid
        except Exception as exc:
            log.warning("Notify failed: %s", exc)

    def _icon_path(self, toast: Toast) -> str:
        if not toast.icon_b64:
            return "phone"
        icons = paths.cache_dir() / "icons"
        icons.mkdir(exist_ok=True)
        data = base64.b64decode(toast.icon_b64)
        # In the Flatpak the cache dir lives under ~/.var/app/<id>, which the host daemon can read.
        path = icons / (hashlib.sha1(data).hexdigest()[:16] + ".png")
        if not path.exists():
            path.write_bytes(data)
        return str(path)

    def _listen(self) -> None:
        try:
            from jeepney import HeaderFields, MatchRule, message_bus
            from jeepney.io.blocking import open_dbus_connection
            conn = open_dbus_connection(bus="SESSION")
            rule = MatchRule(type="signal", interface=_BUS, path=_PATH)
            conn.send_and_get_reply(message_bus.AddMatch(rule), timeout=2)
            with conn.filter(rule) as queue:
                while True:
                    msg = conn.recv_until_filtered(queue)
                    member = msg.header.fields.get(HeaderFields.member)
                    nid = int(msg.body[0])
                    with self._lock:
                        uid = self._ids.get(nid)
                        if member == "NotificationClosed":
                            self._ids.pop(nid, None)
                    if uid is None:
                        continue
                    if member == "ActionInvoked":
                        action = "open" if msg.body[1] in ("default", "open") else str(msg.body[1])
                        self._on_action(uid, action)
        except Exception as exc:
            log.warning("notification signal listener stopped: %s", exc)

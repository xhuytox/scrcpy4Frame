"""Routes forwarded phone notifications to the VR overlay, desktop popups or in-app toasts."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from ..settings import SettingsStore
from .desktop import DesktopNotifier
from .router import Router, Toast
from .sound import Chime
from .vr import VrToaster

log = logging.getLogger(__name__)

VR_DECISION_MS = 3000


class NotificationCenter(QObject):
    changed = Signal()                  # the recent-notifications list changed
    inAppToast = Signal(dict)           # show a toast inside our own window
    actionRequested = Signal(str, str)  # uid, open|dismiss (from desktop popups)

    def __init__(self, settings: SettingsStore, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self.router = Router()
        self.vr = VrToaster(self)
        self.vr.stateChanged.connect(self._vr_state)
        # Signals emitted from the D-Bus listener thread are queued onto the main thread.
        self.desktop = DesktopNotifier(lambda uid, action: self.actionRequested.emit(uid, action))
        self._pending: list[Toast] = []
        self._vr_wait = QTimer(self, singleShot=True, interval=VR_DECISION_MS,
                               timeout=self._flush_without_vr)
        self.chime = Chime()
        QTimer.singleShot(0, self.chime.preload)

    def posted(self, device_id: str, message: dict, visible_mirror_device_id: str) -> None:
        toast = self.router.posted(device_id, message, self.settings.value, visible_mirror_device_id)
        self.changed.emit()
        if toast is not None:
            self._display(toast)

    def removed(self, device_id: str, key: str) -> None:
        if self.router.removed(device_id, key):
            self.changed.emit()

    def forget(self, uid: str) -> None:
        note = self.router.get(uid)
        if note and self.router.removed(note.device_id, note.key):
            self.changed.emit()

    def forget_device(self, device_id: str) -> None:
        self.router.forget_device(device_id)
        self.changed.emit()

    def clear(self) -> None:
        self.router.recent.clear()
        self.changed.emit()

    def shutdown(self) -> None:
        self.vr.stop()

    def _display(self, toast: Toast) -> None:
        mode = self.settings.value.notify_display
        if mode == "app":
            self._in_app(toast)
        elif mode == "desktop":
            self._desktop_or_app(toast)
        elif self.vr.state == "ready":
            self._in_vr(toast)
        elif mode in ("auto", "vr") and self.vr.can_try():
            self._pending.append(toast)
            self.vr.ensure_started()
            if not self._vr_wait.isActive():
                self._vr_wait.start()
        else:
            self._desktop_or_app(toast)

    def _vr_state(self, state: str) -> None:
        if state == "ready":
            self._vr_wait.stop()
            pending, self._pending = self._pending, []
            for toast in pending:
                self._in_vr(toast)
        elif state == "unavailable":
            self._flush_without_vr()

    def _flush_without_vr(self) -> None:
        pending, self._pending = self._pending, []
        for toast in pending:
            self._desktop_or_app(toast)

    def _in_vr(self, toast: Toast) -> None:
        self.vr.show(toast)
        self._chime()

    def _desktop_or_app(self, toast: Toast) -> None:
        if self.desktop.available():
            self.desktop.show(toast)
            self._chime()
        else:
            self._in_app(toast)

    def _chime(self) -> None:
        # Same chime for every display mode. Desktop pop-ups ask the server not to play its own.
        if self.settings.value.notify_sound:
            self.chime.play()

    def _in_app(self, toast: Toast) -> None:
        self._chime()
        self.inAppToast.emit({"uid": toast.uid, "appLabel": toast.app_label, "title": toast.title,
                              "text": toast.text, "icon": toast.icon_b64 or ""})

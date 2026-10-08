"""Faster wheel and touchpad scrolling app-wide, by scaling wheel deltas before Qt Quick sees them.

QStyleHints.wheelScrollLines only takes whole lines, and scaling the deltas covers touchpads too.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint
from PySide6.QtGui import QWheelEvent

SPEED = 2.0         # multiplier on Qt's default of 72 px per wheel notch
# In Game Mode (1920x1080, less Main.qml's 112 px header) Home and Settings show this many px of
# their content, and scroll SPEED * 72 px of it per wheel click (WheelScroll.qml). The VR panel
# scrolls the device by the same share of its screen. Through gamescope, one unit of SteamVR's
# smooth scroll is one wheel click.
PAGE_HEIGHT_PX = 1080 - 112
PAGE_SHARE_PER_CLICK = SPEED * 72 / PAGE_HEIGHT_PX


class WheelSpeed(QObject):
    """Application event filter that multiplies the deltas of wheel events sent to windows."""

    def __init__(self, factor: float, parent: QObject | None = None):
        super().__init__(parent)
        self.factor = factor
        self._resending = False
        self._rest = {"angle": [0.0, 0.0], "pixel": [0.0, 0.0]}   # fractions carried to the next event

    def _scale(self, delta: QPoint, kind: str) -> QPoint:
        rest = self._rest[kind]
        x, y = delta.x() * self.factor + rest[0], delta.y() * self.factor + rest[1]
        out = QPoint(round(x), round(y))
        rest[0], rest[1] = x - out.x(), y - out.y()
        return out

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        # Let our own resent event, and its delivery to items in the window, through untouched.
        if self._resending or event.type() != QEvent.Type.Wheel or not obj.isWindowType():
            return False
        scaled = QWheelEvent(event.position(), event.globalPosition(),
                             self._scale(event.pixelDelta(), "pixel"), self._scale(event.angleDelta(), "angle"),
                             event.buttons(), event.modifiers(), event.phase(), event.inverted(), event.source())
        scaled.setTimestamp(event.timestamp())
        self._resending = True
        try:
            QCoreApplication.sendEvent(obj, scaled)
        finally:
            self._resending = False
        return True


def install(app: QObject, factor: float = SPEED) -> WheelSpeed:
    """Speed up scrolling in every window of `app`. The filter is owned by `app`."""
    speed = WheelSpeed(factor, parent=app)
    app.installEventFilter(speed)
    return speed

"""Wheel scrolling is SPEED times Qt's default on every page."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QML = """
import QtQuick
Flickable { width: 800; height: 600; contentHeight: 20000; boundsBehavior: Flickable.StopAtBounds }
"""

# A page as the app builds them: a button covering the content, scrolled by WheelScroll.
PAGE = """
import QtQuick
import QtQuick.Controls.Basic
import "{components}"
Flickable {{
    id: page
    property int clicks: 0
    width: 800; height: 600; contentHeight: 20000; boundsBehavior: Flickable.StopAtBounds
    WheelScroll {{ flickable: page }}
    Button {{ width: 800; height: 20000; onClicked: page.clicks++ }}
}}
"""


def show(tmp_path, source):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView

    app = QGuiApplication.instance() or QGuiApplication([])
    path = tmp_path / "flick.qml"
    path.write_text(source)
    view = QQuickView()
    view.setSource(QUrl.fromLocalFile(str(path)))
    view.show()
    return app, view


@pytest.fixture
def flick(tmp_path):
    app, view = show(tmp_path, QML)
    yield app, view
    view.close()


@pytest.fixture
def page(tmp_path):
    from scrcpy4frame import paths

    app, view = show(tmp_path, PAGE.format(components=(paths.qml_dir() / "components").as_uri()))
    yield app, view
    view.close()


def wheel(view, angle_y=-120, pixel_y=0):
    """Send one wheel event to the middle of the view."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QGuiApplication, QWheelEvent

    pos = QPointF(400, 300)
    event = QWheelEvent(pos, view.mapToGlobal(pos), QPoint(0, pixel_y), QPoint(0, angle_y), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    event.setTimestamp(int(time.monotonic() * 1000))
    QGuiApplication.sendEvent(view, event)


def scroll(app, view, angle_y=-120, pixel_y=0):
    """Send one wheel event to the middle of the view and return how far the content moved."""
    flick = view.rootObject()
    flick.setProperty("contentY", 0)
    wheel(view, angle_y, pixel_y)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    return flick.property("contentY")


def test_wheel_notch_scrolls_speed_times_the_default(flick):
    from scrcpy4frame import scrolling

    app, view = flick
    assert scroll(app, view) == pytest.approx(72)          # Qt: 3 lines x 24 px per notch
    speed = scrolling.install(app)
    try:
        assert scroll(app, view) == pytest.approx(72 * scrolling.SPEED)
    finally:
        app.removeEventFilter(speed)


def test_press_right_after_wheel_scrolling_clicks(page):
    # Flickable's own wheel handling flicks, and swallows a press that arrives while the flick runs.
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    app, view = page
    for _ in range(30):                     # the Frame's stick: a stream of small deltas at ~90 Hz
        wheel(view, angle_y=-12)
        app.processEvents()
        time.sleep(0.011)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(400, 300))
    assert view.rootObject().property("clicks") == 1
    assert view.rootObject().property("contentY") == pytest.approx(30 * 12 / 120 * 72)


def test_fractional_deltas_carry_over():
    from PySide6.QtCore import QPoint

    from scrcpy4frame.scrolling import WheelSpeed

    speed = WheelSpeed(1.5)
    steps = [speed._scale(QPoint(0, 15), "angle").y() for _ in range(8)]   # a high-resolution wheel
    assert sum(steps) == 180                                               # 8 x 15 x 1.5, nothing lost

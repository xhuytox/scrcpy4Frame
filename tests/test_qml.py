"""Renders every screen and every wizard step offscreen and fails on any QML warning."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    base = tmp_path_factory.mktemp("xdg")
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.environ[var] = str(base / var)
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    from scrcpy4frame.__main__ import build_ui
    from scrcpy4frame.controller import AppController

    QQuickStyle.setStyle("Basic")
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = AppController(game_mode=False)
    engine, warnings = build_ui(controller)
    assert engine.rootObjects(), warnings
    yield app, controller, engine, warnings
    controller.runner.shutdown()


def settle(app, rounds=5):
    for _ in range(rounds):
        app.processEvents()


def all_items(item):
    yield item
    for child in item.childItems():
        yield from all_items(child)


def shown_texts(engine):
    """Text of every visible item on screen."""
    window = engine.rootObjects()[0]
    texts = []
    for item in all_items(window.contentItem()):
        text = item.property("text")
        if isinstance(text, str) and text and item.isVisible():
            texts.append(text)
    return texts


def test_home_states_render(ui):
    from scrcpy4frame.controller import Runtime
    from scrcpy4frame.devices import SavedDevice

    app, controller, engine, warnings = ui
    controller._ready = True
    controller.statusChanged.emit()
    statuses = ["connected", "off", "needs_pair", "unreachable", "ready", "connecting", "unplugged", "usb_waiting"]
    for i, status in enumerate(statuses):
        device = SavedDevice(id=f"adb-SER{i}-abcdef", model=f"Phone {i}", manufacturer="Google",
                             android_release="15", sdk=35, method="tcpip" if status == "unreachable" else "wireless",
                             companion=i % 2 == 0, qs_tile=i == 4)
        controller.store.upsert(device)
        controller.runtime[device.id] = Runtime(wifi_serial=f"192.168.1.{i}:5555", status=status,
                                                listener_check="restarted" if i == 2 else "",
                                                usb_serial=f"SER{i}" if i in (1, 3) else "",
                                                usb_state={1: "unauthorized", 3: "no permissions"}.get(i, ""))
    controller._usb_hint = "unauthorized"
    controller.center.router.posted("adb-SER0-abcdef", {"key": "k", "pkg": "com.x", "appLabel": "X",
                                                        "title": "Hello", "text": "World"},
                                    controller.settings.value)
    controller._mirror_message, controller._mirror_retry_smaller = "Encoder failed", True
    controller._vr_session, controller._mirror_mode = object(), "vr"      # connected (VR panel) card state
    controller._mirror_device = "adb-SER0-abcdef"
    controller.devicesChanged.emit()
    controller.notificationsChanged.emit()
    controller.mirroringChanged.emit()
    settle(app)
    controller._panel_opening = True                                      # overlay not up yet
    controller.mirroringChanged.emit()
    settle(app)
    controller._panel_opening = False
    controller._vr_session, controller._mirror_mode, controller._mirror_device = None, "", ""
    controller.mirroringChanged.emit()
    controller.devicesChanged.emit()
    settle(app)

    # Every device card with its More list and the Wireless debugging help open.
    def items(item):
        yield item
        for child in item.childItems():
            yield from items(child)
    window = engine.rootObjects()[0]
    content = window.contentItem()
    cards = [item for item in items(content) if item.property("showMore") is not None]
    assert len(cards) == len(statuses)
    texts = shown_texts(engine)
    assert "Plug your device into the Frame" in texts and "Plugged in" in texts
    assert "Tap Allow on your device to use the cable." in texts
    assert "A device is plugged in. Tap Allow on it to use the cable." in texts
    for card in cards:
        card.setProperty("showMore", True)
        card.setProperty("showReenable", True)
    settle(app)
    controller._usb_hint = ""
    assert warnings == []


@pytest.mark.parametrize("path", ["wireless", "wired"])
@pytest.mark.parametrize("step", [
    "setup", "brand", "devopts", "wireless", "pick_phone", "pair_open", "pair_code", "manual", "connecting",
    "oem_input", "companion", "tile", "tile_manual", "test", "usb_setup", "usb_plug", "usb_auth",
    "usb_perms", "usb_pick", "done",
])
def test_wizard_steps_render(ui, step, path):
    from scrcpy4frame.adb import DeviceEntry
    from scrcpy4frame.discovery import Service

    app, controller, engine, warnings = ui
    wiz = controller.wizard
    wiz.reset()
    wiz.ctx.path = path
    wiz.ctx.usb_candidates = [DeviceEntry("A1", "device", {"model": "Pixel_8"}), DeviceEntry("B2", "device")]
    wiz.step = step
    wiz.status = "error" if step in ("connecting", "companion") else "waiting"
    wiz.message = "Something to say"
    wiz.help = True
    wiz.history = ["brand"]
    wiz.ctx.target_host = "192.168.1.23"
    wiz.services = [Service("connect", "adb-A-abcdef", "192.168.1.23", 1)]
    wiz.ctx.candidates = [Service("connect", "adb-A-abcdef", "192.168.1.23", 1), Service("connect", "adb-B-abcdef", "192.168.1.24", 2)]
    wiz.ctx.pair_service = Service("pairing", "adb-A-abcdef", "192.168.1.23", 37099)
    wiz.ctx.companion_steps = {"install": "ok", "reconnect": "failed", "notifications": "skipped"}
    controller.set_wizard_active(True)
    controller.wizardChanged.emit()
    settle(app)
    texts = shown_texts(engine)
    if step == "setup":
        assert "How do you want to set up your device?" in texts
        assert {"Wireless", "Wired"} <= set(texts)
        assert "After setup, your device uses the cable when it's plugged in and Wi-Fi when it isn't." in texts
    if step == "usb_pick":
        assert {"Pixel 8", "B2"} <= set(texts)
    if step == "test":
        assert any(t.startswith("Connected to " if path == "wired" else "Paired with ") for t in texts)
    controller.set_wizard_active(False)
    settle(app)
    assert warnings == [], warnings


@pytest.mark.parametrize("page", ["settings", "logs", "home"])
@pytest.mark.parametrize("mirroring", [False, True])
def test_pages_render(ui, page, mirroring):
    app, controller, engine, warnings = ui
    controller._vr_session = object() if mirroring else None          # connected (VR panel) state
    controller._mirror_mode = "vr" if mirroring else ""
    controller.mirroringChanged.emit()
    engine.rootObjects()[0].setProperty("page", page)
    settle(app)
    controller._vr_session, controller._mirror_mode = None, ""
    controller.mirroringChanged.emit()
    settle(app)
    assert warnings == [], warnings


@pytest.mark.parametrize("page", ["home", "settings"])
def test_update_banner_and_button_render(ui, page):
    from scrcpy4frame.updater import Release

    app, controller, engine, warnings = ui
    engine.rootObjects()[0].setProperty("page", page)
    upd = controller.updater
    upd.release, upd.banner = Release((9, 9, 9), "https://example.invalid/b", 1), True
    for state, error in [("idle", ""), ("downloading", ""), ("installing", ""), ("idle", "It broke."),
                         ("installed", ""), ("installed", "Couldn't restart.")]:
        for game_mode in (False, True):
            upd.state, upd.error, upd.progress, upd.game_mode = state, error, 0.42, game_mode
            controller.updateChanged.emit()
            settle(app)
    upd.close_banner()
    settle(app)
    upd.release, upd.state, upd.error, upd.game_mode = None, "idle", "", False
    controller.updateChanged.emit()
    settle(app)
    assert warnings == [], warnings


def test_window_icon_is_one_128px_image(ui):
    # gamescope shows only the first _NET_WM_ICON image as the Frame's dashboard tab icon.
    from PySide6.QtCore import QSize

    from scrcpy4frame.__main__ import window_icon

    icon = window_icon()
    assert icon.availableSizes() == [QSize(128, 128)]
    assert icon.pixmap(QSize(128, 128)).toImage().size() == QSize(128, 128)


def test_no_wifi_is_not_shown_as_an_error(ui):
    # A Wired user may have no Wi-Fi at all; that's not a fault.
    app, controller, engine, warnings = ui
    engine.rootObjects()[0].setProperty("page", "home")
    settle(app)
    window = engine.rootObjects()[0]
    chips = [item for item in all_items(window.contentItem())
             if item.property("text") == "Not connected to Wi-Fi" and item.property("status") is not None]
    assert chips and all(chip.property("status") != "error" for chip in chips)


def test_audio_buffer_says_50_ms_is_for_usb(ui):
    app, controller, engine, warnings = ui
    engine.rootObjects()[0].setProperty("page", "settings")
    settle(app)
    assert any("50 ms is only recommended over USB" in t for t in shown_texts(engine))
    engine.rootObjects()[0].setProperty("page", "home")
    settle(app)

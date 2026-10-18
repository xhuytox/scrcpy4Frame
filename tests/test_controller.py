"""Controller logic with a fake adb, synchronous tasks and a stand-in scrcpy script."""

import os
import stat
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scrcpy4frame.adb import CmdResult, ConnectResult, DeviceEntry, DeviceInfo  # noqa: E402
from scrcpy4frame.devices import SavedDevice  # noqa: E402
from scrcpy4frame.discovery import Service  # noqa: E402

PIXEL = DeviceInfo(serialno="1A2B3C4D", model="Pixel 8", manufacturer="Google", release="15", sdk=35)


class FakeAdb:
    adb_path = "adb"

    def __init__(self):
        self.connected: set[str] = set()
        self.connect_result = ConnectResult(True, message="connected")
        self.uninstall_result = CmdResult(0, "Success", "")
        self.calls = []

    def shell(self, serial, command, timeout=15):
        self.calls.append(("shell", serial, command))
        return ""

    def connect(self, host, port):
        self.calls.append(("connect", host, port))
        if self.connect_result.ok:
            self.connected.add(f"{host}:{port}")
        return self.connect_result

    def wait_for_device(self, serial, timeout=10):
        return serial in self.connected

    def get_state(self, serial):
        return "device" if serial in self.connected else ""

    def device_info(self, serial):
        return PIXEL

    def disconnect(self, serial):
        self.calls.append(("disconnect", serial))
        self.connected.discard(serial)

    def uninstall(self, serial, package):
        self.calls.append(("uninstall", serial, package))
        return self.uninstall_result

    def devices(self):
        return [DeviceEntry(s, "device") for s in sorted(self.connected)]

    def forward(self, serial, remote):
        return None

    def forward_remove(self, serial, port):
        pass


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var))
    from PySide6.QtGui import QGuiApplication

    from scrcpy4frame.controller import AppController

    app = QGuiApplication.instance() or QGuiApplication([])
    controller = AppController(game_mode=False)
    controller.adb = FakeAdb()
    controller._ready = True

    def run_now(fn, *args, on_done=None, on_error=None):
        try:
            result = fn(*args)
        except Exception as exc:  # pragma: no cover - surfaced through on_error
            if on_error:
                on_error(exc)
            return
        if on_done:
            on_done(result)

    controller.runner.run = run_now
    yield controller, app
    controller.runner.shutdown()


def test_auto_connects_saved_phone_when_it_appears(ctl):
    controller, _ = ctl
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq", model="Pixel 8", last_port=1111))
    controller._on_services([])
    assert controller.devices[0]["status"] == "off"
    # Wireless debugging re-enabled: new port, and the mDNS name suffix may differ.
    controller._on_services([Service("connect", "adb-1A2B3C4D-Zz9yXx", "192.168.1.23", 41235)])
    assert controller.adb.calls[-1] == ("connect", "192.168.1.23", 41235)
    row = controller.devices[0]
    assert row["status"] == "connected" and row["host"] == "192.168.1.23"
    assert controller.store.get("adb-1A2B3C4D-vWgJpq").last_port == 41235


def test_dropped_connection_and_auth_failure(ctl):
    controller, _ = ctl
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq"))
    service = Service("connect", "adb-1A2B3C4D-vWgJpq", "192.168.1.23", 41235)
    controller._on_services([service])
    assert controller.devices[0]["status"] == "connected"
    calls = len(controller.adb.calls)
    controller._on_services([service])               # an mDNS refresh before the next adb poll
    assert controller.devices[0]["status"] == "connected" and len(controller.adb.calls) == calls
    controller.adb.connected.clear()                 # phone left Wi-Fi: nothing answers any more
    controller.adb.connect_result = ConnectResult(False, "unreachable", "No route to host")
    controller._on_services([])
    controller._on_entries(controller.adb.devices())
    assert controller.devices[0]["status"] == "off"
    controller.adb.connect_result = ConnectResult(False, "auth", "failed to authenticate")
    controller._on_services([service])
    assert controller.devices[0]["status"] == "needs_pair"


def test_failed_connect_backs_off(ctl):
    controller, _ = ctl
    controller.store.upsert(SavedDevice(id="tcpip-OLD1", method="tcpip", last_host="192.168.1.40", last_port=5555))
    controller.adb.connect_result = ConnectResult(False, "refused", "Connection refused")
    controller._on_entries([])
    assert controller.devices[0]["status"] == "unreachable"
    attempts = len(controller.adb.calls)
    controller._on_entries([])                        # inside the backoff window: no new attempt
    assert len(controller.adb.calls) == attempts


def _fake_scrcpy(tmp_path, body):
    script = tmp_path / "scrcpy"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _wait_for(app, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.02)
    return predicate()


def test_mirror_runs_scrcpy_and_explains_failures(ctl, tmp_path, monkeypatch):
    controller, app = ctl
    args_file = tmp_path / "args.txt"
    script = _fake_scrcpy(tmp_path, f'echo "$@" > {args_file}\n'
                                    'echo "[server] ERROR: Encoding error: android.media.MediaCodec$CodecException"\n'
                                    "exit 1")
    monkeypatch.setenv("SCRCPY4FRAME_SCRCPY", str(script))
    banners = []
    controller.banner.connect(lambda text, kind: banners.append(text))
    controller.mirror("nope")
    assert banners and not controller.mirroring

    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq", model="Pixel 8", sdk=35))
    controller._on_services([Service("connect", "adb-1A2B3C4D-vWgJpq", "192.168.1.23", 41235)])
    controller.mirror("adb-1A2B3C4D-vWgJpq")
    assert controller.mirroring
    assert _wait_for(app, lambda: not controller.mirroring)
    status = controller.mirrorStatus
    assert status["retrySmaller"] and "encoder" in status["message"]
    assert "--serial=192.168.1.23:41235" in args_file.read_text()
    controller.retryMirrorSmaller()
    assert _wait_for(app, lambda: not controller.mirroring)
    assert "--max-size=1280" in args_file.read_text()


def test_discovery_is_bound_once_per_address(ctl):
    from scrcpy4frame.netinfo import NetInfo

    controller, _ = ctl
    starts = []
    controller.discovery.start = lambda ip=None: starts.append(ip) or True
    controller._on_net(NetInfo())                       # offline: all interfaces
    controller._on_net(NetInfo())                       # unchanged: no restart
    controller._on_net(NetInfo(ipv4="192.168.1.10"))    # joined Wi-Fi: rebind
    controller._on_net(NetInfo(ipv4="192.168.1.10", ssid="HomeNet"))
    assert starts == [None, "192.168.1.10"]


def test_update_check_waits_for_the_network(ctl):
    from scrcpy4frame.netinfo import NetInfo

    controller, _ = ctl
    controller.discovery.start = lambda ip=None: True
    checks = []
    controller.updater.check = lambda: checks.append(1)
    controller._on_net(NetInfo())                       # offline at startup: GitHub can't answer
    assert checks == []
    controller._on_net(NetInfo(ipv4="192.168.1.10"))
    assert checks == [1]


def test_reconnects_last_address_when_mdns_is_silent(ctl):
    controller, _ = ctl
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq", last_host="192.168.1.23", last_port=41235))
    controller.adb.connect_result = ConnectResult(False, "refused", "Connection refused")
    controller._on_services([])                       # no mDNS: quiet attempt fails, stays "off"
    assert controller.adb.calls[-1] == ("connect", "192.168.1.23", 41235)
    assert controller.devices[0]["status"] == "off"
    attempts = len(controller.adb.calls)
    controller._on_services([])                       # within the retry window: no new attempt
    assert len(controller.adb.calls) == attempts
    controller.runtime["adb-1A2B3C4D-vWgJpq"].next_quiet_attempt = 0
    controller.adb.connect_result = ConnectResult(True, message="connected")
    controller._on_services([])                       # port still valid: connects without mDNS
    assert controller.devices[0]["status"] == "connected"


class FakeVrSession:
    instances = []

    def __init__(self, adb, serial, title, settings, runner, parent=None):
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            ended = Signal(str, str)
            event = Signal(str, "QVariantMap")

        self._signals = _Signals()
        self.ended = self._signals.ended
        self.event = self._signals.event
        self.serial, self.keys, self.recentered, self.started, self.stopped = serial, [], 0, False, False
        self.visibility = []
        self.recentered_now = 0
        self.theater = []
        self.dashboard_button = []
        self.audio_states = []
        self.settings = settings
        FakeVrSession.instances.append(self)

    def start(self):
        self.started = True

    def send_key(self, key):
        self.keys.append(key)

    def recenter(self, now=False):
        if now:
            self.recentered_now += 1
        else:
            self.recentered += 1

    def set_theater(self, on):
        self.theater.append(on)

    def set_panel_visible(self, visible):
        self.visibility.append(visible)

    def set_dashboard_button(self, enabled):
        self.dashboard_button.append(enabled)

    def set_audio_state(self, on, enabled):
        if not self.audio_states or self.audio_states[-1] != (on, enabled):
            self.audio_states.append((on, enabled))

    def stop(self, kind="stopped", message="", blocking=False):
        if not self.stopped:
            self.stopped = True
            self.ended.emit(kind, message)


def _connected_phone(controller):
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq", model="Pixel 8", sdk=35))
    controller._on_services([Service("connect", "adb-1A2B3C4D-vWgJpq", "192.168.1.23", 41235)])
    return "adb-1A2B3C4D-vWgJpq"


def test_vr_panel_is_used_and_remote_controls_route(ctl, monkeypatch):
    from scrcpy4frame import vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    device_id = _connected_phone(controller)
    controller.mirror(device_id)
    session = FakeVrSession.instances[-1]
    assert session.started and session.serial == "192.168.1.23:41235"
    assert controller.mirroring and controller.mirrorStatus["mode"] == "vr"
    assert controller.devices[0]["mirroring"]
    controller.mirrorKey("back")
    controller.mirrorKey("home")
    controller.recenterPanel()
    assert session.keys == ["back", "home"] and session.recentered == 1
    controller.stopMirror()
    assert not controller.mirroring and controller.mirrorStatus["mode"] == ""
    assert controller.mirrorStatus["message"] == ""


def test_home_shows_the_overlay_opening_until_its_first_frame(ctl, monkeypatch):
    from scrcpy4frame import vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    device_id = _connected_phone(controller)
    assert controller.mirrorStatus["opening"] is False
    controller.mirror(device_id)
    session = FakeVrSession.instances[-1]
    assert controller.mirrorStatus["opening"] is True
    changes = []
    controller.mirroringChanged.connect(lambda: changes.append(True))
    session.event.emit("first_frame", {})                      # the overlay shows with it
    assert controller.mirrorStatus["opening"] is False and changes
    controller.stopMirror()
    controller.mirror(device_id)
    assert controller.mirrorStatus["opening"] is True
    controller.stopMirror()                                    # cancelled before it showed
    assert controller.mirrorStatus["opening"] is False


def test_auto_mode_falls_back_to_window_without_vr(ctl, monkeypatch, tmp_path):
    from scrcpy4frame import vrmirror

    controller, app = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    script = _fake_scrcpy(tmp_path, "sleep 5")
    monkeypatch.setenv("SCRCPY4FRAME_SCRCPY", str(script))
    device_id = _connected_phone(controller)
    controller.mirror(device_id)
    FakeVrSession.instances[-1].stop("vr_unavailable", "VRInitError_Init_HmdNotFound")
    assert controller.mirroring and controller.mirrorStatus["mode"] == "window"
    controller.stopMirror()
    assert _wait_for(app, lambda: not controller.mirroring)


def test_window_when_the_overlay_helper_is_missing(ctl, monkeypatch, tmp_path):
    from scrcpy4frame import vrmirror

    controller, app = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: False)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    monkeypatch.setenv("SCRCPY4FRAME_SCRCPY", str(_fake_scrcpy(tmp_path, "exit 0")))
    controller.mirror(_connected_phone(controller))
    assert not FakeVrSession.instances and controller.mirrorStatus["mode"] == "window"
    assert _wait_for(app, lambda: not controller.mirroring)


class FakeScreen:
    instances = []

    def __init__(self, adb, serial):
        self.serial, self.sent, self.closed = serial, [], False
        FakeScreen.instances.append(self)

    def set_display_power(self, on):
        self.sent.append(on)
        return on

    def close(self):
        self.closed = True


def test_live_phone_screen_toggle_while_mirroring(ctl, monkeypatch):
    from scrcpy4frame import screencontrol, vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    FakeScreen.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    monkeypatch.setattr(screencontrol, "ScreenSession", FakeScreen)
    controller.setPhoneScreen(False)                     # not mirroring: ignored
    assert not FakeScreen.instances
    controller.mirror(_connected_phone(controller))
    assert controller.mirrorStatus["screenOn"] is True   # default: the phone's screen stays on
    controller.setPhoneScreen(False)
    assert controller.mirrorStatus["screenOn"] is False and FakeScreen.instances[-1].sent == [False]
    controller.setPhoneScreen(True)
    assert controller.mirrorStatus["screenOn"] is True
    controller.stopMirror()
    assert FakeScreen.instances[-1].closed               # closing restores the display on the phone


def test_each_connect_opens_the_panel_afresh(ctl, monkeypatch):
    from scrcpy4frame import vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    controller.mirror(_connected_phone(controller))
    FakeVrSession.instances[-1].event.emit(
        "placement", {"pose": "1,0,0,0.2,0,1,0,1.4,0,0,1,-0.7", "panel_size_m": 0.437})   # moved and resized
    controller.stopMirror()
    controller.mirror(_connected_phone(controller))
    # In front of the user at Settings' size: nothing is carried over from the last session.
    message = vrmirror.connect_message(1, "x", FakeVrSession.instances[-1].settings)
    assert "pose" not in message and message["panel_size_m"] == 0.30


def test_panel_can_be_hidden_while_mirroring(ctl, monkeypatch):
    from scrcpy4frame import vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    controller.mirror(_connected_phone(controller))
    session = FakeVrSession.instances[-1]
    assert controller.mirrorStatus["panelVisible"] is True
    session.event.emit("visibility", {"visible": False})      # the panel's own Hide button
    assert controller.mirrorStatus["panelVisible"] is False and controller.mirroring
    controller.setPanelVisible(True)                           # Home: "Hidden" shows it in front of you
    assert session.recentered_now == 1 and session.recentered == 0 and session.visibility == []
    session.event.emit("visibility", {"visible": True})
    assert controller.mirrorStatus["panelVisible"] is True
    controller.setPanelVisible(False)                          # Home: "Shown" hides it
    assert session.visibility == [False]
    session.event.emit("visibility", {"visible": False})
    controller.stopMirror()
    controller.mirror(_connected_phone(controller))           # a new session starts visible
    assert controller.mirrorStatus["panelVisible"] is True


def test_theater_toggle_shows_what_the_panel_reports(ctl, monkeypatch):
    from scrcpy4frame import vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    controller.mirror(_connected_phone(controller))
    session = FakeVrSession.instances[-1]
    assert controller.mirrorStatus["theater"] is False
    controller.setTheater(True)                                # Home: "Theater off"
    assert session.theater == [True] and controller.mirrorStatus["theater"] is False
    session.event.emit("theater", {"on": True})
    assert controller.mirrorStatus["theater"] is True
    session.event.emit("theater", {"on": False})               # the panel's own Theater button
    assert controller.mirrorStatus["theater"] is False
    session.event.emit("theater", {"on": True})
    controller.stopMirror()
    controller.mirror(_connected_phone(controller))           # theater never carries over
    assert controller.mirrorStatus["theater"] is False


def test_dashboard_button_setting_reaches_the_running_panel(ctl, monkeypatch):
    from scrcpy4frame import vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    assert controller.settings.value.dashboard_button is True             # on by default
    controller.setSetting("dashboard_button", False)                      # no session yet: just saved
    assert controller.settings.value.dashboard_button is False
    controller.mirror(_connected_phone(controller))
    session = FakeVrSession.instances[-1]
    assert vrmirror.connect_message(1, "x", controller.settings.value)["dashboard_button"] is False
    controller.setSetting("dashboard_button", True)                       # live, while mirroring
    assert session.dashboard_button == [True]
    assert controller.settingsView["dashboard_button"] is True


def test_hidden_overlay_lets_notifications_pop_up_and_open_shows_it(ctl, monkeypatch):
    from scrcpy4frame import vrmirror

    controller, _ = ctl
    FakeVrSession.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    device_id = _connected_phone(controller)
    controller.settings.update("notify_display", "app")
    toasts = []
    controller.center.inAppToast.connect(toasts.append)
    message = {"key": "k1", "pkg": "com.chat", "appLabel": "Chat", "title": "Hi", "text": "there"}

    controller.mirror(device_id)
    session = FakeVrSession.instances[-1]
    controller.center.posted(device_id, message, controller._visible_mirror_device())
    assert toasts == []                                  # the overlay shows the phone's own pop-up
    session.event.emit("visibility", {"visible": False})
    controller.center.posted(device_id, {**message, "key": "k2"}, controller._visible_mirror_device())
    assert len(toasts) == 1                              # hidden: it pops up here instead
    controller.settings.update("suppress_while_visible", False)
    session.event.emit("visibility", {"visible": True})
    controller.center.posted(device_id, {**message, "key": "k3"}, controller._visible_mirror_device())
    assert len(toasts) == 2                              # setting off: always pops up

    session.event.emit("visibility", {"visible": False})
    controller.openNotification(toasts[0]["uid"])        # Open brings the hidden overlay back
    assert session.visibility == [True]
    assert ("shell", "192.168.1.23:41235", "monkey -p com.chat -c android.intent.category.LAUNCHER 1") \
        in controller.adb.calls
    session.event.emit("visibility", {"visible": True})
    controller.openNotification(toasts[0]["uid"])        # already visible: nothing to do
    assert session.visibility == [True]


class FakeAudioCast:
    instances = []

    def __init__(self, serial, buffer_ms, parent=None):
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            started = Signal()
            stopped = Signal()
            ended = Signal(int, str)

        self._signals = _Signals()
        self.started, self.stopped, self.ended = (self._signals.started, self._signals.stopped,
                                                  self._signals.ended)
        self.serial, self.buffer_ms = serial, buffer_ms
        self.running, self.is_live, self.is_stopping = False, False, False
        FakeAudioCast.instances.append(self)

    def start(self):
        self.running = True

    def go_live(self):
        self.is_live = True
        self.started.emit()

    def stop(self, blocking=False):
        self.is_stopping = True
        if blocking:
            self.exit()

    def exit(self):                      # the client has finished exiting after stop()
        self.running = False
        self.stopped.emit()

    def deleteLater(self):
        pass


@pytest.fixture
def vr_audio(ctl, monkeypatch):
    from scrcpy4frame import audiocast, vrmirror

    FakeVrSession.instances.clear()
    FakeAudioCast.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: True)
    monkeypatch.setattr(vrmirror, "VrMirrorSession", FakeVrSession)
    monkeypatch.setattr(audiocast, "AudioCast", FakeAudioCast)
    return ctl[0]


def test_audio_casting_is_off_until_turned_on(vr_audio):
    controller = vr_audio
    controller.mirror(_connected_phone(controller))
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    status = controller.mirrorStatus
    assert status["audioCasting"] is False and status["audioSupported"] is True
    assert not FakeAudioCast.instances                     # off at every launch
    controller.setAudioCasting(True)
    audio = FakeAudioCast.instances[-1]
    assert audio.running and audio.serial == "192.168.1.23:41235"
    status = controller.mirrorStatus
    assert status["audioBusy"] is True and status["audioCasting"] is False   # until the client is up
    audio.go_live()
    status = controller.mirrorStatus
    assert status["audioBusy"] is False and status["audioCasting"] is True
    controller.setAudioCasting(False)
    status = controller.mirrorStatus
    assert audio.is_stopping and status["audioCasting"] is True and status["audioBusy"] is True
    audio.exit()                                            # off once the client has exited
    status = controller.mirrorStatus
    assert status["audioCasting"] is False and status["audioBusy"] is False
    assert not hasattr(controller.settings.value, "audio")  # the choice is never saved


def test_panel_audio_button_casts_and_shows_what_home_shows(vr_audio):
    controller = vr_audio
    controller.mirror(_connected_phone(controller))
    session = FakeVrSession.instances[-1]
    session.event.emit("first_frame", {})
    assert session.audio_states[-1] == (False, True)
    session.event.emit("audio_cast", {"on": True})        # pressed in the More menu
    audio = FakeAudioCast.instances[-1]
    assert audio.running
    assert session.audio_states[-1] == (False, False)      # busy until the client is up
    audio.go_live()
    assert session.audio_states[-1] == (True, True)
    session.event.emit("audio_cast", {"on": False})
    assert audio.is_stopping and session.audio_states[-1] == (True, False)
    audio.exit()
    assert session.audio_states[-1] == (False, True)
    assert controller.mirrorStatus["audioCasting"] is False


def test_audio_casting_lasts_for_the_app_run_and_waits_for_the_panel(vr_audio):
    controller = vr_audio
    device_id = _connected_phone(controller)
    controller.setAudioCasting(True)                        # chosen before connecting
    controller.mirror(device_id)
    assert controller.mirrorStatus["audioBusy"] is True
    assert not FakeAudioCast.instances                     # not until the panel shows a frame
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    first = FakeAudioCast.instances[-1]
    assert first.running
    first.go_live()
    controller.stopMirror()
    assert first.is_stopping and controller.mirrorStatus["audioBusy"] is True
    controller.mirror(device_id)                            # reconnecting casts again,
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    assert len(FakeAudioCast.instances) == 1                # once the old client has exited
    first.exit()
    assert len(FakeAudioCast.instances) == 2 and FakeAudioCast.instances[-1].running


def test_audio_buffer_setting_restarts_casting_with_the_new_buffer(vr_audio):
    controller = vr_audio
    controller.mirror(_connected_phone(controller))
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    controller.setAudioCasting(True)
    first = FakeAudioCast.instances[-1]
    assert first.buffer_ms == 50                            # the default
    first.go_live()
    controller.setSetting("audio_buffer_ms", 50)            # same value: keeps casting
    assert not first.is_stopping
    controller.setSetting("audio_buffer_ms", 20)
    assert first.is_stopping and controller.mirrorStatus["audioBusy"] is True
    first.exit()                                            # casts again once the old client has exited
    assert len(FakeAudioCast.instances) == 2
    second = FakeAudioCast.instances[-1]
    assert second.running and second.buffer_ms == 20
    assert controller.mirrorStatus["audioBusy"] is True


def test_audio_buffer_setting_waits_for_casting(vr_audio):
    controller = vr_audio
    controller.setSetting("audio_buffer_ms", 30)            # not casting: nothing to restart
    assert not FakeAudioCast.instances
    controller.mirror(_connected_phone(controller))
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    controller.setAudioCasting(True)
    assert FakeAudioCast.instances[-1].buffer_ms == 30


def test_audio_casting_that_stops_by_itself_shows_as_off(vr_audio):
    controller = vr_audio
    banners = []
    controller.banner.connect(lambda text, kind: banners.append(text))
    controller.mirror(_connected_phone(controller))
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    controller.setAudioCasting(True)
    FakeAudioCast.instances[-1].go_live()
    FakeAudioCast.instances[-1].ended.emit(1, "Audio capture failed")
    status = controller.mirrorStatus
    assert status["audioCasting"] is False and status["audioBusy"] is False
    assert banners == ["Audio casting stopped. Audio capture failed"]
    controller.setAudioCasting(True)
    FakeAudioCast.instances[-1].ended.emit(2, "Device disconnected")   # the mirror reports drops itself
    status = controller.mirrorStatus
    assert status["audioCasting"] is False and status["audioBusy"] is False and len(banners) == 1


def test_old_android_cannot_cast_audio(vr_audio):
    controller = vr_audio
    controller.adb.device_info = lambda serial: replace(PIXEL, release="10", sdk=29)
    controller.setAudioCasting(True)
    controller.mirror(_connected_phone(controller))
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    status = controller.mirrorStatus
    assert status["audioSupported"] is False and status["audioCasting"] is False
    assert status["audioBusy"] is False and not FakeAudioCast.instances


def test_window_mode_casts_audio_once_scrcpy_is_up(ctl, monkeypatch, tmp_path):
    from scrcpy4frame import audiocast, vrmirror

    controller, app = ctl
    FakeAudioCast.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: False)
    monkeypatch.setattr(audiocast, "AudioCast", FakeAudioCast)
    args_file = tmp_path / "args.txt"
    monkeypatch.setenv("SCRCPY4FRAME_SCRCPY", str(_fake_scrcpy(
        tmp_path, f'echo "$@" > {args_file}\nsleep 0.3\n'
                  'echo "[server] INFO: Device: [Google] google Pixel 8 (Android 15)"\nexec sleep 5')))
    controller.setAudioCasting(True)
    controller.mirror(_connected_phone(controller))
    assert controller.mirrorStatus["mode"] == "window" and not FakeAudioCast.instances
    assert _wait_for(app, lambda: FakeAudioCast.instances)  # after scrcpy's server has started
    assert FakeAudioCast.instances[-1].running
    assert "--no-audio" in args_file.read_text()             # the window never carries audio
    controller.stopMirror()
    assert _wait_for(app, lambda: not controller.mirroring)
    assert FakeAudioCast.instances[-1].is_stopping


def test_window_mode_casts_audio_even_without_the_server_log(ctl, monkeypatch, tmp_path):
    from scrcpy4frame import audiocast, controller as controller_module, vrmirror

    controller, app = ctl
    FakeAudioCast.instances.clear()
    monkeypatch.setattr(vrmirror, "available", lambda: False)
    monkeypatch.setattr(audiocast, "AudioCast", FakeAudioCast)
    monkeypatch.setattr(controller_module, "AUDIO_JOIN_FALLBACK_MS", 200)
    monkeypatch.setenv("SCRCPY4FRAME_SCRCPY", str(_fake_scrcpy(tmp_path, "exec sleep 5")))
    controller.setAudioCasting(True)
    controller.mirror(_connected_phone(controller))
    assert _wait_for(app, lambda: FakeAudioCast.instances)
    controller.stopMirror()
    assert _wait_for(app, lambda: not controller.mirroring)


def test_quitting_waits_for_the_audio_client(vr_audio):
    controller = vr_audio
    controller.mirror(_connected_phone(controller))
    FakeVrSession.instances[-1].event.emit("first_frame", {})
    controller.setAudioCasting(True)
    audio = FakeAudioCast.instances[-1]
    controller.shutdown()
    assert audio.is_stopping and not audio.running          # the phone gets its sound back


class FakeWaker:
    def __init__(self):
        self.sent = []

    def wake(self, targets):
        self.sent.append(list(targets))

    def close(self):
        pass


class FakeLink:
    def __init__(self, state="connected"):
        self.state, self.stops = state, []

    def stop(self, bye=False):
        self.stops.append(bye)


def test_wakes_companions_it_cannot_reach(ctl):
    from scrcpy4frame.netinfo import NetInfo

    controller, _ = ctl
    controller._waker = waker = FakeWaker()
    controller.net = NetInfo(ipv4="192.168.1.10", prefix=24)
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq", serialno="1A2B3C4D", companion=True,
                                        wake_port=38490, last_host="192.168.1.23"))
    controller.store.upsert(SavedDevice(id="adb-OLD-aaaaaa", serialno="OLD", companion=True,
                                        last_host="192.168.1.24"))          # old companion: no wake port
    controller.store.upsert(SavedDevice(id="adb-GONE-bbbbbb", serialno="GONE", wake_port=38490,
                                        last_host="192.168.1.25"))          # companion removed
    controller._wake_devices()
    assert waker.sent == [[("192.168.1.23", 38490), ("192.168.1.255", 38490)]]
    controller._woke("192.168.1.23")                  # it confirmed: no wakes for a while
    controller._wake_devices()
    assert len(waker.sent) == 1
    controller._woken_until.clear()
    controller.links["adb-1A2B3C4D-vWgJpq"] = FakeLink("connected")   # its pings keep it on
    controller._wake_devices()
    assert len(waker.sent) == 1
    controller.links["adb-1A2B3C4D-vWgJpq"] = FakeLink("waiting")
    controller._wake_devices()
    assert len(waker.sent) == 2


def test_companion_hello_records_its_wake_port(ctl):
    controller, _ = ctl
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq", companion=True))
    controller._companion_hello("adb-1A2B3C4D-vWgJpq", {"type": "hello", "wakePort": 38490})
    assert controller.store.get("adb-1A2B3C4D-vWgJpq").wake_port == 38490
    controller._companion_hello("adb-1A2B3C4D-vWgJpq", {"type": "hello", "wakePort": True})
    controller._companion_hello("adb-1A2B3C4D-vWgJpq", {"type": "hello", "protocol": 1})
    assert controller.store.get("adb-1A2B3C4D-vWgJpq").wake_port == 38490
    controller._companion_hello("adb-1A2B3C4D-vWgJpq", {"type": "hello", "wakePort": 0})   # Android 10
    assert controller.store.get("adb-1A2B3C4D-vWgJpq").wake_port == 0


def test_companion_note_explains_a_restarted_listener(ctl):
    controller, _ = ctl
    xiaomi, pixel = "adb-XIAOMI-aaaaaa", "adb-PIXEL-bbbbbb"
    controller.store.upsert(SavedDevice(id=xiaomi, serialno="XIAOMI", manufacturer="Xiaomi", companion=True))
    controller.store.upsert(SavedDevice(id=pixel, serialno="PIXEL", manufacturer="Google", companion=True))
    controller.links.update({xiaomi: FakeLink("waiting"), pixel: FakeLink("waiting")})

    def notes():
        return {row["id"]: row["companionNote"] for row in controller.devices}

    controller._companion_listener_checked(xiaomi, "running")
    controller._companion_listener_checked(pixel, "error")
    assert notes() == {xiaomi: "", pixel: ""}
    controller._companion_listener_checked(xiaomi, "restarted")
    controller._companion_listener_checked(pixel, "failed")
    assert "Autostart" in notes()[xiaomi]
    assert "off and on again" in notes()[pixel]
    controller.links[xiaomi].state = controller.links[pixel].state = "connected"
    assert "Autostart" in notes()[xiaomi]             # it'll be stopped again unless allowed to run
    assert notes()[pixel] == ""                       # it came back after all
    controller.links[pixel].state = "waiting"
    controller._companion_listener_checked(pixel, "revoked")
    assert "Notification access" in notes()[pixel]
    controller._companion_listener_checked(pixel, "restarted")
    assert "Autostart" not in notes()[pixel] and "battery settings" in notes()[pixel]
    controller.removeCompanion(xiaomi)
    assert notes()[xiaomi] == ""


def test_says_bye_only_when_the_app_leaves(ctl):
    controller, _ = ctl
    controller.server = None                          # no adb server to stop
    kept, forgotten, last = FakeLink(), FakeLink(), FakeLink()
    controller.store.upsert(SavedDevice(id="adb-A-aaaaaa", serialno="A", companion=True))
    controller.store.upsert(SavedDevice(id="adb-B-bbbbbb", serialno="B", companion=True))
    controller.links.update({"adb-A-aaaaaa": forgotten, "adb-B-bbbbbb": kept})
    controller.removeCompanion("adb-B-bbbbbb")        # uninstalls over adb, so the device must stay on
    assert kept.stops == [False]
    controller.forgetDevice("adb-A-aaaaaa")           # so does forgetting it
    assert forgotten.stops == [False]
    controller.links["adb-C-cccccc"] = last
    controller.shutdown()
    assert last.stops == [True]


def _connected_with_companion(controller, device_id="adb-1A2B3C4D-vWgJpq"):
    controller.store.upsert(SavedDevice(id=device_id, serialno="1A2B3C4D", nickname="Work phone",
                                        companion=True, wake_port=38490, qs_tile=True))
    controller._on_services([Service("connect", device_id, "192.168.1.23", 41235)])
    assert controller.devices[0]["status"] == "connected"
    controller.center.posted(device_id, {"key": "k1", "pkg": "com.whatsapp", "title": "Alice"}, "")
    assert controller.notifications
    return device_id


def test_app_update_brings_companions_on_connected_devices_up_to_date(ctl):
    from scrcpy4frame.updater import CompanionTarget

    controller, _ = ctl
    device_id = _connected_with_companion(controller)
    controller.store.upsert(SavedDevice(id="adb-PLAIN-aaaaaa", serialno="PLAIN"))
    controller._on_services([Service("connect", device_id, "192.168.1.23", 41235),
                             Service("connect", "adb-PLAIN-aaaaaa", "192.168.1.24", 37001)])
    controller.store.upsert(SavedDevice(id="adb-OFF-bbbbbb", serialno="OFF", companion=True))
    assert [row["status"] for row in controller.devices] == ["connected", "connected", "off"]
    assert controller.updater.companions() == [CompanionTarget("Work phone", "192.168.1.23:41235", PIXEL.sdk)]


def test_updates_an_older_companion_once_when_it_says_hello(ctl, monkeypatch):
    from scrcpy4frame import companion

    controller, _ = ctl
    banners, updates = [], []
    controller.banner.connect(lambda text, kind: banners.append((text, kind)))
    device_id = _connected_with_companion(controller)
    controller._companion_version = 3
    monkeypatch.setattr(companion, "update", lambda adb, serial, apk, version, sdk: updates.append(
        (serial, version, sdk)) or companion.InstallReport(ok=True))
    for version in (3, 4, "2", True, None):           # as new, newer, or not a number
        controller._companion_hello(device_id, {"type": "hello", "versionCode": version})
    controller._companion_version = None              # no bundled APK
    controller._companion_hello(device_id, {"type": "hello", "versionCode": 2})
    assert updates == [] and banners == []

    controller._companion_version = 3
    controller._companion_hello(device_id, {"type": "hello", "versionCode": 2})
    assert updates == [("192.168.1.23:41235", 3, PIXEL.sdk)]
    assert banners == [("Updating the companion app on Work phone…", "info"),
                       ("The companion app on Work phone is updated.", "info")]
    controller._companion_hello(device_id, {"type": "hello", "versionCode": 2})     # once per run
    assert len(updates) == 1


def test_says_when_updating_an_older_companion_fails(ctl, monkeypatch):
    from scrcpy4frame import companion

    controller, _ = ctl
    banners = []
    controller.banner.connect(lambda text, kind: banners.append((text, kind)))
    device_id = _connected_with_companion(controller)
    controller._companion_version = 3
    monkeypatch.setattr(companion, "update", lambda *args: companion.InstallReport(
        steps={"install": "failed"}, error="Your device blocked the install."))
    controller._companion_hello(device_id, {"type": "hello", "versionCode": 2})
    assert banners[-1] == ("Updating the companion app on Work phone didn't finish: Your device blocked the "
                           "install. scrcpy4Frame tries again the next time it starts.", "error")


def test_forgetting_a_device_uninstalls_its_companion_and_drops_its_settings(ctl):
    from scrcpy4frame import companion

    controller, _ = ctl
    banners = []
    controller.banner.connect(lambda text, kind: banners.append((text, kind)))
    device_id = _connected_with_companion(controller)
    controller.forgetDevice(device_id)
    calls = controller.adb.calls
    # Wireless debugging goes off last: the companion that would have done it is gone.
    steps = [("uninstall", "192.168.1.23:41235", companion.PACKAGE),
             ("shell", "192.168.1.23:41235", "settings put global adb_wifi_enabled 0"),
             ("disconnect", "192.168.1.23:41235")]
    assert [c for c in calls if c in steps] == steps
    assert controller.devices == [] and controller.notifications == []
    assert device_id not in controller.links and device_id not in controller.runtime
    assert banners == [("Companion removed.", "info")]
    # Set up again, it starts from scratch.
    controller.store.load()
    assert controller.store.all() == []
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-Zz9yXx", serialno="1A2B3C4D"))
    again = controller.store.get("adb-1A2B3C4D-Zz9yXx")
    assert not again.companion and not again.nickname and not again.wake_port and not again.qs_tile


def test_forgetting_a_device_says_when_its_companion_stays(ctl):
    controller, _ = ctl
    banners = []
    controller.banner.connect(lambda text, kind: banners.append((text, kind)))
    device_id = _connected_with_companion(controller)
    controller.adb.uninstall_result = CmdResult(1, "", "Failure [DELETE_FAILED_INTERNAL_ERROR]")
    controller.forgetDevice(device_id)
    assert controller.devices == []
    assert banners == [("Couldn't uninstall the companion from Work phone. Uninstall it on the device.", "error")]
    assert ("shell", "192.168.1.23:41235", "settings put global adb_wifi_enabled 0") in controller.adb.calls
    assert ("disconnect", "192.168.1.23:41235") in controller.adb.calls

    banners.clear()                                   # not connected: nothing can reach it
    controller.store.upsert(SavedDevice(id="adb-OFF-aaaaaa", serialno="OFF", model="Pixel 7", companion=True))
    calls = len(controller.adb.calls)
    controller.forgetDevice("adb-OFF-aaaaaa")
    assert controller.adb.calls[calls:] == []
    assert controller.store.get("adb-OFF-aaaaaa") is None
    assert banners == [("Pixel 7 is forgotten, but its companion app is still on it. "
                        "Uninstall it on the device.", "error")]


def test_forgetting_a_cable_set_up_device_leaves_wireless_debugging_alone(ctl):
    from scrcpy4frame import companion

    controller, _ = ctl
    controller.store.upsert(SavedDevice(id="tcpip-1A2B3C4D", method="tcpip", serialno="1A2B3C4D", companion=True,
                                        last_host="192.168.1.23", last_port=5555))
    controller._on_entries([])
    assert controller.devices[0]["status"] == "connected"
    controller.forgetDevice("tcpip-1A2B3C4D")
    assert ("uninstall", "192.168.1.23:5555", companion.PACKAGE) in controller.adb.calls
    assert not any("adb_wifi_enabled" in str(c) for c in controller.adb.calls)


def test_forgetting_a_device_without_the_companion_only_disconnects(ctl):
    controller, _ = ctl
    banners = []
    controller.banner.connect(lambda text, kind: banners.append((text, kind)))
    controller.store.upsert(SavedDevice(id="adb-1A2B3C4D-vWgJpq", serialno="1A2B3C4D"))
    controller._on_services([Service("connect", "adb-1A2B3C4D-vWgJpq", "192.168.1.23", 41235)])
    controller.forgetDevice("adb-1A2B3C4D-vWgJpq")
    assert not any(c[0] == "uninstall" or "adb_wifi_enabled" in str(c) for c in controller.adb.calls)
    assert ("disconnect", "192.168.1.23:41235") in controller.adb.calls
    assert controller.devices == [] and banners == []

"""The Qt-facing application controller, exposed to QML as `app`."""

from __future__ import annotations

import functools
import logging
import math
import time
from collections import deque
from dataclasses import asdict, dataclass, replace

from PySide6.QtCore import (Property, QObject, QProcess, QProcessEnvironment, QTimer, Signal,
                            Slot)

from . import (__version__, audiocast, companion, guides, handoff, netinfo, paths, phonechecks,
               screencontrol, vrmirror, wake)
from .adb import TCPIP_PORT, Adb, AdbServer, DeviceEntry, DeviceInfo, PairResult, is_tcp_serial, make_env
from .companion_link import CompanionLink
from .devices import NONE, SETUP_WIRED, SETUP_WIRELESS, TCPIP, WIRELESS, DeviceStore, SavedDevice
from .discovery import Discovery, Service, serial_from_guid
from .mirror import EXIT_DISCONNECTED, build_args, classify
from .notify.center import NotificationCenter
from .settings import SettingsStore
from .tasks import TaskRunner
from .updater import CompanionTarget, Updater
from .wizard import DONE, TEST, Wizard, brand_for_manufacturer

log = logging.getLogger(__name__)

POLL_MS = 3000
USB_POLL_MS = 1000
NET_POLL_MS = 10_000
QUIET_RETRY_S = 60
AUDIO_JOIN_FALLBACK_MS = 8000
WAKE_EVERY_MS = 5000
WAKE_HOLD_S = 30            # a woken companion keeps Wireless debugging on for 60 s without contact
WOKEN_QUERY_MS = (1000, 3000)   # when to look for a woken device's new adb port over mDNS

HANDOFF_USB_GRACE_S = 10    # after `adb tcpip`, how long a missing USB entry counts as coming back
HANDOFF_RETRY_S = 60        # how soon to look again for a plugged-in device on the Frame's network
HANDOFF_QUERY_MS = (1000, 4000, 10_000)     # when to ask mDNS for a device's new Wireless debugging port
SWITCHING = "Your device is switching connections. Try again in a few seconds."
WIFI_RESUME_S = 15          # after a USB mirror ends with the cable pulled, how long to wait for Wi-Fi
WIFI_CHECK_S = 5            # how often to check that a Wi-Fi connection still reaches its device
WIFI_CHECK_TIMEOUT_S = 5
WIFI_CHECK_TRIES = 2        # unanswered checks in a row before the connection counts as gone
BACK_SOON = "Your screen will be back in a few seconds."

# A tcpip device that doesn't answer: either it restarted (Wi-Fi mode is gone) or it's off the Wi-Fi.
TCPIP_UNREACHABLE = "Plug your device into the Frame or, if it hasn't restarted, put it on the same Wi-Fi."
USB_HINT_STATES = ("unauthorized", "no permissions")
# The Frame only becomes the USB host when the device hands control over, and that resets on every plug-in.
USB_CONTROL_HINT = "Plugged in? On the device, tap the USB notification. "

WIZARD_COMMANDS = {
    "choose_setup", "pick_usb", "choose_brand", "next", "back", "show_help", "pick", "not_mine", "use_manual", "use_usb",
    "submit_code", "manual_submit", "retry", "skip", "accept_companion", "decline_companion",
    "add_tile", "check_again", "start_mirror", "finish",
}


@dataclass
class Runtime:
    wifi_serial: str = ""       # adb serial ("ip:port") while connected over Wi-Fi
    usb_serial: str = ""        # adb serial while plugged in
    usb_state: str = ""         # "" | device | unauthorized | no permissions | returning (adbd restarting)
    status: str = "off"         # connected | connecting | ready | off | unreachable | needs_pair | unplugged
    detail: str = ""
    connecting: bool = False
    next_attempt: float = 0.0
    failures: int = 0
    next_quiet_attempt: float = 0.0     # retry of the last known address while mDNS can't see it
    listener_check: str = ""            # last notable companion.restart_listener() outcome
    ops: int = 0                        # adb work in flight that `adb tcpip` would cut off
    handoff: str = ""                   # "" | checking | wireless | tcpip: the Wi-Fi hand-off's phase
    next_handoff: float = 0.0           # monotonic time the hand-off may start again
    handoff_gen: int = 0                # bumped on unplug, so late hand-off results are dropped
    usb_grace_until: float = 0.0        # after `adb tcpip`, a missing USB entry is "returning" until then
    auth_failed: bool = False           # Wi-Fi refused our key: don't retry until asked or plugged in
    wifi_checking: bool = False         # checking that the Wi-Fi connection still reaches the device
    next_wifi_check: float = 0.0
    wifi_answered_at: float = 0.0       # monotonic start of the last check the device answered

    @property
    def via(self) -> str:
        """The connection in use: the cable wins."""
        if self.usb_state in ("device", "returning"):
            return "usb"
        return "wifi" if self.wifi_serial else ""

    @property
    def serial(self) -> str:
        # While USB is returning, work goes over Wi-Fi.
        return self.usb_serial if self.usb_state == "device" else self.wifi_serial


@dataclass
class ConnectOutcome:
    ok: bool
    serial: str = ""
    info: DeviceInfo | None = None
    error: str = ""
    message: str = ""


class _WizardActions:
    """Implements wizard.Actions by running adb work on the task runner."""

    def __init__(self, app: "AppController"):
        self.app = app

    @property
    def wizard(self) -> Wizard:
        return self.app.wizard

    def pair(self, host, port, code):
        self.app.runner.run(self.app.adb.pair, host, port, code, on_done=self.wizard.pair_done,
                            on_error=lambda e: self.wizard.pair_done(PairResult(False, error="other", message=str(e))))

    def connect(self, host, port):
        self.app.runner.run(self.app.connect_blocking, host, port,
                            on_done=lambda r: self.wizard.connect_done(r.ok, r.serial, r.info, r.error, r.message),
                            on_error=lambda e: self.wizard.connect_done(False, message=str(e)))

    def check_input(self, serial):
        self.app.runner.run(phonechecks.input_blocked, self.app.adb, serial,
                            on_done=self.wizard.input_checked,
                            on_error=lambda e: self.wizard.input_checked(False))

    def install_companion(self, serial, sdk):
        apk = paths.companion_apk_path()
        self.app.runner.run(companion.install_and_grant, self.app.adb, serial, apk, sdk,
                            on_done=self.wizard.companion_done,
                            on_error=lambda e: self.wizard.companion_done(
                                companion.InstallReport(error=f"Installing the companion failed: {e}")))

    def add_tile(self, serial):
        self.app.runner.run(companion.add_tile, self.app.adb, serial, on_done=self.wizard.tile_done,
                            on_error=lambda e: self.wizard.tile_done(False))

    def check_tile(self, serial):
        self.app.runner.run(companion.check_tile, self.app.adb, serial, on_done=self.wizard.tile_done,
                            on_error=lambda e: self.wizard.tile_done(False))

    def usb_poll(self, enabled):
        self.app.set_usb_poll(enabled)

    def read_usb_device(self, usb_serial):
        self.app.runner.run(self.app.adb.device_info, usb_serial, on_done=self.wizard.usb_device_read,
                            on_error=lambda e: self.wizard.usb_device_read(None))

    def save_device(self, device):
        return self.app.device_saved(device)

    def start_mirror(self, device_id):
        self.app.set_wizard_active(False)
        self.app.mirror(device_id)

    def finish(self, device_id):
        self.app.set_wizard_active(False)


class AppController(QObject):
    devicesChanged = Signal()
    wizardChanged = Signal()
    wizardActiveChanged = Signal()
    mirroringChanged = Signal()
    netChanged = Signal()
    statusChanged = Signal()
    notificationsChanged = Signal()
    settingsChanged = Signal()
    updateChanged = Signal()
    toast = Signal("QVariantMap")
    banner = Signal(str, str)               # text, kind (info | error)
    _servicesFromThread = Signal(list)
    _discoveryErrorFromThread = Signal(str)

    def __init__(self, game_mode: bool, parent: QObject | None = None):
        super().__init__(parent)
        self.game_mode = game_mode
        self.runner = TaskRunner(self)
        self.settings = SettingsStore(paths.config_dir() / "settings.json")
        self.store = DeviceStore(paths.config_dir() / "devices.json")
        adb_path = paths.tool_path("adb")
        self.adb = Adb(adb_path, make_env(game_mode)) if adb_path else None
        self.server = AdbServer(self.adb) if self.adb else None
        self._ready = False
        self._fatal = "" if adb_path else "The bundled adb tool is missing, so devices can't be connected."
        self._discovery_error = ""
        self._discovery_bound_to: str | None = None
        self.services: list[Service] = []
        self.net = netinfo.NetInfo()
        self.discovery = Discovery(on_change=self._servicesFromThread.emit,
                                   on_error=self._discoveryErrorFromThread.emit)
        self._servicesFromThread.connect(self._on_services)
        self._discoveryErrorFromThread.connect(self._on_discovery_error)
        self.runtime: dict[str, Runtime] = {}
        self._last_rows: list[dict] = []
        self._entries: list[DeviceEntry] = []
        self._poll_in_flight = False
        self._usb_poll = False
        self._usb_identity: dict[str, str] = {}     # USB adb serial -> ro.serialno, when they differ
        self._identifying: set[str] = set()
        self._usb_hint = ""                         # state of a plugged-in device that isn't saved
        self.wizard = Wizard(_WizardActions(self), companion_available=paths.companion_apk_path() is not None,
                             on_change=self.wizardChanged.emit)
        bundled = paths.companion_apk_path()
        self._companion_version = companion.apk_version(bundled) if bundled else None    # the bundled APK's
        self._companion_updates: set[str] = set()      # devices whose older companion this run tried to update
        self._wizard_active = False
        self._mirror_proc: QProcess | None = None
        self._vr_session: vrmirror.VrMirrorSession | None = None
        self._mirror_mode = ""                  # "window" | "vr" while mirroring
        self._screen: screencontrol.ScreenSession | None = None
        self._screen_on = True                  # phone display state while mirroring
        self._screen_busy = False
        self._panel_visible = True              # VR panel shown (it can be hidden while mirroring)
        self._panel_opening = False             # VR panel started but not in the headset yet
        self._panel_theater = False             # VR panel in theater mode (never carried over)
        self._audio: audiocast.AudioCast | None = None
        self._audio_casting = False             # wanted for this app run only: off at launch, never saved
        self._audio_supported = True            # the mirrored phone can cast its audio
        self._audio_serial = ""                 # mirrored phone, once its mirror is up
        self._mirror_device = ""
        self._mirror_serial = ""                # the connection a running mirror uses
        self._mirror_to_usb = ""                # device whose Wi-Fi mirror is restarting over the cable
        self._mirror_to_wifi: dict | None = None    # a USB mirror whose cable was pulled, waiting for Wi-Fi
        self._mirror_stopped = False            # the running mirror was stopped on purpose
        self._polls = 0                         # `adb devices` results so far
        self._mirror_last_device = ""
        self._mirror_log: deque[str] = deque(maxlen=400)
        self._mirror_message = ""
        self._mirror_retry_smaller = False
        self._max_size_override: int | None = None
        self.links: dict[str, CompanionLink] = {}
        self._wake_key = wake.load_or_create_key(paths.config_dir() / "wake-key")
        self._waker = wake.WakeSender(self._wake_key, self) if self._wake_key else None
        if self._waker is not None:
            self._waker.woke.connect(self._woke)
        self._woken_until: dict[str, float] = {}     # address -> monotonic time to wake it again
        self.center = NotificationCenter(self.settings, self)
        self.center.changed.connect(self.notificationsChanged)
        self.center.inAppToast.connect(self.toast)
        self.center.actionRequested.connect(self._notification_action)
        self.mirroringChanged.connect(self._push_vr_audio)
        self.updater = Updater(self.runner, game_mode, self.adb, self._companion_targets, self)
        self.updater.changed.connect(self.updateChanged)
        self._poll_timer = QTimer(self, interval=POLL_MS, timeout=self._poll)
        self._tick_timer = QTimer(self, interval=1000, timeout=self._tick)
        self._net_timer = QTimer(self, interval=NET_POLL_MS, timeout=self._refresh_net)
        self._wake_timer = QTimer(self, interval=WAKE_EVERY_MS, timeout=self._wake_devices)

    def start(self) -> None:
        self._refresh_net()
        self._net_timer.start()
        self._tick_timer.start()
        if self.server is None:
            self.statusChanged.emit()
            return
        self.runner.run(self.server.start, on_done=self._server_started,
                        on_error=lambda e: self._server_started(False))

    def _server_started(self, ok: bool) -> None:
        if not ok:
            self._fatal = "Couldn't start the adb server. See Settings › Logs for details."
        self._ready = ok
        self.statusChanged.emit()
        if ok:
            self._poll()
            self._poll_timer.start()
            self._wake_devices()
            self._wake_timer.start()

    @Slot()
    def shutdown(self) -> None:
        for timer in (self._poll_timer, self._tick_timer, self._net_timer, self._wake_timer):
            timer.stop()
        # Close the screen session here (restores the phone's display). Ending the mirror would
        # hand it to the worker pool, which is shutting down.
        screen, self._screen = self._screen, None
        if screen is not None:
            screen.close()
        audio, self._audio = self._audio, None
        if audio is not None:
            audio.stop(blocking=True)             # so the phone gets its sound back
        if self._mirror_proc is not None:
            self._mirror_proc.kill()
            self._mirror_proc.waitForFinished(2000)
        if self._vr_session is not None:
            self._vr_session.stop(blocking=True)   # let vr-mirror exit cleanly first
        # After everything else that needs adb: `bye` turns the device's Wireless debugging off.
        for link in self.links.values():
            link.stop(bye=True)
        if self._waker is not None:
            self._waker.close()
        self.updater.shutdown()
        self.center.shutdown()
        self.discovery.stop()
        self.runner.shutdown()
        if self.server is not None:
            self.server.stop()

    def _refresh_net(self) -> None:
        self.runner.run(netinfo.current, on_done=self._on_net)

    def _on_net(self, info: netinfo.NetInfo) -> None:
        if (info.ipv4, info.ssid) != (self.net.ipv4, self.net.ssid):
            for rt in self.runtime.values():
                rt.next_handoff = 0.0       # a new network: try giving plugged-in devices Wi-Fi again
        if info != self.net:
            self.net = info
            self.netChanged.emit()
        if info.online:
            self.updater.check()        # once, as soon as the Frame is online after startup
        # Rebind mDNS when the Wi-Fi address changes. No address binds all interfaces.
        if info.ipv4 != self._discovery_bound_to:
            self._discovery_bound_to = info.ipv4
            self._discovery_error = ""
            self.runner.run(self.discovery.start, info.ipv4 or None)

    def _on_services(self, services: list) -> None:
        self.services = services
        if self._wizard_active:
            self.wizard.services_changed(services)
        self._update_statuses()

    def _on_discovery_error(self, message: str) -> None:
        self._discovery_error = message
        self.statusChanged.emit()

    def _poll(self) -> None:
        if not self._ready or self._poll_in_flight:
            return
        self._poll_in_flight = True
        self.runner.run(self.adb.devices, on_done=self._on_entries,
                        on_error=lambda e: self._on_entries(None))

    def _on_entries(self, entries: list[DeviceEntry] | None) -> None:
        self._poll_in_flight = False
        if entries is None:
            return
        self._entries = entries
        self._polls += 1
        if self._wizard_active and self._usb_poll:
            self.wizard.usb_devices(entries)
        self._update_statuses()

    def set_usb_poll(self, enabled: bool) -> None:
        self._usb_poll = enabled
        self._poll_timer.setInterval(USB_POLL_MS if enabled else POLL_MS)
        if enabled:
            self._poll()

    def _connect_service_for(self, device: SavedDevice) -> Service | None:
        for service in self.services:
            if service.kind == "connect" and device.matches_guid(service.name):
                return service
        return None

    def _is_usb_of(self, device: SavedDevice, serial: str) -> bool:
        if serial == device.usb_serial:
            return True
        return bool(device.serialno) and device.serialno in (serial, self._usb_identity.get(serial))

    def _update_statuses(self) -> None:
        connected = {e.serial for e in self._entries if e.state == "device"}
        usb = [e for e in self._entries if not e.is_tcp]
        now = time.monotonic()
        matched: set[str] = set()
        for device in self.store.all():
            rt = self.runtime.setdefault(device.id, Runtime())
            entry = next((e for e in usb if self._is_usb_of(device, e.serial)), None)
            if entry is not None:
                matched.add(entry.serial)
            self._update_usb(rt, entry, now)
            self._update_wifi(device, rt, connected, now)
            self._maybe_check_wifi(device, rt, now)
            self._settle_status(device, rt)
            if not rt.via:
                self._stop_link(device.id)
            elif rt.serial and rt.handoff != "tcpip":
                # While adbd restarts, a forward would die with it: the link waits for a live connection.
                self._ensure_link(device, rt.serial)
            self._maybe_start_handoff(device, rt, now)
            self._maybe_move_mirror_to_usb(device, rt)
            self._maybe_resume_over_wifi(device, rt, now)
        self._check_unknown_usb([e for e in usb if e.serial not in matched])
        self._emit_devices_if_changed()

    def _update_usb(self, rt: Runtime, entry: DeviceEntry | None, now: float) -> None:
        restarting = rt.handoff == "tcpip" or now < rt.usb_grace_until
        if entry is not None and not (entry.state == "offline" and restarting):
            if entry.state == "device" and rt.usb_state != "device":
                rt.auth_failed = False          # plugged in: worth trying Wi-Fi again
            rt.usb_serial, rt.usb_state = entry.serial, entry.state
        elif (entry is not None or rt.usb_serial) and restarting:
            rt.usb_serial = rt.usb_serial or entry.serial
            rt.usb_state = "returning"      # `adb tcpip` restarts adbd, which drops USB for a moment
        elif rt.usb_serial or rt.usb_state:
            rt.usb_serial, rt.usb_state = "", ""
            # Unplugged: drop any hand-off still running, and try again on the next plug-in.
            rt.handoff, rt.next_handoff, rt.handoff_gen = "", 0.0, rt.handoff_gen + 1

    def _update_wifi(self, device: SavedDevice, rt: Runtime, connected: set[str], now: float) -> None:
        if rt.wifi_serial and rt.wifi_serial in connected:
            return
        if rt.wifi_serial:                                  # the Wi-Fi connection just dropped
            stale, rt.wifi_serial = rt.wifi_serial, ""
            self.runner.run(self.adb.disconnect, stale)
        if rt.connecting or not self._ready:
            return
        on_usb = rt.via == "usb"
        if device.method == WIRELESS:
            service = self._connect_service_for(device)
            if service is None:
                rt.status = "off"
                # mDNS can go quiet (phone asleep, client isolation) but the port only changes
                # when Wireless debugging is toggled, so retry the last address.
                if device.last_host and device.last_port and now >= rt.next_quiet_attempt and not on_usb:
                    self._quiet_connect(device)
            elif rt.auth_failed:
                rt.status = "needs_pair"
            else:
                rt.status = "ready"
                if now >= rt.next_attempt:
                    self._auto_connect(device, service.host, service.port)
        elif device.method == TCPIP:
            if now >= rt.next_attempt and device.last_host and not on_usb:
                self._auto_connect(device, device.last_host, device.last_port or TCPIP_PORT)
            elif rt.failures:
                rt.status = "unreachable"

    def _maybe_check_wifi(self, device: SavedDevice, rt: Runtime, now: float) -> None:
        """`adb devices` keeps a Wi-Fi connection whose device left the network (Wi-Fi turned off, out of
        range) as "device" for minutes, so ask the device itself now and then."""
        if not rt.wifi_serial or rt.wifi_checking or now < rt.next_wifi_check:
            return
        rt.wifi_checking, rt.next_wifi_check = True, now + WIFI_CHECK_S
        serial = rt.wifi_serial
        # A check that fails to run says nothing about the device: keep the connection.
        self.runner.run(self._wifi_answers, serial,
                        on_done=lambda ok: self._wifi_checked(device.id, serial, now, ok),
                        on_error=lambda e: self._wifi_checked(device.id, serial, now, True))

    # Runs on a worker thread.
    def _wifi_answers(self, serial: str) -> bool:
        for _ in range(WIFI_CHECK_TRIES):
            if self.adb.responds(serial, WIFI_CHECK_TIMEOUT_S):
                return True
        # Here, so it's gone before anything reconnects to the same address.
        self.adb.disconnect(serial)
        return False

    def _wifi_checked(self, device_id: str, serial: str, started: float, ok: bool) -> None:
        rt = self.runtime.get(device_id)
        if rt is None:
            return
        rt.wifi_checking = False
        if rt.wifi_serial != serial:
            return
        if ok:
            rt.wifi_answered_at = started
            waiting = self._mirror_to_wifi
            if waiting is not None and waiting["device"] == device_id:
                self._update_statuses()     # the unplugged mirror waits for this to carry on over Wi-Fi
            return
        log.info("%s doesn't answer over Wi-Fi (%s): dropping that connection", device_id, serial)
        rt.wifi_serial = ""
        self._update_statuses()

    @staticmethod
    def _settle_status(device: SavedDevice, rt: Runtime) -> None:
        """The card's status from both connections."""
        if rt.via:
            rt.status, rt.detail = "connected", ""
            return
        if rt.status == "connected":
            rt.status = "connecting" if rt.connecting else "off"
        if rt.status == "connecting":
            return
        if rt.usb_state in USB_HINT_STATES and (device.method == NONE or device.setup == SETUP_WIRED):
            rt.status, rt.detail = "usb_waiting", ""     # plugged in; the card says what it waits for
        elif device.method == NONE or (device.setup == SETUP_WIRED and rt.status in ("off", "needs_pair")):
            # Set up over the cable: plugging in is how to reconnect, never Wireless debugging help.
            brand = brand_for_manufacturer(device.manufacturer)
            rt.status, rt.detail = "unplugged", USB_CONTROL_HINT + guides.text("usbControl", brand)
        elif device.method == TCPIP and rt.status == "unreachable":
            rt.detail = TCPIP_UNREACHABLE

    # The Wi-Fi hand-off: a plugged-in device that isn't on Wi-Fi gets Wireless debugging turned on, or
    # failing that `adb tcpip`, so it keeps working once unplugged. Each step runs on the task runner;
    # its result is dropped if the device was unplugged or forgotten meanwhile.

    def _maybe_start_handoff(self, device: SavedDevice, rt: Runtime, now: float) -> None:
        if (rt.usb_state != "device" or rt.wifi_serial or rt.connecting or rt.handoff or now < rt.next_handoff
                or not self.net.online or not self._ready):
            return
        if self._wizard_active and self.wizard.step not in (TEST, DONE):
            return
        rt.next_handoff, rt.handoff = math.inf, "checking"
        gen, usb_serial, net = rt.handoff_gen, rt.usb_serial, self.net
        self.runner.run(handoff.device_address, self.adb, usb_serial, net,
                        on_done=lambda host: self._handoff_address(device.id, gen, usb_serial, net, host),
                        on_error=lambda e: self._handoff_address(device.id, gen, usb_serial, net, ""))

    def _handoff_current(self, device_id: str, gen: int, usb_serial: str):
        device, rt = self.store.get(device_id), self.runtime.get(device_id)
        if device is None or rt is None or rt.handoff_gen != gen or rt.usb_serial != usb_serial:
            return None
        return device, rt

    def _handoff_address(self, device_id: str, gen: int, usb_serial: str, net: netinfo.NetInfo, host: str) -> None:
        current = self._handoff_current(device_id, gen, usb_serial)
        if current is None:
            return
        device, rt = current
        if not host:                        # not on the Frame's network (yet)
            rt.handoff, rt.next_handoff = "", time.monotonic() + HANDOFF_RETRY_S
            return
        key = handoff.network_key(net)
        if device.sdk >= companion.WIRELESS_DEBUGGING_SDK and key not in device.wd_asked_networks:
            rt.handoff = "wireless"
            self.devicesChanged.emit()
            # Its port is announced over mDNS; the browser's own queries are minutes apart.
            for delay in HANDOFF_QUERY_MS:
                QTimer.singleShot(delay, lambda: self.discovery.query_now(host))
            attempt = functools.partial(handoff.try_wireless_debugging,
                                        find_port=lambda: self._mdns_port(device.serialno, host))
            self.runner.run(attempt, self.adb, usb_serial, host,
                            on_done=lambda r: self._handoff_wireless(device_id, gen, usb_serial, key, host, r),
                            on_error=lambda e: self._handoff_wireless(device_id, gen, usb_serial, key, host,
                                                                      handoff.HandoffResult(False)))
        else:
            self._handoff_tcpip(device, rt, gen, usb_serial, host)

    def _handoff_wireless(self, device_id: str, gen: int, usb_serial: str, key: str, host: str,
                          result: handoff.HandoffResult) -> None:
        current = self._handoff_current(device_id, gen, usb_serial)
        if current is None:
            return
        device, rt = current
        if result.ok:
            self.store.upsert(replace(
                device, method=WIRELESS, last_host=result.host, last_port=result.port, wifi_ok=True,
                app_enabled_wireless_debugging=device.app_enabled_wireless_debugging or result.enabled_wd))
            rt.wifi_serial, rt.handoff = f"{result.host}:{result.port}", ""
            self._note_connected(rt.wifi_serial)
            self.devicesChanged.emit()
            return
        if not result.locked and key not in device.wd_asked_networks:  # declined or unanswered: not again here
            device = self.store.upsert(replace(device, wd_asked_networks=device.wd_asked_networks + [key]))
        self._handoff_tcpip(device, rt, gen, usb_serial, host)

    def _handoff_tcpip(self, device: SavedDevice, rt: Runtime, gen: int, usb_serial: str, host: str) -> None:
        # `adb tcpip` restarts adbd on the device, cutting off anything else talking to it.
        if self._wizard_active:
            # The wizard's test mirror may start any moment: wait for it to close (set_wizard_active).
            rt.handoff, rt.next_handoff = "", math.inf
            self.devicesChanged.emit()
            return
        if self._mirror_device == device.id or rt.ops or rt.connecting:
            rt.handoff, rt.next_handoff = "", time.monotonic() + HANDOFF_RETRY_S
            self.devicesChanged.emit()
            return
        rt.handoff = "tcpip"
        self._stop_link(device.id)          # its forward dies with adbd; it's made afresh after
        self.devicesChanged.emit()
        self.runner.run(handoff.try_tcpip, self.adb, usb_serial, host,
                        on_done=lambda r: self._handoff_tcpip_done(device.id, gen, r),
                        on_error=lambda e: self._handoff_tcpip_done(device.id, gen, handoff.HandoffResult(False)))

    def _handoff_tcpip_done(self, device_id: str, gen: int, result: handoff.HandoffResult) -> None:
        device, rt = self.store.get(device_id), self.runtime.get(device_id)
        if device is None or rt is None or rt.handoff_gen != gen:
            return
        rt.usb_grace_until, rt.handoff = time.monotonic() + HANDOFF_USB_GRACE_S, ""
        if result.ok:
            rt.wifi_serial = f"{result.host}:{result.port}"
            self._note_connected(rt.wifi_serial)
            method = TCPIP if device.method == NONE else device.method
            where = {} if method == WIRELESS else {"last_host": result.host, "last_port": result.port}
            self.store.upsert(replace(device, method=method, wifi_ok=True, **where))
        self.devicesChanged.emit()
        self._poll()

    def _mdns_port(self, serialno: str, host: str) -> int:
        """The device's Wireless debugging port as announced over mDNS, or 0. Called from a worker."""
        for service in list(self.services):
            if service.kind == "connect" and service.host == host and (
                    not serialno or serial_from_guid(service.name) in (serialno, None)):
                return service.port
        return 0

    def _switching(self, device_id: str) -> bool:
        """Refuses adb work while `adb tcpip` restarts adbd on the device, with a banner saying why."""
        rt = self.runtime.get(device_id)
        if rt is not None and (rt.handoff == "tcpip" or rt.usb_state == "returning"):
            self.banner.emit(SWITCHING, "info")
            return True
        return False

    def _run_op(self, device_id: str, fn, *args, on_done=None, on_error=None) -> None:
        """Runs adb work on the device, counted so the hand-off's `adb tcpip` waits for it."""
        rt = self.runtime.setdefault(device_id, Runtime())
        rt.ops += 1

        def finished(result):
            rt.ops -= 1
            if on_done:
                on_done(result)

        def failed(exc):
            rt.ops -= 1
            if on_error:
                on_error(exc)
            else:
                log.warning("adb work on %s failed: %s", device_id, exc)

        self.runner.run(fn, *args, on_done=finished, on_error=failed)

    def _maybe_move_mirror_to_usb(self, device: SavedDevice, rt: Runtime) -> None:
        """The cable wins, even over a running mirror: plugged in during a Wi-Fi mirror, it restarts over USB."""
        if (device.id != self._mirror_device or rt.usb_state != "device" or not is_tcp_serial(self._mirror_serial)
                or self._mirror_to_usb == device.id):
            return
        log.info("%s is plugged in: restarting its mirror over USB", device.id)
        self._mirror_to_usb = device.id
        self._announce_switch("Plugged in: switching to USB.")
        self.stopMirror()

    def _announce_switch(self, banner: str) -> None:
        """The mirror is about to go away for a few seconds: say why."""
        self.banner.emit(f"{banner} {BACK_SOON}", "info")

    def _await_wifi(self, device_id: str, message: str, retry_smaller: bool) -> bool:
        """A USB mirror ended. If its cable was pulled and the device has Wi-Fi, carry on there; the
        failure message is kept back until that's known."""
        device, rt = self.store.get(device_id), self.runtime.get(device_id)
        if self._mirror_stopped or device is None or rt is None or device.method == NONE:
            return False
        now = time.monotonic()
        self._mirror_to_wifi = {"device": device_id, "message": message, "retry": retry_smaller,
                                "polls": self._polls, "since": now, "until": now + WIFI_RESUME_S}
        rt.next_wifi_check = 0.0
        self._poll()
        return True

    def _maybe_resume_over_wifi(self, device: SavedDevice, rt: Runtime, now: float) -> None:
        waiting = self._mirror_to_wifi
        if waiting is None or waiting["device"] != device.id or self._polls <= waiting["polls"]:
            return                          # decide on a fresh `adb devices`, not the one from before
        # The device must answer over Wi-Fi after the unplug: it may have left Wi-Fi while plugged in.
        if rt.via == "wifi" and rt.wifi_answered_at >= waiting["since"]:
            self._mirror_to_wifi = None
            log.info("%s was unplugged: continuing its mirror over Wi-Fi", device.id)
            self._announce_switch("Unplugged: switching to Wi-Fi.")
            self.mirror(device.id)
        elif rt.usb_state == "device" or now >= waiting["until"]:
            # Still plugged in (it dropped for another reason), or no Wi-Fi came: say what happened.
            self._mirror_to_wifi = None
            self._mirror_message, self._mirror_retry_smaller = waiting["message"], waiting["retry"]
            self.mirroringChanged.emit()

    def _resume_over_usb(self, device_id: str) -> bool:
        """After a mirror stopped by _maybe_move_mirror_to_usb ends: start it again on the cable."""
        if not device_id or self._mirror_to_usb != device_id:
            return False
        self._mirror_to_usb = ""
        self._mirror_message, self._mirror_retry_smaller = "", False
        self.mirror(device_id)
        return True

    def _check_unknown_usb(self, entries: list[DeviceEntry]) -> None:
        """Plugged-in devices that match no saved device: read their serial number in case the USB
        serial differs from it, and hint when one waits for Allow or permissions."""
        self._usb_hint = ""
        for entry in entries:
            if entry.state == "device":
                if entry.serial not in self._usb_identity and entry.serial not in self._identifying:
                    self._identifying.add(entry.serial)
                    self.runner.run(self.adb.device_info, entry.serial,
                                    on_done=lambda info, s=entry.serial: self._usb_identified(s, info),
                                    on_error=lambda e, s=entry.serial: self._identifying.discard(s))
            elif entry.state in USB_HINT_STATES:
                self._usb_hint = entry.state

    def _usb_identified(self, serial: str, info: DeviceInfo) -> None:
        self._identifying.discard(serial)
        self._usb_identity[serial] = info.serialno
        device = next((d for d in self.store.all() if info.serialno and d.serialno == info.serialno), None)
        if device is not None and device.usb_serial != serial:
            self.store.upsert(replace(device, usb_serial=serial))
            self._update_statuses()

    def _wake_devices(self) -> None:
        """Ask the companion of each device we can't reach to turn Wireless debugging on.

        It keeps it on while it hears from us (pings over the companion link, or these wakes) and
        turns it off once we've said `bye` or gone quiet. A broadcast reaches a device whose
        address changed, unless its Wi-Fi drops broadcasts while the screen is off.
        """
        if self._waker is None:
            return
        now = time.monotonic()
        targets, ports = [], set()
        for device in self.store.all():
            if device.method != WIRELESS or not device.companion or not device.wake_port:
                continue
            rt = self.runtime.get(device.id)
            if rt is not None and rt.via == "usb":
                continue                    # plugged in: no need for Wi-Fi
            link = self.links.get(device.id)
            if link is not None and link.state == "connected":
                continue                    # its pings keep Wireless debugging on
            if device.last_host and now < self._woken_until.get(device.last_host, 0.0):
                continue
            ports.add(device.wake_port)
            if device.last_host:
                targets.append((device.last_host, device.wake_port))
        broadcast = netinfo.broadcast_address(self.net.ipv4, self.net.prefix) if ports else ""
        if broadcast:
            targets += [(broadcast, port) for port in sorted(ports)]
        if targets:
            self._waker.wake(targets)

    def _woke(self, host: str) -> None:
        if time.monotonic() < self._woken_until.get(host, 0.0):
            return
        self._woken_until[host] = time.monotonic() + WAKE_HOLD_S
        # Its adb port is new each time Wireless debugging goes on, so don't count on catching
        # the announcement.
        for delay in WOKEN_QUERY_MS:
            QTimer.singleShot(delay, lambda: self.discovery.query_now(host))

    def _emit_devices_if_changed(self) -> None:
        # Rebuilding the QML list steals keyboard/D-pad focus, so only emit on change.
        snapshot = self._device_rows()
        if snapshot != self._last_rows:
            self._last_rows = snapshot
            self.devicesChanged.emit()

    def _auto_connect(self, device: SavedDevice, host: str, port: int) -> None:
        rt = self.runtime[device.id]
        rt.connecting, rt.status = True, "connecting"
        self.runner.run(self.connect_blocking, host, port,
                        on_done=lambda r: self._auto_connected(device.id, r),
                        on_error=lambda e: self._auto_connected(device.id, ConnectOutcome(False, message=str(e))))

    def _quiet_connect(self, device: SavedDevice) -> None:
        rt = self.runtime[device.id]
        rt.connecting, rt.next_quiet_attempt = True, time.monotonic() + QUIET_RETRY_S

        def done(result: ConnectOutcome) -> None:
            if result.ok:
                self._auto_connected(device.id, result)
            else:
                rt.connecting = False   # stay "off" silently until mDNS or the next retry

        self.runner.run(self.connect_blocking, device.last_host, device.last_port, on_done=done,
                        on_error=lambda e: done(ConnectOutcome(False, message=str(e))))

    def _auto_connected(self, device_id: str, result: ConnectOutcome) -> None:
        rt = self.runtime.get(device_id)
        device = self.store.get(device_id)
        if rt is None or device is None:
            return
        rt.connecting = False
        if result.ok and result.info is not None:
            rt.wifi_serial, rt.detail, rt.failures = result.serial, "", 0
            self._note_connected(result.serial)
            host, _, port = result.serial.rpartition(":")
            info = result.info
            device = self.store.upsert(replace(
                device, last_host=host, last_port=int(port), last_connected_at=time.time(), wifi_ok=True,
                model=info.model or device.model, android_release=info.release or device.android_release,
                sdk=info.sdk or device.sdk))
            self._ensure_link(device, rt.serial)
        elif result.error == "auth":
            rt.status, rt.auth_failed = "needs_pair", True
            rt.detail = "Your device no longer trusts the Frame. Pair it again."
        else:
            rt.failures += 1
            rt.next_attempt = time.monotonic() + min(60, 5 * 2 ** min(rt.failures, 4))
            rt.status = "unreachable" if device.method == TCPIP else "ready"
            rt.detail = result.message
        self._settle_status(device, rt)
        self.devicesChanged.emit()

    # Runs on a worker thread.
    def connect_blocking(self, host: str, port: int) -> ConnectOutcome:
        result = self.adb.connect(host, port)
        if not result.ok:
            return ConnectOutcome(False, error=result.error, message=result.message)
        serial = f"{host}:{port}"
        if not self.adb.wait_for_device(serial, timeout=10):
            state = self.adb.get_state(serial)
            self.adb.disconnect(serial)
            return ConnectOutcome(False, error="auth" if "unauthorized" in state else "other",
                                  message=f"The device didn't finish connecting ({state or 'offline'}).")
        return ConnectOutcome(True, serial=serial, info=self.adb.device_info(serial))

    def _note_connected(self, serial: str) -> None:
        # Treat it as connected until the next `adb devices` poll, or a discovery update in between
        # would mark it disconnected.
        if not any(e.serial == serial and e.state == "device" for e in self._entries):
            self._entries = [e for e in self._entries if e.serial != serial] + [DeviceEntry(serial, "device")]

    def device_saved(self, device: SavedDevice) -> SavedDevice:
        """Stores a device the wizard just connected, and returns it as stored (maybe under an older id)."""
        stored = self.store.add(device)
        rt = self.runtime.setdefault(stored.id, Runtime())
        if device.usb_serial:
            rt.usb_serial, rt.usb_state = device.usb_serial, "device"
            self._note_connected(device.usb_serial)
        elif device.last_host:
            rt.wifi_serial = f"{device.last_host}:{device.last_port}"
            self._note_connected(rt.wifi_serial)
        rt.detail, rt.failures, rt.connecting = "", 0, False
        self._settle_status(stored, rt)
        self._ensure_link(stored, rt.serial)
        self.devicesChanged.emit()
        return stored

    def _ensure_link(self, device: SavedDevice, serial: str) -> None:
        if not serial:
            return                  # without -s, adb would pick whichever device it sees
        link = self.links.get(device.id)
        if not device.companion:
            if link:
                self._stop_link(device.id)
            return
        if link and link.serial == serial:
            return
        if link:
            link.stop()
        link = CompanionLink(device.id, serial, self.adb, self.runner, self._wake_key, self)
        link.hello.connect(self._companion_hello)
        link.listenerChecked.connect(self._companion_listener_checked)
        link.posted.connect(lambda dev, msg: self.center.posted(dev, msg, self._visible_mirror_device()))
        link.removed.connect(self.center.removed)
        link.stateChanged.connect(lambda *_: self.devicesChanged.emit())
        self.links[device.id] = link
        link.start()

    def _stop_link(self, device_id: str, bye: bool = False) -> None:
        link = self.links.pop(device_id, None)
        if link:
            link.stop(bye=bye)

    def _companion_targets(self) -> list[CompanionTarget]:
        """The connected devices with the companion, whose companion an app update brings up to date."""
        targets = []
        for device in self.store.all():
            rt = self.runtime.get(device.id)
            if device.companion and rt and rt.status == "connected" and rt.serial:
                targets.append(CompanionTarget(device.display_name, rt.serial, device.sdk))
        return targets

    def _companion_hello(self, device_id: str, message: dict) -> None:
        port = message.get("wakePort")
        device = self.store.get(device_id)
        # 0 on Android 10 and older, which has no Wireless debugging to turn on.
        if device and type(port) is int and 0 <= port < 65536 and port != device.wake_port:
            self.store.upsert(replace(device, wake_port=port))
        self._update_companion(device_id, message.get("versionCode"))

    def _update_companion(self, device_id: str, version) -> None:
        """Install the bundled companion over an older one, once per device per run. An app update
        does the same for the devices connected then (Updater)."""
        device, link, latest = self.store.get(device_id), self.links.get(device_id), self._companion_version
        if (device is None or link is None or latest is None or type(version) is not int or version >= latest
                or device_id in self._companion_updates):
            return
        self._companion_updates.add(device_id)
        name = device.display_name
        self.banner.emit(f"Updating the companion app on {name}…", "info")

        def done(report: companion.InstallReport | None) -> None:
            if report is None:          # adb found it no older after all
                return
            self.banner.emit(f"The companion app on {name} is updated." if report.ok else
                             companion.update_failure(name, report), "info" if report.ok else "error")

        self._run_op(device_id, companion.update, self.adb, link.serial, paths.companion_apk_path(), latest,
                     device.sdk, on_done=done, on_error=lambda e: done(
                            companion.InstallReport(error=f"Installing the companion failed: {e}")))

    def _companion_listener_checked(self, device_id: str, outcome: str) -> None:
        if outcome in ("restarted", "failed", "revoked", "missing"):
            self.runtime.setdefault(device_id, Runtime()).listener_check = outcome
            self.devicesChanged.emit()

    @staticmethod
    def _companion_note(device: SavedDevice, outcome: str, state: str) -> str:
        if outcome == "restarted":
            # Stays after it reconnects: the device will stop it again unless allowed to run.
            if brand_for_manufacturer(device.manufacturer) == "xiaomi":
                how = ("open Settings › Apps › Manage apps › scrcpy4Frame Companion, turn on Autostart and "
                       "set Battery saver to \"No restrictions\".")
            else:
                how = "let scrcpy4Frame Companion run in the background in its battery settings."
            return ("The device had stopped the companion app, so notifications stopped and Wireless debugging "
                    "couldn't turn on by itself. It's been restarted. To keep it running, on the device " + how)
        if state == "connected":
            return ""
        if outcome == "failed":
            return ("The device stopped the companion app and it couldn't be restarted. On the device, turn "
                    "notification access for scrcpy4Frame Companion off and on again.")
        if outcome == "revoked":
            return ("Notification access for scrcpy4Frame Companion is off on the device. Turn it on in the "
                    "device's settings to get its notifications.")
        if outcome == "missing":
            return "The companion app isn't on the device anymore. Remove it here, then install it again."
        return ""

    @Property(bool, constant=True)
    def gameMode(self) -> bool:
        return self.game_mode

    @Property(bool, notify=statusChanged)
    def ready(self) -> bool:
        return self._ready

    @Property(str, notify=statusChanged)
    def fatalError(self) -> str:
        return self._fatal

    @Property(str, notify=statusChanged)
    def discoveryError(self) -> str:
        return self._discovery_error

    @Property("QVariantMap", notify=netChanged)
    def network(self) -> dict:
        return {"ssid": self.net.ssid, "ip": self.net.ipv4, "online": self.net.online}

    @Property("QVariantList", notify=devicesChanged)
    def devices(self) -> list:
        return self._device_rows()

    def _device_rows(self) -> list[dict]:
        out = []
        for device in self.store.all():
            rt = self.runtime.get(device.id, Runtime())
            link = self.links.get(device.id)
            via = rt.via
            if device.id == self._mirror_device and self._mirror_serial:
                via = "wifi" if is_tcp_serial(self._mirror_serial) else "usb"
            out.append({
                "id": device.id, "name": device.display_name, "model": device.model,
                "manufacturer": device.manufacturer, "release": device.android_release,
                "sdk": device.sdk, "method": device.method, "status": rt.status, "detail": rt.detail,
                "setup": device.setup, "via": via,
                "usbHint": rt.usb_state if rt.usb_state in USB_HINT_STATES else "",
                "host": "" if via == "usb" else (rt.wifi_serial.rpartition(":")[0] or device.last_host),
                "companion": device.companion, "companionState": link.state if link else "",
                "companionNote": self._companion_note(device, rt.listener_check, link.state if link else ""),
                "qsTile": device.qs_tile, "mirroring": device.id == self._mirror_device,
            })
        return out

    @Property("QVariantList", notify=devicesChanged)
    def handoffAsking(self) -> list:
        """Devices whose phone may be asking "Allow wireless debugging on this network?"."""
        return [device_id for device_id, rt in self.runtime.items() if rt.handoff == "wireless"]

    @Property(str, notify=devicesChanged)
    def usbHint(self) -> str:
        """A plugged-in device that isn't saved waits for Allow, or for USB permissions on the Frame."""
        return "" if self._wizard_active else self._usb_hint

    @Property(bool, notify=wizardActiveChanged)
    def wizardActive(self) -> bool:
        return self._wizard_active

    @Property("QVariantMap", notify=wizardChanged)
    def wizardView(self) -> dict:
        view = self.wizard.view()
        view["guide"] = guides.guide(view["step"], view["brand"], self.net.ssid)
        view["companionAvailable"] = self.wizard.companion_available
        return view

    @Property(bool, notify=mirroringChanged)
    def mirroring(self) -> bool:
        return self._mirror_proc is not None or self._vr_session is not None

    @Property("QVariantMap", notify=mirroringChanged)
    def mirrorStatus(self) -> dict:
        audio = self._audio
        audio_live = audio is not None and audio.is_live          # still true while it stops
        audio_stopping = audio is not None and audio.is_stopping
        return {"deviceId": self._mirror_device, "lastDeviceId": self._mirror_last_device,
                "message": self._mirror_message, "retrySmaller": self._mirror_retry_smaller,
                "mode": self._mirror_mode,
                "screenOn": self._screen_on, "screenBusy": self._screen_busy,
                "panelVisible": self._panel_visible,
                "opening": self._panel_opening,
                "theater": self._panel_theater,
                "audioCasting": audio_live,
                "audioBusy": audio_stopping or (self._audio_casting and self._audio_supported
                                                and not audio_live),
                "audioSupported": self._audio_supported}

    @Property("QVariantList", notify=notificationsChanged)
    def notifications(self) -> list:
        return self.center.router.recent_list()

    @Property("QVariantMap", notify=settingsChanged)
    def settingsView(self) -> dict:
        return asdict(self.settings.value)

    @Property("QVariantList", constant=True)
    def brands(self) -> list:
        return guides.brands()

    @Property("QVariantMap", notify=statusChanged)
    def about(self) -> dict:
        return {
            "version": __version__, "flatpak": paths.in_flatpak(), "gameMode": self.game_mode,
            "adb": paths.tool_path("adb") or "missing", "scrcpy": paths.tool_path("scrcpy") or "missing",
            "vrToast": paths.tool_path("vr-toast") or "missing",
            "companionApk": str(paths.companion_apk_path() or "missing"),
            "dataDir": str(paths.data_dir()), "configDir": str(paths.config_dir()),
        }

    @Property("QVariantMap", notify=updateChanged)
    def appUpdate(self) -> dict:
        return self.updater.view()

    @Slot()
    def installUpdate(self) -> None:
        self.updater.install()

    @Slot()
    def closeUpdateBanner(self) -> None:
        self.updater.close_banner()

    @Slot()
    def restartForUpdate(self) -> None:
        self.updater.restart()

    def set_wizard_active(self, active: bool) -> None:
        if active != self._wizard_active:
            self._wizard_active = active
            if not active and self._usb_poll:
                self.set_usb_poll(False)
            if not active:
                for rt in self.runtime.values():    # a hand-off may have waited for the wizard
                    if rt.next_handoff == math.inf and not rt.handoff:
                        rt.next_handoff = 0.0
            self.wizardActiveChanged.emit()

    @Slot(str, str)
    def startWizard(self, mode: str, device_id: str = "") -> None:
        exclude = {rt.wifi_serial.rpartition(":")[0] for rt in self.runtime.values() if rt.wifi_serial}
        saved = {d.serialno for d in self.store.all()}
        known = ({d.usb_serial for d in self.store.all()} | saved
                 | {usb for usb, serialno in self._usb_identity.items() if serialno in saved}) - {""}
        self.wizard.start(mode, self.store.get(device_id) if device_id else None, self.services, exclude, known)
        self.set_wizard_active(True)

    @Slot()
    def cancelWizard(self) -> None:
        self.set_wizard_active(False)

    @Slot(str, "QVariantList")
    def wizardAction(self, name: str, args: list) -> None:
        if name not in WIZARD_COMMANDS:
            log.warning("unknown wizard action %s", name)
            return
        getattr(self.wizard, name)(*args)

    @Slot(str, result="QVariantMap")
    def guide(self, step: str) -> dict:
        return guides.guide(step, self.wizard.ctx.brand or "other", self.net.ssid)

    @Slot(str, str, result="QVariantMap")
    def guideFor(self, step: str, device_id: str) -> dict:
        from .wizard import brand_for_manufacturer
        device = self.store.get(device_id)
        brand = brand_for_manufacturer(device.manufacturer) if device else "other"
        return guides.guide(step, brand, self.net.ssid)

    def _tick(self) -> None:
        if self._wizard_active:
            self.wizard.tick()

    @Slot(str)
    def mirror(self, device_id: str) -> None:
        if self.mirroring or self._switching(device_id):
            return
        device = self.store.get(device_id)
        rt = self.runtime.get(device_id)
        if device is None or rt is None or rt.status != "connected":
            self.banner.emit("That device isn't ready yet.", "error")
            return
        if vrmirror.available():
            self._mirror_vr(device, rt.serial)
        else:
            self._mirror_window(device, rt.serial)

    def _open_screen_control(self, serial: str) -> None:
        self._close_screen_control()
        self._screen = screencontrol.ScreenSession(self.adb, serial)
        self._screen_on = not self.settings.value.turn_screen_off
        self._screen_busy = False

    def _close_screen_control(self) -> None:
        screen, self._screen = self._screen, None
        if screen is not None:
            self.runner.run(screen.close)   # the server restores the display when it exits

    @Slot(bool)
    def setPhoneScreen(self, on: bool) -> None:
        """Turn the phone's display on or off while mirroring. The phone keeps running."""
        screen = self._screen
        if screen is None or self._screen_busy:
            return
        self._screen_busy = True
        self.mirroringChanged.emit()

        def done(result) -> None:
            self._screen_busy = False
            if screen is self._screen:
                self._screen_on = bool(result) if isinstance(result, bool) else on
            self.mirroringChanged.emit()

        def failed(error: BaseException) -> None:
            self._screen_busy = False
            self.banner.emit(f"Couldn't change the device's screen: {error}", "error")
            self.mirroringChanged.emit()

        self.runner.run(screen.set_display_power, on, on_done=done, on_error=failed)

    @Slot(bool)
    def setAudioCasting(self, on: bool) -> None:
        """Play the phone's audio on the Frame, or back on the phone. Lasts until the app quits."""
        self._audio_casting = bool(on)
        self._sync_audio()
        self.mirroringChanged.emit()

    def _mirror_up(self, serial: str) -> None:
        """The mirror's server is running. Starting the audio client earlier could race its push."""
        if not self._audio_serial:
            self._audio_serial = serial
            self._sync_audio()

    def _close_audio(self) -> None:
        self._audio_serial = ""
        self._sync_audio()

    def _sync_audio(self) -> None:
        """Run the audio-only client exactly while casting is wanted and a capable phone is up,
        with Settings' audio buffer: a client on another buffer is stopped and started again.

        A client that is stopping stays in `_audio` until it has exited, so Home shows it as busy.
        """
        wanted = bool(self._audio_serial) and self._audio_casting and self._audio_supported
        buffer_ms = self.settings.value.audio_buffer_ms
        audio = self._audio
        if audio is None and wanted:
            audio = audiocast.AudioCast(self._audio_serial, buffer_ms, self)
            audio.started.connect(lambda: audio is self._audio and self.mirroringChanged.emit())
            audio.stopped.connect(lambda: self._audio_stopped(audio))
            audio.ended.connect(lambda code, reason: self._audio_ended(audio, code, reason))
            self._audio = audio
            audio.start()
        elif audio is not None and not audio.is_stopping and (not wanted or audio.buffer_ms != buffer_ms):
            audio.stop()            # once it has exited, _audio_stopped starts it again if wanted

    def _audio_stopped(self, audio: audiocast.AudioCast) -> None:
        if audio is not self._audio:
            return
        self._audio = None
        audio.deleteLater()
        self._sync_audio()          # a new session may want it again by now
        self.mirroringChanged.emit()

    def _audio_ended(self, audio: audiocast.AudioCast, code: int, reason: str) -> None:
        if audio is not self._audio:
            return
        self._audio = None
        audio.deleteLater()
        self._audio_casting = False          # so the button shows what's really happening
        if code != EXIT_DISCONNECTED:        # a dropped phone is reported by the mirror itself
            self.banner.emit("Audio casting stopped." + (f" {reason}" if reason else ""), "error")
        self.mirroringChanged.emit()

    def _mirror_vr(self, device: SavedDevice, serial: str) -> None:
        # Each session opens in front of the user at Settings' size: where and how big the last
        # one was left isn't kept.
        session = vrmirror.VrMirrorSession(self.adb, serial, device.display_name, self.settings.value,
                                           self.runner, self)
        session.ended.connect(lambda kind, message: self._vr_ended(session, device.id, kind, message))
        session.event.connect(self._vr_event)
        self._vr_session = session
        self._mirror_mode = "vr"
        self._panel_visible = True
        self._panel_opening = True
        self._panel_theater = False
        self._audio_supported = audiocast.supported(device.sdk)
        self._open_screen_control(serial)
        self._mirror_device = self._mirror_last_device = device.id
        self._mirror_serial, self._mirror_stopped = serial, False
        self._mirror_message, self._mirror_retry_smaller = "", False
        log.info("starting VR panel mirroring of %s (%s)", device.id, serial)
        session.start()
        self.mirroringChanged.emit()
        self.devicesChanged.emit()

    def _vr_event(self, name: str, payload: dict) -> None:
        if name == "visibility":
            self._panel_visible = bool(payload.get("visible", True))
            self.mirroringChanged.emit()
        elif name == "theater":                     # Home's button or the panel's own
            self._panel_theater = payload.get("on") is True
            self.mirroringChanged.emit()
        elif name == "audio_cast":                  # the panel's own audio button
            self.setAudioCasting(payload.get("on") is True)
        elif name == "first_frame" and self._vr_session is not None:
            self._panel_opening = False             # the panel shows with its first frame
            self._mirror_up(self._vr_session.serial)
            self.mirroringChanged.emit()

    def _push_vr_audio(self) -> None:
        """The panel's audio button shows what Home's does, and can't be pressed when Home's can't."""
        if self._vr_session is not None:
            status = self.mirrorStatus
            self._vr_session.set_audio_state(status["audioCasting"],
                                             status["audioSupported"] is not False and not status["audioBusy"])

    def _vr_ended(self, session, device_id: str, kind: str, message: str) -> None:
        if session is not self._vr_session:
            return
        self._vr_session = None
        self._panel_opening = False
        over_usb = not is_tcp_serial(self._mirror_serial)
        self._mirror_device = self._mirror_serial = ""
        self._mirror_mode = ""
        self._close_screen_control()
        self._close_audio()
        self.mirroringChanged.emit()
        self.devicesChanged.emit()
        if self._resume_over_usb(device_id):
            return
        if kind == "vr_unavailable":
            self.banner.emit("The overlay isn't available right now, so the device opens in a window.", "info")
            device, rt = self.store.get(device_id), self.runtime.get(device_id)
            if device and rt and rt.status == "connected":
                self._mirror_window(device, rt.serial)
            return
        if kind in ("failed", "disconnected"):
            text = {
                "disconnected": "The connection to your device dropped. It may have locked or "
                                + ("been unplugged." if over_usb else "left the Wi-Fi."),
            }.get(kind, "The overlay couldn't start.") + (f"\n{message}" if message and kind == "failed" else "")
            if over_usb and self._await_wifi(device_id, text, False):
                return
            self._mirror_message = text
            self.mirroringChanged.emit()
        self._poll()

    def _mirror_window(self, device: SavedDevice, serial: str) -> None:
        device_id = device.id
        rt_serial = serial
        scrcpy = paths.tool_path("scrcpy")
        if scrcpy is None:
            self.banner.emit("The bundled scrcpy tool is missing.", "error")
            return
        args = build_args(scrcpy, rt_serial, f"{device.display_name} · scrcpy4Frame", self.settings.value,
                          max_size_override=self._max_size_override)
        proc = QProcess(self)
        env = QProcessEnvironment()
        for key, value in make_env(self.game_mode).items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._mirror_output(proc, rt_serial))
        proc.finished.connect(lambda code, status: self._mirror_finished(proc, code, status))
        proc.errorOccurred.connect(lambda err: self._mirror_error(proc, err))
        self._mirror_log.clear()
        self._mirror_proc = proc
        self._mirror_mode = "window"
        self._audio_supported = audiocast.supported(device.sdk)
        self._open_screen_control(rt_serial)
        self._mirror_device = self._mirror_last_device = device_id
        self._mirror_serial, self._mirror_stopped = rt_serial, False
        self._mirror_message, self._mirror_retry_smaller = "", False
        log.info("starting scrcpy: %s", " ".join(args))
        proc.start()
        # Audio joins once scrcpy's server logs, or after a while if that line never shows.
        QTimer.singleShot(AUDIO_JOIN_FALLBACK_MS,
                          lambda: proc is self._mirror_proc and self._mirror_up(rt_serial))
        self.mirroringChanged.emit()
        self.devicesChanged.emit()

    def _mirror_output(self, proc: QProcess, serial: str) -> None:
        text = bytes(proc.readAllStandardOutput().data()).decode(errors="replace")
        for line in text.splitlines():
            self._mirror_log.append(line)
            log.info("scrcpy: %s", line)
            if line.startswith("[server]") and proc is self._mirror_proc:
                self._mirror_up(serial)

    def _mirror_error(self, proc: QProcess, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._mirror_log.append(f"failed to start scrcpy: {proc.errorString()}")
            self._mirror_finished(proc, 1, QProcess.ExitStatus.CrashExit)

    def _mirror_finished(self, proc: QProcess, code: int, status) -> None:
        if proc is not self._mirror_proc:
            return
        if status != QProcess.ExitStatus.NormalExit and code == 0:
            code = 1
        over_usb = not is_tcp_serial(self._mirror_serial)
        outcome = classify(code, "\n".join(self._mirror_log), usb=over_usb)
        device_id = self._mirror_device
        self._mirror_proc = None
        self._mirror_device = self._mirror_serial = ""
        self._mirror_mode = ""
        self._close_screen_control()
        self._close_audio()
        self._max_size_override = None
        if self._resume_over_usb(device_id):
            return
        if outcome.kind != "ok":
            log.warning("scrcpy ended: %s (exit %s)", outcome.kind, code)
            if over_usb and self._await_wifi(device_id, outcome.message, outcome.retry_smaller):
                self.mirroringChanged.emit()
                self.devicesChanged.emit()
                return
            self._mirror_message, self._mirror_retry_smaller = outcome.message, outcome.retry_smaller
        self.mirroringChanged.emit()
        self.devicesChanged.emit()
        self._poll()

    @Slot()
    def stopMirror(self) -> None:
        self._mirror_stopped = True
        if self._mirror_proc is not None:
            self._mirror_proc.terminate()
        if self._vr_session is not None:
            self._vr_session.stop()

    @Slot(str)
    def mirrorKey(self, key: str) -> None:
        """Back / Home / Recents / Power for the phone shown in the VR panel."""
        if self._vr_session is not None:
            self._vr_session.send_key(key)

    @Slot()
    def recenterPanel(self) -> None:
        if self._vr_session is not None:
            self._vr_session.recenter()

    @Slot(bool)
    def setPanelVisible(self, visible: bool) -> None:
        """Hide the VR panel while mirroring continues, or bring it back in front of the user at
        once (as the dashboard button does; only Recenter counts down). In theater it comes back
        where it was instead."""
        if self._vr_session is None:
            return
        if visible:
            self._vr_session.recenter(now=True)
        else:
            self._vr_session.set_panel_visible(False)

    @Slot(bool)
    def setTheater(self, on: bool) -> None:
        """Theater on or off; Home's button shows it once the panel reports it."""
        if self._vr_session is not None:
            self._vr_session.set_theater(on)

    @Slot()
    def retryMirrorSmaller(self) -> None:
        # Offered after an encoder failure in window mode.
        device, rt = self.store.get(self._mirror_last_device), self.runtime.get(self._mirror_last_device)
        if self.mirroring or device is None or rt is None or rt.status != "connected":
            return
        self._max_size_override = 1280
        self._mirror_window(device, rt.serial)

    @Slot()
    def clearMirrorMessage(self) -> None:
        self._mirror_message = ""
        self.mirroringChanged.emit()

    @Slot(str)
    def reconnectDevice(self, device_id: str) -> None:
        rt = self.runtime.setdefault(device_id, Runtime())
        rt.next_attempt, rt.failures, rt.auth_failed = 0.0, 0, False
        if rt.status == "needs_pair":
            rt.status = "off"
        self._update_statuses()

    @Slot(str)
    def forgetDevice(self, device_id: str) -> None:
        """Drop everything kept for the device, and uninstall its companion while adb still reaches it."""
        if self._switching(device_id):
            return
        device, rt = self.store.get(device_id), self.runtime.pop(device_id, None)
        serial = rt.serial if rt else ""
        uninstall = bool(device and device.companion and rt and rt.status == "connected")
        # No bye: it turns Wireless debugging off, and the uninstall goes over adb. We turn it off
        # ourselves afterwards. A companion we can't reach isn't linked anyway.
        self._stop_link(device_id)
        self.store.remove(device_id)
        self.center.forget_device(device_id)
        if self._mirror_last_device == device_id:
            self._mirror_last_device = ""
        self.devicesChanged.emit()
        if not uninstall:
            if device and device.companion:
                self.banner.emit(f"{device.display_name} is forgotten, but its companion app is still on it. "
                                 "Uninstall it on the device.", "error")
            if serial:
                self.runner.run(self.adb.disconnect, serial)
            return

        # Runs on a worker thread.
        def uninstall_and_turn_off() -> bool:
            ok = companion.uninstall(self.adb, serial)
            # Even if the uninstall failed: we won't wake it again.
            if (device.setup == SETUP_WIRELESS and device.method == WIRELESS) or device.app_enabled_wireless_debugging:
                companion.turn_off_wireless_debugging(self.adb, serial)
            return ok

        def done(ok: bool) -> None:
            self.runner.run(self.adb.disconnect, serial)
            self.banner.emit("Companion removed." if ok else
                             f"Couldn't uninstall the companion from {device.display_name}. "
                             "Uninstall it on the device.", "info" if ok else "error")

        # Not counted in ops: the device is gone from the list, so no hand-off can start on it.
        self.runner.run(uninstall_and_turn_off, on_done=done, on_error=lambda e: done(False))

    @Slot(str, str)
    def renameDevice(self, device_id: str, name: str) -> None:
        device = self.store.get(device_id)
        if device:
            self.store.upsert(replace(device, nickname=name.strip()))
            self.devicesChanged.emit()

    @Slot(str)
    def installCompanion(self, device_id: str) -> None:
        if self._switching(device_id):
            return
        device, rt = self.store.get(device_id), self.runtime.get(device_id)
        apk = paths.companion_apk_path()
        if not device or not rt or rt.status != "connected" or apk is None:
            self.banner.emit("That device isn't ready yet.", "error")
            return
        self.banner.emit("Installing the companion…", "info")

        def done(report: companion.InstallReport) -> None:
            current = self.store.get(device_id)
            if current:
                self.store.upsert(replace(current, companion=report.ok))
                self._ensure_link(self.store.get(device_id), rt.serial)
            self.banner.emit("Companion installed." if report.ok else report.error,
                             "info" if report.ok else "error")
            self.devicesChanged.emit()

        self._run_op(device_id, companion.install_and_grant, self.adb, rt.serial, apk, device.sdk, on_done=done)

    @Slot(str)
    def removeCompanion(self, device_id: str) -> None:
        device, rt = self.store.get(device_id), self.runtime.get(device_id)
        if not device or self._switching(device_id):
            return
        self._stop_link(device_id)
        self.store.upsert(replace(device, companion=False))
        if rt:
            rt.listener_check = ""
        if rt and rt.status == "connected":
            self._run_op(device_id, companion.uninstall, self.adb, rt.serial,
                         on_done=lambda ok: self.banner.emit(
                             "Companion removed." if ok else "Couldn't uninstall the companion.",
                             "info" if ok else "error"))
        self.devicesChanged.emit()

    @Slot(str)
    def addTile(self, device_id: str) -> None:
        if self._switching(device_id):
            return
        rt = self.runtime.get(device_id)
        if not rt or rt.status != "connected":
            self.banner.emit("That device isn't ready yet.", "error")
            return

        def done(ok: bool) -> None:
            device = self.store.get(device_id)
            if ok and device:
                self.store.upsert(replace(device, qs_tile=True))
            self.banner.emit("Shortcut added to Quick Settings." if ok else
                             "Your device didn't accept the shortcut.", "info" if ok else "error")
            self.devicesChanged.emit()

        self._run_op(device_id, companion.add_tile, self.adb, rt.serial, on_done=done)

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value) -> None:
        changed = self.settings.update(key, value)
        if key == "dashboard_button" and self._vr_session is not None:
            self._vr_session.set_dashboard_button(self.settings.value.dashboard_button)
        if key == "audio_buffer_ms" and self._audio is not None:
            self._sync_audio()
            self.mirroringChanged.emit()
        if changed:
            self.settingsChanged.emit()

    @Slot(str)
    def muteApp(self, package: str) -> None:
        muted = list(self.settings.value.muted_packages)
        if package and package not in muted:
            self.setSetting("muted_packages", muted + [package])

    @Slot(str)
    def unmuteApp(self, package: str) -> None:
        self.setSetting("muted_packages", [p for p in self.settings.value.muted_packages if p != package])

    @Slot(str)
    def dismissNotification(self, uid: str) -> None:
        note = self.center.router.get(uid)
        if note is None:
            return
        link = self.links.get(note.device_id)
        if link:
            link.dismiss(note.key)
        self.center.forget(uid)

    def _visible_mirror_device(self) -> str:
        """Device whose mirror is on screen. A hidden VR panel counts as not visible."""
        if self._vr_session is not None and not self._panel_visible:
            return ""
        return self._mirror_device

    @Slot(str)
    def openNotification(self, uid: str) -> None:
        note = self.center.router.get(uid)
        rt = self.runtime.get(note.device_id) if note else None
        if note is None or rt is None or rt.status != "connected":
            return
        command = f"monkey -p {note.pkg} -c android.intent.category.LAUNCHER 1"
        self.runner.run(self.adb.shell, rt.serial, command)
        if not self.mirroring:
            self.mirror(note.device_id)
        elif (self._vr_session is not None and not self._panel_visible
              and self._mirror_device == note.device_id):
            self._vr_session.set_panel_visible(True)   # show the app it just opened

    @Slot()
    def clearNotifications(self) -> None:
        self.center.clear()

    def _notification_action(self, uid: str, action: str) -> None:
        if action == "open":
            self.openNotification(uid)
        elif action == "dismiss":
            self.dismissNotification(uid)

    @Slot(result=str)
    def logText(self) -> str:
        path = paths.cache_dir() / "scrcpy4frame.log"
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-400:])

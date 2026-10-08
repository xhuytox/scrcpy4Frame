"""The Qt-facing application controller, exposed to QML as `app`."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, replace

from PySide6.QtCore import (Property, QObject, QProcess, QProcessEnvironment, QTimer, Signal,
                            Slot)

from . import (__version__, audiocast, companion, guides, netinfo, paths, phonechecks, screencontrol,
               vrmirror, wake)
from .adb import TCPIP_PORT, Adb, AdbServer, DeviceEntry, DeviceInfo, PairResult, make_env
from .companion_link import CompanionLink
from .devices import TCPIP, WIRELESS, DeviceStore, SavedDevice
from .discovery import Discovery, Service, same_phone
from .mirror import EXIT_DISCONNECTED, build_args, classify
from .notify.center import NotificationCenter
from .settings import SettingsStore
from .tasks import TaskRunner
from .updater import Updater
from .wizard import Wizard

log = logging.getLogger(__name__)

POLL_MS = 3000
USB_POLL_MS = 1000
NET_POLL_MS = 10_000
QUIET_RETRY_S = 60
AUDIO_JOIN_FALLBACK_MS = 8000
WAKE_EVERY_MS = 5000
WAKE_HOLD_S = 30            # a woken companion keeps Wireless debugging on for 60 s without contact
WOKEN_QUERY_MS = (1000, 3000)   # when to look for a woken device's new adb port over mDNS

WIZARD_COMMANDS = {
    "choose_brand", "next", "back", "show_help", "pick", "not_mine", "use_manual", "use_usb",
    "submit_code", "manual_submit", "retry", "skip", "accept_companion", "decline_companion",
    "add_tile", "check_again", "start_mirror", "finish",
}


@dataclass
class Runtime:
    serial: str = ""            # adb serial ("ip:port") while connected
    status: str = "off"         # connected | connecting | ready | off | unreachable | needs_pair
    detail: str = ""
    connecting: bool = False
    next_attempt: float = 0.0
    failures: int = 0
    next_quiet_attempt: float = 0.0     # retry of the last known address while mDNS can't see it


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

    def usb_tcpip(self, usb_serial):
        self.app.runner.run(self.app.tcpip_blocking, usb_serial,
                            on_done=lambda r: self.wizard.tcpip_done(r.ok, r.serial, r.info, r.error, r.message),
                            on_error=lambda e: self.wizard.tcpip_done(False, message=str(e)))

    def save_device(self, device):
        self.app.device_saved(device)

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
        self.wizard = Wizard(_WizardActions(self), companion_available=paths.companion_apk_path() is not None,
                             on_change=self.wizardChanged.emit)
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
        self.updater = Updater(self.runner, game_mode, self)
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
            if service.kind == "connect" and same_phone(service.name, device.id):
                return service
        return None

    def _update_statuses(self) -> None:
        connected = {e.serial for e in self._entries if e.state == "device"}
        now = time.monotonic()
        for device in self.store.all():
            rt = self.runtime.setdefault(device.id, Runtime())
            if rt.serial and rt.serial in connected:
                rt.status, rt.detail = "connected", ""
                self._ensure_link(device, rt.serial)
                continue
            if rt.status == "connected":                       # the connection just dropped
                self._stop_link(device.id)
                stale, rt.serial = rt.serial, ""
                if stale:
                    self.runner.run(self.adb.disconnect, stale)
            if rt.connecting or not self._ready:
                continue
            if device.method == WIRELESS:
                service = self._connect_service_for(device)
                if service is None:
                    rt.status = "off"
                    # mDNS can go quiet (phone asleep, client isolation) but the port only changes
                    # when Wireless debugging is toggled, so retry the last address.
                    if device.last_host and device.last_port and now >= rt.next_quiet_attempt:
                        self._quiet_connect(device)
                elif rt.status != "needs_pair":
                    rt.status = "ready"
                    if now >= rt.next_attempt:
                        self._auto_connect(device, service.host, service.port)
            elif now >= rt.next_attempt and device.last_host:
                self._auto_connect(device, device.last_host, device.last_port or TCPIP_PORT)
            elif rt.failures:
                rt.status = "unreachable"
        self._emit_devices_if_changed()

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
            rt.serial, rt.status, rt.detail, rt.failures = result.serial, "connected", "", 0
            self._note_connected(result.serial)
            host, _, port = result.serial.rpartition(":")
            info = result.info
            self.store.upsert(replace(device, last_host=host, last_port=int(port), last_connected_at=time.time(),
                                      model=info.model or device.model, android_release=info.release or device.android_release,
                                      sdk=info.sdk or device.sdk))
            self._ensure_link(self.store.get(device_id) or device, rt.serial)
        elif result.error == "auth":
            rt.status = "needs_pair"
            rt.detail = "Your device no longer trusts the Frame. Pair it again."
        else:
            rt.failures += 1
            rt.next_attempt = time.monotonic() + min(60, 5 * 2 ** min(rt.failures, 4))
            rt.status = "unreachable" if device.method == TCPIP else "ready"
            rt.detail = result.message
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

    # Runs on a worker thread.
    def tcpip_blocking(self, usb_serial: str) -> ConnectOutcome:
        info = self.adb.device_info(usb_serial)
        found = self.adb.wlan_ip(usb_serial)
        if not found:
            return ConnectOutcome(False, error="no_wifi")
        host = found[0]
        if not self.adb.tcpip(usb_serial, TCPIP_PORT):
            return ConnectOutcome(False, message="The device refused to switch to Wi-Fi mode.")
        time.sleep(2)
        for _ in range(6):
            if self.adb.connect(host, TCPIP_PORT).ok and self.adb.wait_for_device(f"{host}:{TCPIP_PORT}", 5):
                return ConnectOutcome(True, serial=f"{host}:{TCPIP_PORT}", info=info)
            time.sleep(1.5)
        return ConnectOutcome(False, message=f"Couldn't reach the device at {host} over Wi-Fi. "
                                             "Is it on the same network as the Frame?")

    def _note_connected(self, serial: str) -> None:
        # Treat it as connected until the next `adb devices` poll, or a discovery update in between
        # would mark it disconnected.
        if not any(e.serial == serial and e.state == "device" for e in self._entries):
            self._entries = [e for e in self._entries if e.serial != serial] + [DeviceEntry(serial, "device")]

    def device_saved(self, device: SavedDevice) -> None:
        self.store.upsert(device)
        rt = self.runtime.setdefault(device.id, Runtime())
        rt.serial = f"{device.last_host}:{device.last_port}"
        self._note_connected(rt.serial)
        rt.status, rt.detail, rt.failures, rt.connecting = "connected", "", 0, False
        self._ensure_link(device, rt.serial)
        self.devicesChanged.emit()

    def _ensure_link(self, device: SavedDevice, serial: str) -> None:
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
        link.posted.connect(lambda dev, msg: self.center.posted(dev, msg, self._visible_mirror_device()))
        link.removed.connect(self.center.removed)
        link.stateChanged.connect(lambda *_: self.devicesChanged.emit())
        self.links[device.id] = link
        link.start()

    def _stop_link(self, device_id: str, bye: bool = False) -> None:
        link = self.links.pop(device_id, None)
        if link:
            link.stop(bye=bye)

    def _companion_hello(self, device_id: str, message: dict) -> None:
        port = message.get("wakePort")
        device = self.store.get(device_id)
        # 0 on Android 10 and older, which has no Wireless debugging to turn on.
        if device and type(port) is int and 0 <= port < 65536 and port != device.wake_port:
            self.store.upsert(replace(device, wake_port=port))

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
            out.append({
                "id": device.id, "name": device.display_name, "model": device.model,
                "manufacturer": device.manufacturer, "release": device.android_release,
                "sdk": device.sdk, "method": device.method, "status": rt.status, "detail": rt.detail,
                "host": rt.serial.rpartition(":")[0] or device.last_host,
                "companion": device.companion, "companionState": link.state if link else "",
                "qsTile": device.qs_tile, "mirroring": device.id == self._mirror_device,
            })
        return out

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
            self.wizardActiveChanged.emit()

    @Slot(str, str)
    def startWizard(self, mode: str, device_id: str = "") -> None:
        exclude = {rt.serial.rpartition(":")[0] for rt in self.runtime.values() if rt.status == "connected"}
        self.wizard.start(mode, self.store.get(device_id) if device_id else None, self.services, exclude)
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
        if self.mirroring:
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
        self._mirror_device = ""
        self._mirror_mode = ""
        self._close_screen_control()
        self._close_audio()
        self.mirroringChanged.emit()
        self.devicesChanged.emit()
        if kind == "vr_unavailable":
            self.banner.emit("The overlay isn't available right now, so the device opens in a window.", "info")
            device, rt = self.store.get(device_id), self.runtime.get(device_id)
            if device and rt and rt.status == "connected":
                self._mirror_window(device, rt.serial)
            return
        if kind in ("failed", "disconnected"):
            self._mirror_message = {
                "disconnected": "The connection to your device dropped. It may have locked or left the Wi-Fi.",
            }.get(kind, "The overlay couldn't start.") + (f"\n{message}" if message and kind == "failed" else "")
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
        outcome = classify(code, "\n".join(self._mirror_log))
        self._mirror_proc = None
        self._mirror_device = ""
        self._mirror_mode = ""
        self._close_screen_control()
        self._close_audio()
        self._max_size_override = None
        if outcome.kind != "ok":
            self._mirror_message, self._mirror_retry_smaller = outcome.message, outcome.retry_smaller
            log.warning("scrcpy ended: %s (exit %s)", outcome.kind, code)
        self.mirroringChanged.emit()
        self.devicesChanged.emit()
        self._poll()

    @Slot()
    def stopMirror(self) -> None:
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
        rt.next_attempt, rt.failures = 0.0, 0
        if rt.status == "needs_pair":
            rt.status = "off"
        self._update_statuses()

    @Slot(str)
    def forgetDevice(self, device_id: str) -> None:
        """Drop everything kept for the device, and uninstall its companion while adb still reaches it."""
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
            if device.method == WIRELESS:      # even if the uninstall failed: we won't wake it again
                companion.turn_off_wireless_debugging(self.adb, serial)
            return ok

        def done(ok: bool) -> None:
            self.runner.run(self.adb.disconnect, serial)
            self.banner.emit("Companion removed." if ok else
                             f"Couldn't uninstall the companion from {device.display_name}. "
                             "Uninstall it on the device.", "info" if ok else "error")

        self.runner.run(uninstall_and_turn_off, on_done=done, on_error=lambda e: done(False))

    @Slot(str, str)
    def renameDevice(self, device_id: str, name: str) -> None:
        device = self.store.get(device_id)
        if device:
            self.store.upsert(replace(device, nickname=name.strip()))
            self.devicesChanged.emit()

    @Slot(str)
    def installCompanion(self, device_id: str) -> None:
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

        self.runner.run(companion.install_and_grant, self.adb, rt.serial, apk, device.sdk, on_done=done)

    @Slot(str)
    def removeCompanion(self, device_id: str) -> None:
        device, rt = self.store.get(device_id), self.runtime.get(device_id)
        if not device:
            return
        self._stop_link(device_id)
        self.store.upsert(replace(device, companion=False))
        if rt and rt.status == "connected":
            self.runner.run(companion.uninstall, self.adb, rt.serial,
                            on_done=lambda ok: self.banner.emit(
                                "Companion removed." if ok else "Couldn't uninstall the companion.",
                                "info" if ok else "error"))
        self.devicesChanged.emit()

    @Slot(str)
    def addTile(self, device_id: str) -> None:
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

        self.runner.run(companion.add_tile, self.adb, rt.serial, on_done=done)

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

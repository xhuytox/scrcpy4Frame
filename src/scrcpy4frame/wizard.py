"""Setup wizard state machine.

Qt-free: slow work goes out through an injected `Actions` and results come back as `*_done` calls.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Callable, Protocol

from . import companion as companion_mod
from .adb import DeviceEntry, DeviceInfo, PairResult, TCPIP_PORT
from .devices import TCPIP, WIRELESS, SavedDevice
from .discovery import Service, same_phone
from .phonechecks import needs_input_check

# Steps
BRAND = "brand"
DEVOPTS = "devopts"
WIRELESS_ON = "wireless"
PICK_PHONE = "pick_phone"
PAIR_OPEN = "pair_open"
PAIR_CODE = "pair_code"
MANUAL = "manual"
CONNECTING = "connecting"
OEM_INPUT = "oem_input"
COMPANION = "companion"
TILE = "tile"
TILE_MANUAL = "tile_manual"
TEST = "test"
DONE = "done"
USB_SETUP = "usb_setup"
USB_PLUG = "usb_plug"
USB_AUTH = "usb_auth"
USB_PERMS = "usb_perms"
USB_TCPIP = "usb_tcpip"
USB_UNPLUG = "usb_unplug"

WIRELESS_ORDER = [BRAND, DEVOPTS, WIRELESS_ON, PAIR_OPEN, PAIR_CODE, CONNECTING, COMPANION, TILE, TEST]
USB_ORDER = [BRAND, USB_SETUP, USB_PLUG, USB_TCPIP, USB_UNPLUG, COMPANION, TEST]
_PROGRESS_ALIAS = {PICK_PHONE: WIRELESS_ON, MANUAL: PAIR_CODE, OEM_INPUT: CONNECTING,
                   TILE_MANUAL: TILE, USB_AUTH: USB_PLUG, USB_PERMS: USB_PLUG, DONE: TEST}
# _TRANSIENT steps are never pushed to history. _NO_BACK steps have Back disabled.
_TRANSIENT = {CONNECTING, OEM_INPUT, USB_TCPIP, USB_UNPLUG, DONE}
_NO_BACK = _TRANSIENT | {BRAND, TEST}

HELP_AFTER_S = 45
CONNECT_WAIT_S = 20
INPUT_RECHECK_S = 3

BRANDS = ("pixel", "samsung", "xiaomi", "oneplus", "motorola", "other")


def brand_for_manufacturer(manufacturer: str) -> str:
    m = manufacturer.strip().lower()
    if m == "google":
        return "pixel"
    if m in ("xiaomi", "redmi", "poco"):
        return "xiaomi"
    if m in ("oneplus", "oppo", "realme"):
        return "oneplus"
    if m in ("samsung", "motorola"):
        return m
    return "other"


class Actions(Protocol):
    def pair(self, host: str, port: int, code: str) -> None: ...
    def connect(self, host: str, port: int) -> None: ...
    def check_input(self, serial: str) -> None: ...
    def install_companion(self, serial: str, sdk: int) -> None: ...
    def add_tile(self, serial: str) -> None: ...
    def check_tile(self, serial: str) -> None: ...
    def usb_poll(self, enabled: bool) -> None: ...
    def usb_tcpip(self, usb_serial: str) -> None: ...
    def save_device(self, device: SavedDevice) -> None: ...
    def start_mirror(self, device_id: str) -> None: ...
    def finish(self, device_id: str) -> None: ...


@dataclass
class Context:
    path: str = "wireless"                  # wireless | usb
    brand: str = ""
    target_host: str = ""
    excluded_hosts: set[str] = field(default_factory=set)
    candidates: list[Service] = field(default_factory=list)
    pair_service: Service | None = None
    connect_service: Service | None = None
    manual_connect_port: int = 0
    guid: str = ""
    serial: str = ""                        # adb serial of the TCP connection ("ip:port")
    info: DeviceInfo = field(default_factory=DeviceInfo)
    device: SavedDevice | None = None
    usb_serial: str = ""
    usb_state: str = ""
    companion_steps: dict[str, str] = field(default_factory=dict)


class Wizard:
    def __init__(self, actions: Actions, companion_available: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[], None] | None = None):
        self.actions = actions
        self.companion_available = companion_available
        self.clock = clock
        self.on_change = on_change or (lambda: None)
        self.services: list[Service] = []
        self.reset()

    def reset(self) -> None:
        self.ctx = Context()
        self.step = BRAND
        self.status = "idle"            # idle | waiting | working | ok | error
        self.message = ""
        self.help = False
        self.busy = False
        self.history: list[str] = []
        self._entered = self.clock()
        self._last_input_check = 0.0
        self._pair_succeeded = False

    def _goto(self, step: str, status: str = "idle", message: str = "", record: bool = True) -> None:
        if record and self.step != step and self.step not in _TRANSIENT:
            self.history.append(self.step)
        self.step = step
        self.status = status
        self.message = message
        self.help = False
        self._entered = self.clock()

    def _changed(self) -> None:
        self.on_change()

    def _save_device(self, **changes) -> None:
        if self.ctx.device is None:
            return
        self.ctx.device = replace(self.ctx.device, **changes) if changes else self.ctx.device
        self.actions.save_device(self.ctx.device)

    def start(self, mode: str = "new", device: SavedDevice | None = None,
              services: list[Service] | None = None, exclude_hosts: set[str] | None = None) -> None:
        """mode: "new" (wireless pairing), "usb" (cable setup) or "repair" (pair a saved phone again)."""
        self.reset()
        if services is not None:
            self.services = list(services)
        self.ctx.excluded_hosts = set(exclude_hosts or ())
        if mode == "repair" and device is not None:
            self.ctx.brand = brand_for_manufacturer(device.manufacturer)
            self.ctx.target_host = device.last_host
            self.history = []
            self.step = PAIR_OPEN
            self.status = "waiting"
            self._scan_pair_open()
        elif mode == "usb":
            self.ctx.path = "usb"
        self._changed()

    def choose_brand(self, brand: str) -> None:
        self.ctx.brand = brand if brand in BRANDS else "other"
        if self.ctx.path == "usb":
            self._goto(USB_SETUP)
        else:
            self._goto(DEVOPTS)
        self._changed()

    def next(self) -> None:
        if self.busy:
            return
        if self.step == DEVOPTS:
            self._goto(WIRELESS_ON, "waiting")
            self._scan_for_phone()
        elif self.step == USB_SETUP:
            self._goto(USB_PLUG, "waiting")
            self.actions.usb_poll(True)
        elif self.step == COMPANION and self.status == "ok":
            self._enter_tile()
        elif self.step in (TILE, TILE_MANUAL):
            self._goto(TEST)
        self._changed()

    def back(self) -> None:
        # An automatic regression like PAIR_CODE -> PAIR_OPEN can leave the current step on top.
        while self.history and self.history[-1] == self.step:
            self.history.pop()
        if self.busy or self.step in _NO_BACK or not self.history:
            return
        if self.step in (USB_PLUG, USB_AUTH, USB_PERMS):
            self.actions.usb_poll(False)
        previous = self.history.pop()
        self.step = previous
        self.status = "waiting" if previous in (WIRELESS_ON, PAIR_OPEN, USB_PLUG) else "idle"
        self.message = ""
        self.help = False
        self._entered = self.clock()
        if previous == WIRELESS_ON:
            self.ctx.candidates = []
        self._changed()

    def show_help(self) -> None:
        self.help = True
        self._changed()

    def pick(self, host: str) -> None:
        chosen = next((c for c in self.ctx.candidates if c.host == host), None)
        if chosen:
            self.ctx.target_host = chosen.host
            self.ctx.connect_service = chosen
            self._goto(PAIR_OPEN, "waiting")
            self._scan_pair_open()
            self._changed()

    def not_mine(self) -> None:
        """The found phone isn't the user's. Exclude it and keep looking."""
        if self.ctx.target_host:
            self.ctx.excluded_hosts.add(self.ctx.target_host)
        self.ctx.target_host = ""
        self.ctx.connect_service = None
        self.history = [s for s in self.history if s not in (WIRELESS_ON, PICK_PHONE, PAIR_OPEN)]
        self._goto(WIRELESS_ON, "waiting")
        self._scan_for_phone()
        self._changed()

    def use_manual(self) -> None:
        self._goto(MANUAL)
        self._changed()

    def use_usb(self) -> None:
        self.ctx.path = "usb"
        self._goto(USB_SETUP)
        self._changed()

    def submit_code(self, code: str) -> None:
        code = code.strip()
        if self.busy or self.step != PAIR_CODE or self.ctx.pair_service is None:
            return
        if len(code) != 6 or not code.isdigit():
            self.status, self.message = "error", "The pairing code is 6 digits."
            self._changed()
            return
        self.busy = True
        self.status, self.message = "working", "Pairing…"
        service = self.ctx.pair_service
        self._changed()
        self.actions.pair(service.host, service.port, code)

    def manual_submit(self, host: str, connect_port: int, pair_port: int, code: str) -> None:
        if self.busy:
            return
        host = host.strip()
        parts = host.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            self.status, self.message = "error", "Enter the IP address shown on your device, like 192.168.1.23."
        elif not (0 < int(connect_port or 0) < 65536 and 0 < int(pair_port or 0) < 65536):
            self.status, self.message = "error", "Enter both port numbers shown on your device."
        elif len(code.strip()) != 6 or not code.strip().isdigit():
            self.status, self.message = "error", "The pairing code is 6 digits."
        else:
            self.ctx.target_host = host
            self.ctx.manual_connect_port = int(connect_port)
            self.ctx.pair_service = Service("pairing", "manual", host, int(pair_port))
            self.busy = True
            self.status, self.message = "working", "Pairing…"
            self._changed()
            self.actions.pair(host, int(pair_port), code.strip())
            return
        self._changed()

    def retry(self) -> None:
        if self.busy:
            return
        if self.step == CONNECTING:
            self.status, self.message = "waiting", ""
            self._entered = self.clock()
            self._try_connect()
        elif self.step == COMPANION:
            self.accept_companion()
            return
        elif self.step == USB_TCPIP and self.ctx.usb_serial:
            self._start_tcpip()
        elif self.step == USB_UNPLUG:
            self._goto(USB_PLUG, "waiting")
            self.actions.usb_poll(True)
        self._changed()

    def skip(self) -> None:
        if self.busy:
            return
        if self.step == OEM_INPUT:
            self._enter_companion()
        elif self.step in (TILE, TILE_MANUAL):
            self._goto(TEST)
        elif self.step == COMPANION:
            self._enter_tile()
        self._changed()

    def accept_companion(self) -> None:
        if self.busy or self.step != COMPANION:
            return
        self.busy = True
        self.status, self.message = "working", "Installing the companion on your device…"
        self.ctx.companion_steps = {}
        self._changed()
        self.actions.install_companion(self.ctx.serial, self.ctx.info.sdk)

    def decline_companion(self) -> None:
        if self.busy or self.step != COMPANION:
            return
        self._save_device(companion=False)
        self._enter_tile()
        self._changed()

    def add_tile(self) -> None:
        if self.busy or self.step != TILE:
            return
        self.busy = True
        self.status, self.message = "working", "Adding the shortcut…"
        self._changed()
        self.actions.add_tile(self.ctx.serial)

    def check_again(self) -> None:
        if self.busy:
            return
        if self.step == TILE_MANUAL:
            self.busy = True
            self.status, self.message = "working", "Checking…"
            self._changed()
            self.actions.check_tile(self.ctx.serial)
        elif self.step == OEM_INPUT:
            self._check_input()
            self._changed()

    def start_mirror(self) -> None:
        if self.ctx.device is None:
            return
        self._goto(DONE)
        self._changed()
        self.actions.start_mirror(self.ctx.device.id)

    def finish(self) -> None:
        device_id = self.ctx.device.id if self.ctx.device else ""
        self._goto(DONE)
        self._changed()
        self.actions.finish(device_id)

    def services_changed(self, services: list[Service]) -> None:
        self.services = list(services)
        if self.step == WIRELESS_ON or self.step == PICK_PHONE:
            self._scan_for_phone()
        elif self.step == PAIR_OPEN:
            self._scan_pair_open()
        elif self.step == PAIR_CODE and not self.busy and not self._pair_succeeded:
            current = self.ctx.pair_service
            same = [s for s in self.services if s.kind == "pairing" and current
                    and (s.name == current.name or s.host == current.host)]
            if same:
                self.ctx.pair_service = same[0]
            else:
                self._goto(PAIR_OPEN, "waiting",
                           "The pairing window on your device closed. Tap \"Pair device with "
                           "pairing code\" again.", record=False)
        elif self.step == CONNECTING and not self.busy and self.status == "waiting":
            self._try_connect()
        self._changed()

    def tick(self) -> None:
        elapsed = self.clock() - self._entered
        changed = False
        if self.step in (WIRELESS_ON, PAIR_OPEN, USB_PLUG, PICK_PHONE) and not self.help \
                and elapsed > HELP_AFTER_S:
            self.help = True
            changed = True
        if self.step == CONNECTING and self.status == "waiting" and not self.busy \
                and elapsed > CONNECT_WAIT_S:
            self.status = "error"
            self.message = ("We can't see your device's Wireless debugging service. Make sure "
                            "Wireless debugging is still on, then tap Try again.")
            changed = True
        if self.step == OEM_INPUT and not self.busy and self.status == "waiting" \
                and self.clock() - self._last_input_check > INPUT_RECHECK_S:
            self._check_input()
            changed = True
        if changed:
            self._changed()

    def pair_done(self, result: PairResult) -> None:
        self.busy = False
        if result.ok:
            self._pair_succeeded = True
            self.ctx.guid = result.guid
            self._goto(CONNECTING, "waiting", "Paired! Connecting…")
            self._try_connect()
        elif self.step == MANUAL:
            self.status = "error"
            self.message = ("That didn't work. Check the IP address, the pairing port and the "
                            "6-digit code on your device, then try again.")
        elif result.error == "wrong_code":
            self.status = "error"
            self.message = "That code didn't match. Check the code on your device and try again."
        elif result.error == "unreachable":
            self._goto(PAIR_OPEN, "waiting",
                       "We couldn't reach the pairing window. Tap \"Pair device with pairing "
                       "code\" on your device again.", record=False)
            self._scan_pair_open()
        else:
            self.status = "error"
            self.message = "Pairing failed. Try again with a fresh code."
        self._changed()

    def connect_done(self, ok: bool, serial: str = "", info: DeviceInfo | None = None,
                     error: str = "", message: str = "") -> None:
        self.busy = False
        if ok and info is not None:
            self.ctx.serial = serial
            self._connected(info, WIRELESS, self.ctx.guid or f"wireless-{info.serialno}")
        elif error == "auth":
            self._goto(PAIR_OPEN, "waiting",
                       "Your device didn't accept the Frame's key. Let's pair again: tap \"Pair "
                       "device with pairing code\".", record=False)
            self._pair_succeeded = False
        else:
            self.status = "error"
            self.message = "Couldn't connect to your device. Make sure it's unlocked and on the same Wi-Fi."
        self._changed()

    def input_checked(self, blocked: bool) -> None:
        self.busy = False
        if self.step != OEM_INPUT:
            return
        if blocked:
            self.status, self.message = "waiting", ""
        else:
            self._enter_companion()
        self._changed()

    def companion_done(self, report: companion_mod.InstallReport) -> None:
        self.busy = False
        self.ctx.companion_steps = dict(report.steps)
        if report.ok:
            self._save_device(companion=True)
            self.status, self.message = "ok", "The companion is set up."
        else:
            self.status, self.message = "error", report.error
        self._changed()

    def tile_done(self, ok: bool) -> None:
        self.busy = False
        if ok:
            self._save_device(qs_tile=True)
            if self.step == TILE_MANUAL:
                self.status, self.message = "ok", "Found it. You're all set."
            else:
                self.status, self.message = "ok", (
                    "Added! Pull down Quick Settings, tap the pencil icon and drag "
                    "\"Wireless debugging\" to the first page.")
        elif self.step == TILE:
            self._goto(TILE_MANUAL)
        else:
            self.status, self.message = "error", (
                "We couldn't detect the tile. You can still continue.")
        self._changed()

    def usb_devices(self, entries: list[DeviceEntry]) -> None:
        usb = [e for e in entries if not e.is_tcp]
        if self.step in (USB_PLUG, USB_AUTH, USB_PERMS):
            ready = [e for e in usb if e.state == "device"]
            if ready:
                self.ctx.usb_serial = ready[0].serial
                self._goto(USB_TCPIP)
                self._start_tcpip()
            elif any(e.state == "unauthorized" for e in usb):
                if self.step != USB_AUTH:
                    self._goto(USB_AUTH, "waiting")
            elif any(e.state == "no permissions" for e in usb):
                if self.step != USB_PERMS:
                    self._goto(USB_PERMS, "waiting")
            elif self.step != USB_PLUG:
                self._goto(USB_PLUG, "waiting")
            self.ctx.usb_state = usb[0].state if usb else ""
        elif self.step == USB_UNPLUG:
            still_plugged = any(e.serial == self.ctx.usb_serial for e in usb)
            tcp_ok = any(e.serial == self.ctx.serial and e.state == "device" for e in entries)
            if not still_plugged and tcp_ok:
                self.actions.usb_poll(False)
                self._enter_companion()
            elif not still_plugged and not tcp_ok:
                self.status = "error"
                self.message = ("The Wi-Fi connection dropped when the cable was unplugged. Plug "
                                "the device back in and tap Try again.")
        self._changed()

    def tcpip_done(self, ok: bool, serial: str = "", info: DeviceInfo | None = None,
                   error: str = "", message: str = "") -> None:
        self.busy = False
        if ok and info is not None:
            self.ctx.serial = serial
            self._connected(info, TCPIP, f"tcpip-{info.serialno or serial}")
        elif error == "no_wifi":
            self.status = "error"
            self.message = "Your device isn't on Wi-Fi. Connect it to the same Wi-Fi as the Frame, then tap Try again."
        else:
            self.status = "error"
            self.message = message or "Switching the device to Wi-Fi mode failed. Tap Try again."
        self._changed()

    def _scan_for_phone(self) -> None:
        pairing = [s for s in self.services if s.kind == "pairing"
                   and s.host not in self.ctx.excluded_hosts]
        connect = [s for s in self.services if s.kind == "connect"
                   and s.host not in self.ctx.excluded_hosts]
        if len(pairing) == 1:                 # the pairing dialog is already open
            found = pairing[0]
            self.ctx.target_host = found.host
            self.ctx.connect_service = next((c for c in connect if c.host == found.host), None)
            self._goto(PAIR_CODE)
            self.ctx.pair_service = found
        elif len(connect) == 1:
            self.ctx.target_host = connect[0].host
            self.ctx.connect_service = connect[0]
            self._goto(PAIR_OPEN, "waiting")
        elif len(connect) > 1:
            self.ctx.candidates = connect
            if self.step != PICK_PHONE:
                self._goto(PICK_PHONE)
        elif self.step == PICK_PHONE:
            self._goto(WIRELESS_ON, "waiting", record=False)

    def _scan_pair_open(self) -> None:
        pairing = [s for s in self.services if s.kind == "pairing"]
        for service in self.services:
            if service.kind == "connect" and service.host == self.ctx.target_host:
                self.ctx.connect_service = service
        match = [p for p in pairing if p.host == self.ctx.target_host]
        if not match and len(pairing) == 1:
            match = pairing
        if match:
            self.ctx.target_host = match[0].host
            self.ctx.pair_service = match[0]
            self._goto(PAIR_CODE)

    def _connect_target(self) -> tuple[str, int] | None:
        connect = [s for s in self.services if s.kind == "connect"]
        if self.ctx.guid:
            for service in connect:
                if same_phone(service.name, self.ctx.guid):
                    return service.host, service.port
        for service in connect:
            if service.host == self.ctx.target_host:
                return service.host, service.port
        if self.ctx.manual_connect_port:
            return self.ctx.target_host, self.ctx.manual_connect_port
        if self.ctx.connect_service:
            return self.ctx.connect_service.host, self.ctx.connect_service.port
        return None

    def _try_connect(self) -> None:
        target = self._connect_target()
        if target is None or self.busy:
            return
        self.busy = True
        self.status, self.message = "working", "Connecting…"
        self.actions.connect(*target)

    def _connected(self, info: DeviceInfo, method: str, device_id: str) -> None:
        self.history = []          # pairing/cable steps can't be revisited once connected
        self.ctx.info = info
        self.ctx.brand = brand_for_manufacturer(info.manufacturer) or self.ctx.brand
        host, _, port = self.ctx.serial.rpartition(":")
        self.ctx.device = SavedDevice(
            id=device_id, method=method, serialno=info.serialno, model=info.model,
            manufacturer=info.manufacturer, android_release=info.release, sdk=info.sdk,
            last_host=host, last_port=int(port) if port.isdigit() else TCPIP_PORT,
            last_connected_at=time.time())
        self.actions.save_device(self.ctx.device)
        if method == WIRELESS and needs_input_check(info.manufacturer):
            self._goto(OEM_INPUT, "working", "Checking your device's settings…")
            self._check_input()
        elif method == WIRELESS:
            self._enter_companion()
        else:
            self._goto(USB_UNPLUG, "waiting")

    def _check_input(self) -> None:
        self.busy = True
        self._last_input_check = self.clock()
        self.actions.check_input(self.ctx.serial)

    def _enter_companion(self) -> None:
        if not self.companion_available or self.ctx.info.sdk < companion_mod.MIN_SDK:
            self._enter_tile()
        else:
            self._goto(COMPANION)

    def _enter_tile(self) -> None:
        if self.ctx.path == "usb" or self.ctx.info.sdk < companion_mod.WIRELESS_DEBUGGING_SDK:
            self._goto(TEST)
        else:
            self._goto(TILE)

    def _start_tcpip(self) -> None:
        self.busy = True
        self.status, self.message = "working", "Switching your device to Wi-Fi mode…"
        self.actions.usb_tcpip(self.ctx.usb_serial)

    def view(self) -> dict:
        order = USB_ORDER if self.ctx.path == "usb" else WIRELESS_ORDER
        step_for_progress = _PROGRESS_ALIAS.get(self.step, self.step)
        index = order.index(step_for_progress) if step_for_progress in order else 0
        pair = self.ctx.pair_service
        return {
            "step": self.step,
            "status": self.status,
            "message": self.message,
            "help": self.help,
            "busy": self.busy,
            "canBack": any(h != self.step for h in self.history) and not self.busy and self.step not in _NO_BACK,
            "path": self.ctx.path,
            "brand": self.ctx.brand,
            "progress": index,
            "progressTotal": len(order),
            "targetHost": self.ctx.target_host,
            # Repair mode pre-fills target_host from the saved device, so check it's really advertising.
            "targetFound": any(s.kind == "connect" and s.host == self.ctx.target_host for s in self.services),
            "candidates": [{"name": c.name, "host": c.host, "port": c.port} for c in self.ctx.candidates],
            "pairHost": pair.host if pair else "",
            "pairPort": pair.port if pair else 0,
            "usbState": self.ctx.usb_state,
            "companionSteps": dict(self.ctx.companion_steps),
            "device": {
                "id": self.ctx.device.id if self.ctx.device else "",
                "model": self.ctx.info.model,
                "manufacturer": self.ctx.info.manufacturer,
                "release": self.ctx.info.release,
                "sdk": self.ctx.info.sdk,
            },
        }

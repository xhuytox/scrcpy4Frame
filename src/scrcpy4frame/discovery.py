"""mDNS phone discovery. Android advertises `_adb-tls-connect._tcp` while Wireless debugging is on
and `_adb-tls-pairing._tcp` while the pairing-code dialog is open."""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

CONNECT_TYPE = "_adb-tls-connect._tcp.local."
PAIRING_TYPE = "_adb-tls-pairing._tcp.local."
_KIND = {CONNECT_TYPE: "connect", PAIRING_TYPE: "pairing"}


@dataclass(frozen=True)
class Service:
    kind: str       # "connect" | "pairing"
    name: str       # instance name == pairing guid, "adb-<serial>-<suffix>"
    host: str       # IPv4
    port: int


def instance_name(full_name: str, service_type: str) -> str:
    suffix = "." + service_type
    return full_name[: -len(suffix)] if full_name.endswith(suffix) else full_name


_GUID = re.compile(r"^adb-(.+)-[A-Za-z0-9]{6}$")


def serial_from_guid(guid: str) -> str | None:
    match = _GUID.match(guid)
    return match.group(1) if match else None


def same_phone(guid_a: str, guid_b: str) -> bool:
    if guid_a == guid_b:
        return True
    a, b = serial_from_guid(guid_a), serial_from_guid(guid_b)
    return bool(a) and a == b


class Discovery:
    """Background mDNS browser. `on_change(list[Service])` is called from a worker thread."""

    def __init__(self, on_change: Callable[[list[Service]], None],
                 on_error: Callable[[str], None] | None = None):
        self._on_change = on_change
        self._on_error = on_error or (lambda msg: None)
        self._lock = threading.Lock()
        self._services: dict[tuple[str, str], Service] = {}
        self._zc = None
        self._browsers = []
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.interface_ip: str | None = None
        self.error: str = ""

    def snapshot(self) -> list[Service]:
        with self._lock:
            return list(self._services.values())

    def start(self, interface_ip: str | None = None) -> bool:
        from zeroconf import IPVersion, InterfaceChoice, ServiceBrowser, Zeroconf

        self.stop()
        self.interface_ip = interface_ip
        try:
            interfaces = [interface_ip] if interface_ip else InterfaceChoice.All
            self._zc = Zeroconf(interfaces=interfaces, ip_version=IPVersion.V4Only)
        except OSError as exc:
            self.error = f"Couldn't listen for devices on the network ({exc})."
            log.error("zeroconf start failed: %s", exc)
            self._on_error(self.error)
            return False
        self.error = ""
        self._stop.clear()
        self._thread = threading.Thread(target=self._resolver, name="mdns-resolver", daemon=True)
        self._thread.start()
        self._browsers = [ServiceBrowser(self._zc, t, handlers=[self._on_service_state_change])
                          for t in (CONNECT_TYPE, PAIRING_TYPE)]
        log.info("mDNS discovery started on %s", interface_ip or "all interfaces")
        return True

    def query_now(self, host: str | None = None) -> None:
        """Ask for Wireless debugging services now, as the browser's own queries back off to minutes
        apart. With `host`, also ask it directly: a sleeping device may drop multicast."""
        from zeroconf import DNSOutgoing, DNSQuestion, const

        zc = self._zc
        if zc is None:
            return
        out = DNSOutgoing(const._FLAGS_QR_QUERY)
        out.add_question(DNSQuestion(CONNECT_TYPE, const._TYPE_PTR, const._CLASS_IN))
        try:
            zc.send(out)
            if host:
                zc.send(out, addr=host)
        except (RuntimeError, AssertionError) as exc:    # closed by a concurrent restart
            log.debug("mDNS query skipped: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._zc is not None:
            for browser in self._browsers:
                browser.cancel()
            self._zc.close()
        self._zc = None
        self._browsers = []
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None
        with self._lock:
            self._services.clear()

    # Runs on the zeroconf thread, which must not block. Resolution happens on our own thread.
    def _on_service_state_change(self, zeroconf, service_type, name, state_change) -> None:
        from zeroconf import ServiceStateChange

        if state_change is ServiceStateChange.Removed:
            key = (_KIND[service_type], instance_name(name, service_type))
            with self._lock:
                removed = self._services.pop(key, None)
            if removed:
                self._notify()
        else:
            self._queue.put((service_type, name))

    def _resolver(self) -> None:
        from zeroconf import ServiceInfo

        while not self._stop.is_set():
            item = self._queue.get()
            if item is None or self._zc is None:
                continue
            service_type, name = item
            info = ServiceInfo(service_type, name)
            if not info.request(self._zc, 3000):
                log.debug("could not resolve %s", name)
                continue
            addresses = info.parsed_addresses()
            ipv4 = [a for a in addresses if "." in a and not a.startswith("127.")]
            if not ipv4 or not info.port:
                continue
            service = Service(_KIND[service_type], instance_name(name, service_type), ipv4[0], info.port)
            with self._lock:
                changed = self._services.get((service.kind, service.name)) != service
                self._services[(service.kind, service.name)] = service
            if changed:
                log.info("found %s service %s at %s:%d", service.kind, service.name, service.host, service.port)
                self._notify()

    def _notify(self) -> None:
        try:
            self._on_change(self.snapshot())
        except Exception:  # never let a UI callback kill the resolver thread
            log.exception("discovery callback failed")


def wait_for(discovery: Discovery, predicate: Callable[[list[Service]], Service | None],
             timeout: float) -> Service | None:
    """Blocking poll for CLI use. Returns the first service `predicate` yields, or None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = predicate(discovery.snapshot())
        if found:
            return found
        time.sleep(0.25)
    return None

"""Blocking steps that give a plugged-in device Wi-Fi, so it keeps working once unplugged. Run on worker threads."""

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass

from .adb import TCPIP_PORT, Adb
from .netinfo import NetInfo, same_subnet

log = logging.getLogger(__name__)

WD_CONFIRM_S = 30       # time for the user to answer "Allow wireless debugging on this network?"


@dataclass(frozen=True)
class HandoffResult:
    ok: bool
    host: str = ""
    port: int = 0
    enabled_wd: bool = False    # this call turned Wireless debugging on, and it stayed on
    locked: bool = False        # not tried: the device is locked, so it couldn't show the prompt


def network_key(net: NetInfo) -> str:
    """Names the Frame's network, to remember where asking for Wireless debugging failed."""
    if not net.online:
        return ""
    if net.ssid:
        return net.ssid
    return f"subnet:{ipaddress.ip_network(f'{net.ipv4}/{net.prefix}', strict=False)}"


def device_address(adb: Adb, usb_serial: str, net: NetInfo) -> str:
    """The device's IPv4 on the Frame's network, or "". Mobile data, VPN or tethering don't count."""
    if not net.online:
        return ""
    try:
        addrs = adb.ipv4_addrs(usb_serial)
    except OSError as exc:
        log.info("couldn't read the addresses of %s: %s", usb_serial, exc)
        return ""
    return next((ip for ip, _prefix in addrs if same_subnet(net.ipv4, ip, net.prefix)), "")


def try_wireless_debugging(adb: Adb, usb_serial: str, host: str, find_port=lambda: 0, sleep=time.sleep,
                           clock=time.monotonic, confirm_s: float = WD_CONFIRM_S) -> HandoffResult:
    """Turns Wireless debugging on over USB and connects to it, trusting the key allowed over USB.

    `find_port` looks for the port over mDNS: some devices (Samsung) don't set service.adb.tls.port.
    """
    try:
        if adb.keyguard_showing(usb_serial):
            return HandoffResult(False, locked=True)
        was_on = adb.wireless_debugging_enabled(usb_serial) is True
        if not was_on:
            adb.set_wireless_debugging(usb_serial, True)
        deadline = clock() + confirm_s
        while True:
            # Android turns it back off until the user allows this network.
            if adb.wireless_debugging_enabled(usb_serial) is True:
                port = adb.wireless_debugging_port(usb_serial) or find_port()
                if port:
                    break
            if clock() >= deadline:
                return HandoffResult(False)
            sleep(1)
        if adb.connect(host, port).ok and adb.wait_for_device(f"{host}:{port}", 10):
            return HandoffResult(True, host, port, enabled_wd=not was_on)
        if not was_on:
            adb.set_wireless_debugging(usb_serial, False)
        return HandoffResult(False)
    except OSError as exc:
        log.info("Wireless debugging hand-off for %s failed: %s", usb_serial, exc)
        return HandoffResult(False)


def try_tcpip(adb: Adb, usb_serial: str, host: str, sleep=time.sleep) -> HandoffResult:
    """`adb tcpip 5555`, then connect over Wi-Fi. Lasts until the device restarts."""
    try:
        if not adb.tcpip(usb_serial, TCPIP_PORT):
            return HandoffResult(False)
        sleep(2)
        for _ in range(6):
            if adb.connect(host, TCPIP_PORT).ok and adb.wait_for_device(f"{host}:{TCPIP_PORT}", 5):
                return HandoffResult(True, host, TCPIP_PORT)
            sleep(1.5)
        return HandoffResult(False)
    except OSError as exc:
        log.info("tcpip hand-off for %s failed: %s", usb_serial, exc)
        return HandoffResult(False)

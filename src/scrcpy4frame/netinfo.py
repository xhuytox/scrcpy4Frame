"""The Frame's own network: Wi-Fi name and IPv4, from NetworkManager over D-Bus when available."""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass

log = logging.getLogger(__name__)

NM = "org.freedesktop.NetworkManager"


@dataclass(frozen=True)
class NetInfo:
    ipv4: str = ""
    prefix: int = 24
    ssid: str = ""
    interface: str = ""

    @property
    def online(self) -> bool:
        return bool(self.ipv4)


def same_subnet(ip_a: str, ip_b: str, prefix: int) -> bool:
    try:
        net = ipaddress.ip_network(f"{ip_a}/{prefix}", strict=False)
        return ipaddress.ip_address(ip_b) in net
    except ValueError:
        return False


def broadcast_address(ip: str, prefix: int) -> str:
    """The subnet's broadcast address, or "" without one (no address, or a /31 or /32)."""
    try:
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return ""
    return str(net.broadcast_address) if net.version == 4 and net.prefixlen < 31 else ""


def _fallback_ipv4() -> str:
    # Connecting a UDP socket sends nothing; it just picks the outbound interface.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return ""


def _nm_info() -> NetInfo | None:
    try:
        from jeepney import DBusAddress, Properties
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return None
    try:
        conn = open_dbus_connection(bus="SYSTEM")
    except Exception as exc:  # no system bus (macOS dev loop) or no permission
        log.debug("no system bus: %s", exc)
        return None

    def props(path: str, interface: str) -> dict:
        addr = DBusAddress(path, bus_name=NM, interface=interface)
        reply = conn.send_and_get_reply(Properties(addr).get_all(), timeout=2)
        return {k: v[1] for k, v in reply.body[0].items()}

    try:
        nm = props("/org/freedesktop/NetworkManager", NM)
        primary = nm.get("PrimaryConnection", "/")
        if primary in ("/", ""):
            return NetInfo()
        active = props(primary, NM + ".Connection.Active")
        ssid = active.get("Id", "")
        ipv4, prefix = "", 24
        ip4_path = active.get("Ip4Config", "/")
        if ip4_path != "/":
            ip4 = props(ip4_path, NM + ".IP4Config")
            for entry in ip4.get("AddressData", []):
                ipv4 = entry["address"][1]
                prefix = int(entry["prefix"][1])
                break
        interface = ""
        for dev_path in active.get("Devices", []):
            device = props(dev_path, NM + ".Device")
            interface = device.get("Interface", "")
            if active.get("Type") == "802-11-wireless":
                wifi = props(dev_path, NM + ".Device.Wireless")
                ap = wifi.get("ActiveAccessPoint", "/")
                if ap != "/":
                    raw = props(ap, NM + ".AccessPoint").get("Ssid", b"")
                    ssid = bytes(raw).decode(errors="replace") or ssid
            break
        return NetInfo(ipv4=ipv4, prefix=prefix, ssid=ssid, interface=interface)
    except Exception as exc:
        log.warning("NetworkManager query failed: %s", exc)
        return None
    finally:
        conn.close()


def current() -> NetInfo:
    info = _nm_info()
    if info is not None and info.ipv4:
        return info
    return NetInfo(ipv4=_fallback_ipv4(), ssid=info.ssid if info else "")

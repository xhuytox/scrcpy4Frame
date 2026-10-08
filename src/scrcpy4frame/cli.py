"""Headless commands for debugging on the Frame over SSH:

    flatpak run io.github.xhuytox.Scrcpy4Frame cli diagnose
    flatpak run io.github.xhuytox.Scrcpy4Frame cli discover --seconds 20
    flatpak run io.github.xhuytox.Scrcpy4Frame cli pair 192.168.1.23 37099 123456
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from . import __version__, netinfo, paths
from .adb import Adb, AdbServer, make_env
from .discovery import Discovery


def _adb() -> Adb:
    path = paths.tool_path("adb")
    if not path:
        sys.exit("adb not found")
    return Adb(path, make_env())


def _version(tool: str | None, flag: str = "--version") -> str:
    if not tool:
        return "missing"
    try:
        out = subprocess.run([tool, flag], capture_output=True, text=True, timeout=5)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError) as exc:
        return f"error: {exc}"


def cmd_diagnose(_args) -> int:
    info = netinfo.current()
    rows = [
        ("scrcpy4Frame", __version__),
        ("in Flatpak", paths.in_flatpak()),
        ("adb", f"{paths.tool_path('adb')}  ({_version(paths.tool_path('adb'))})"),
        ("scrcpy", f"{paths.tool_path('scrcpy')}  ({_version(paths.tool_path('scrcpy'))})"),
        ("scrcpy-server", paths.scrcpy_server_path() or "scrcpy default"),
        ("vr-toast", paths.tool_path("vr-toast") or "missing"),
        ("companion.apk", paths.companion_apk_path() or "missing"),
        ("Wi-Fi", f"{info.ssid or '?'}  {info.ipv4 or 'no IPv4'}/{info.prefix}  ({info.interface or '?'})"),
        ("adb key home", paths.adb_home()),
    ]
    for name, value in rows:
        print(f"{name:>14}: {value}")
    return 0


def cmd_discover(args) -> int:
    info = netinfo.current()
    seen: dict = {}
    discovery = Discovery(on_change=lambda services: seen.update({(s.kind, s.name): s for s in services}),
                          on_error=lambda msg: print("error:", msg, file=sys.stderr))
    if not discovery.start(info.ipv4 or None):
        return 1
    print(f"Listening on {info.ipv4 or 'all interfaces'} for {args.seconds}s… "
          "(turn on Wireless debugging / open the pairing window on the device)")
    try:
        time.sleep(args.seconds)
    finally:
        discovery.stop()
    for service in seen.values():
        print(f"{service.kind:8} {service.name:40} {service.host}:{service.port}")
    if not seen:
        print("No devices found.")
    return 0 if seen else 2


def _with_server(fn):
    adb = _adb()
    server = AdbServer(adb)
    if not server.start():
        sys.exit("could not start the private adb server")
    try:
        return fn(adb)
    finally:
        server.stop()


def cmd_pair(args) -> int:
    result = _with_server(lambda adb: adb.pair(args.host, args.port, args.code))
    print(result)
    return 0 if result.ok else 1


def cmd_connect(args) -> int:
    def run(adb: Adb):
        result = adb.connect(args.host, args.port)
        print(result)
        if result.ok:
            serial = f"{args.host}:{args.port}"
            adb.wait_for_device(serial)
            print(adb.device_info(serial))
        return result.ok
    return 0 if _with_server(run) else 1


def cmd_devices(_args) -> int:
    for entry in _with_server(lambda adb: adb.devices()):
        print(entry.serial, entry.state, entry.props)
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="scrcpy4frame cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("diagnose", help="show bundled tools, network and paths")
    discover = sub.add_parser("discover", help="list devices advertising wireless debugging over mDNS")
    discover.add_argument("--seconds", type=int, default=15)
    pair = sub.add_parser("pair", help="pair using the code shown on the device")
    pair.add_argument("host")
    pair.add_argument("port", type=int)
    pair.add_argument("code")
    connect = sub.add_parser("connect", help="connect to the Wireless debugging port")
    connect.add_argument("host")
    connect.add_argument("port", type=int)
    sub.add_parser("devices", help="list adb devices on the private server")
    args = parser.parse_args(argv)
    return {"diagnose": cmd_diagnose, "discover": cmd_discover, "pair": cmd_pair,
            "connect": cmd_connect, "devices": cmd_devices}[args.command](args)

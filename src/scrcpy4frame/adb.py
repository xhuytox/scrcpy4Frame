"""Blocking, Qt-free adb client and a private adb server kept apart from host adb and Lepton."""

from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from . import paths

log = logging.getLogger(__name__)

ADB_SERVER_PORT = 5038
TCPIP_PORT = 5555


def make_env(game_mode: bool = False) -> dict[str, str]:
    """Env for adb/scrcpy child processes. Not applied to the Qt process."""
    env = dict(os.environ)
    env.update({
        "ANDROID_ADB_SERVER_PORT": str(ADB_SERVER_PORT),
        "ADB_EMU": "0",                    # don't probe 127.0.0.1:5555-5585 (Lepton instances)
        "ADB_MDNS_AUTO_CONNECT": "0",      # we discover and connect ourselves
        "HOME": str(paths.adb_home()),     # adb key lives in $HOME/.android
        "LOGNAME": "scrcpy4Frame",         # key comment -> phone shows "scrcpy4Frame@SteamFrame"
        "USER": "scrcpy4Frame",
        "HOSTNAME": "SteamFrame",
    })
    adb = paths.tool_path("adb")
    if adb:
        env["ADB"] = adb
    server = paths.scrcpy_server_path()
    if server:
        env["SCRCPY_SERVER_PATH"] = server
    if game_mode:
        env["SDL_VIDEODRIVER"] = "x11"
    return env


@dataclass(frozen=True)
class CmdResult:
    rc: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return (self.out + "\n" + self.err).strip()


@dataclass(frozen=True)
class DeviceEntry:
    serial: str
    state: str                      # device | unauthorized | offline | no permissions | ...
    props: dict[str, str] = field(default_factory=dict)

    @property
    def is_tcp(self) -> bool:
        return is_tcp_serial(self.serial)


_TCP_SERIAL = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]):(\d+)$")


def is_tcp_serial(serial: str) -> bool:
    return bool(_TCP_SERIAL.match(serial))


def is_ignored_serial(serial: str) -> bool:
    """Emulators, local (Lepton) instances, and duplicate mDNS transports.

    Some adb builds also connect a newly paired phone by its mDNS name despite
    ADB_MDNS_AUTO_CONNECT=0. We only use ip:port, and that duplicate must not look like USB.
    """
    return (serial.startswith("emulator-") or serial.startswith("127.")
            or serial.startswith("localhost") or serial.startswith("[::1]")
            or "._adb-tls-connect._tcp" in serial or "._adb._tcp" in serial)


def parse_devices(out: str) -> list[DeviceEntry]:
    entries = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        serial = parts[0]
        rest = line[len(serial):].strip()
        if rest.startswith("no permissions"):
            state = "no permissions"
        else:
            state = parts[1] if len(parts) > 1 else "unknown"
        props = {}
        for token in parts[2:]:
            if ":" in token and not token.startswith("("):
                key, _, value = token.partition(":")
                if key.isidentifier():
                    props[key] = value
        if not is_ignored_serial(serial):
            entries.append(DeviceEntry(serial, state, props))
    return entries


@dataclass(frozen=True)
class PairResult:
    ok: bool
    guid: str = ""
    error: str = ""          # "", "wrong_code", "unreachable", "other"
    message: str = ""


_PAIR_OK = re.compile(r"Successfully paired to (\S+) \[guid=([^\]]+)\]")


def parse_pair(result: CmdResult) -> PairResult:
    match = _PAIR_OK.search(result.out)
    if match:
        return PairResult(True, guid=match.group(2), message=match.group(0))
    text = result.text
    if "Wrong password" in text:
        return PairResult(False, error="wrong_code", message=text)
    if ("Unable to start pairing client" in text or "Connection refused" in text
            or "No route to host" in text or "timed out" in text.lower()):
        return PairResult(False, error="unreachable", message=text)
    return PairResult(False, error="other", message=text or f"adb pair exited with {result.rc}")


@dataclass(frozen=True)
class ConnectResult:
    ok: bool
    error: str = ""          # "", "auth", "refused", "unreachable", "other"
    message: str = ""


def parse_connect(result: CmdResult) -> ConnectResult:
    """`adb connect` exits 0 even on failure, so only the text decides."""
    text = result.text
    if "connected to" in text and "failed" not in text and "cannot" not in text:
        return ConnectResult(True, message=text)
    if "authenticate" in text or "unauthorized" in text:
        return ConnectResult(False, "auth", text)
    if "refused" in text:
        return ConnectResult(False, "refused", text)
    if "No route to host" in text or "timed out" in text.lower() or "unreachable" in text:
        return ConnectResult(False, "unreachable", text)
    return ConnectResult(False, "other", text or f"adb connect exited with {result.rc}")


def parse_ipv4_addrs(out: str) -> list[tuple[str, int]]:
    """Every non-loopback IPv4 and prefix from `ip -o -4 addr` output, in order."""
    return [(m.group(1), int(m.group(2)))
            for m in re.finditer(r"inet (\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})", out)
            if not m.group(1).startswith("127.")]


def parse_wlan_ip(out: str) -> tuple[str, int] | None:
    """First non-loopback IPv4 and prefix from `ip -o -4 addr` output."""
    found = parse_ipv4_addrs(out)
    return found[0] if found else None


def parse_tcpip(result: CmdResult) -> bool:
    return "restarting in TCP mode" in result.text


def parse_getprop_lines(out: str) -> dict[str, str]:
    """Parse `getprop` output lines like `[ro.product.model]: [Pixel 8]`."""
    props = {}
    for match in re.finditer(r"^\[([^\]]+)\]: \[(.*)\]\s*$", out, re.MULTILINE):
        props[match.group(1)] = match.group(2)
    return props


@dataclass(frozen=True)
class DeviceInfo:
    serialno: str = ""
    model: str = ""
    manufacturer: str = ""
    brand: str = ""
    release: str = ""
    sdk: int = 0

    @classmethod
    def from_props(cls, props: dict[str, str]) -> "DeviceInfo":
        try:
            sdk = int(props.get("ro.build.version.sdk", "0") or 0)
        except ValueError:
            sdk = 0
        return cls(
            serialno=props.get("ro.serialno", "") or props.get("ro.boot.serialno", ""),
            model=props.get("ro.product.model", ""),
            manufacturer=props.get("ro.product.manufacturer", ""),
            brand=props.get("ro.product.brand", ""),
            release=props.get("ro.build.version.release", ""),
            sdk=sdk,
        )


Runner = Callable[[Sequence[str], dict, float], CmdResult]


def _subprocess_runner(args: Sequence[str], env: dict, timeout: float) -> CmdResult:
    try:
        proc = subprocess.run(list(args), env=env, capture_output=True, text=True,
                              timeout=timeout, errors="replace")
        return CmdResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return CmdResult(124, out, f"timed out after {timeout:.0f}s")
    except OSError as exc:
        return CmdResult(127, "", str(exc))


class Adb:
    def __init__(self, adb_path: str, env: dict[str, str] | None = None,
                 runner: Runner = _subprocess_runner):
        self.adb_path = adb_path
        self.env = env if env is not None else make_env()
        self._runner = runner

    def run(self, *args: str, serial: str | None = None, timeout: float = 15) -> CmdResult:
        argv = [self.adb_path]
        if serial:
            argv += ["-s", serial]
        argv += list(args)
        result = self._runner(argv, self.env, timeout)
        log.debug("adb %s -> rc=%s out=%r err=%r", " ".join(argv[1:]), result.rc,
                  result.out[-300:], result.err[-300:])
        return result

    def devices(self) -> list[DeviceEntry]:
        return parse_devices(self.run("devices", "-l", timeout=10).out)

    def pair(self, host: str, port: int, code: str) -> PairResult:
        return parse_pair(self.run("pair", f"{host}:{port}", code, timeout=25))

    def connect(self, host: str, port: int) -> ConnectResult:
        return parse_connect(self.run("connect", f"{host}:{port}", timeout=15))

    def disconnect(self, serial: str) -> None:
        self.run("disconnect", serial, timeout=5)

    def get_state(self, serial: str) -> str:
        result = self.run("get-state", serial=serial, timeout=5)
        return result.out.strip() if result.rc == 0 else ""

    def wait_for_device(self, serial: str, timeout: float = 10) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.get_state(serial) == "device":
                return True
            time.sleep(0.5)
        return False

    def shell(self, serial: str, command: str, timeout: float = 15) -> CmdResult:
        # adb joins remote arguments with spaces, so pass the command as one pre-quoted string.
        return self.run("shell", command, serial=serial, timeout=timeout)

    def responds(self, serial: str, timeout: float = 5) -> bool:
        """The device answers over this connection. `adb devices` keeps listing a Wi-Fi connection as
        "device" for minutes after the device left the network."""
        result = self.shell(serial, "echo ok", timeout=timeout)
        return result.rc == 0 and result.out.strip() == "ok"

    def device_info(self, serial: str) -> DeviceInfo:
        return DeviceInfo.from_props(parse_getprop_lines(self.shell(serial, "getprop").out))

    def tcpip(self, serial: str, port: int = TCPIP_PORT) -> bool:
        return parse_tcpip(self.run("tcpip", str(port), serial=serial, timeout=10))

    def wlan_ip(self, serial: str) -> tuple[str, int] | None:
        found = parse_wlan_ip(self.shell(serial, "ip -o -4 addr show wlan0").out)
        return found or parse_wlan_ip(self.shell(serial, "ip -o -4 addr").out)

    def ipv4_addrs(self, serial: str) -> list[tuple[str, int]]:
        return parse_ipv4_addrs(self.shell(serial, "ip -o -4 addr").out)

    def wireless_debugging_enabled(self, serial: str) -> bool | None:
        value = self.shell(serial, "settings get global adb_wifi_enabled").out.strip()
        return {"1": True, "0": False}.get(value)

    def set_wireless_debugging(self, serial: str, on: bool) -> None:
        self.shell(serial, f"settings put global adb_wifi_enabled {1 if on else 0}")

    def keyguard_showing(self, serial: str) -> bool:
        """The lock screen is up, so the device can't show a prompt."""
        out = self.shell(serial, "dumpsys window").out
        return "isKeyguardShowing=true" in out or "mDreamingLockscreen=true" in out

    def wireless_debugging_port(self, serial: str) -> int:
        """The port Wireless debugging listens on (Android 11+), or 0 while it's off."""
        value = self.shell(serial, "getprop service.adb.tls.port").out.strip()
        return int(value) if value.isdigit() and int(value) > 0 else 0

    def install(self, serial: str, apk: str | Path) -> CmdResult:
        return self.run("install", "-r", str(apk), serial=serial, timeout=180)

    def uninstall(self, serial: str, package: str) -> CmdResult:
        return self.run("uninstall", package, serial=serial, timeout=60)

    def forward(self, serial: str, remote: str) -> int | None:
        result = self.run("forward", "tcp:0", remote, serial=serial, timeout=10)
        text = result.out.strip()
        return int(text) if result.rc == 0 and text.isdigit() else None

    def forward_remove(self, serial: str, local_port: int) -> None:
        self.run("forward", "--remove", f"tcp:{local_port}", serial=serial, timeout=5)

    def kill_server(self) -> None:
        self.run("kill-server", timeout=5)


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def server_argv(adb_path: str) -> list[str]:
    if os.environ.get("SCRCPY4FRAME_NO_USB") == "1":
        # Pinning to a nonexistent serial keeps the server off USB, so plugged-in phones don't
        # prompt "Allow USB debugging?" for our key.
        return [adb_path, "--one-device", "scrcpy4frame-no-usb", "server", "nodaemon"]
    return [adb_path, "server", "nodaemon"]


class AdbServer:
    """`adb server nodaemon` on our own port as a child process, killed on exit.

    A detached daemon would keep the Flatpak sandbox alive and Steam would show the app as running.
    """

    def __init__(self, adb: Adb, log_path: Path | None = None):
        self.adb = adb
        self.log_path = log_path or paths.cache_dir() / "adb-server.log"
        self._proc: subprocess.Popen | None = None

    def start(self, timeout: float = 10) -> bool:
        if _port_open(ADB_SERVER_PORT):
            log.info("adb server already listening on %d; reusing it", ADB_SERVER_PORT)
            return True
        log_file = open(self.log_path, "ab")
        self._proc = subprocess.Popen(server_argv(self.adb.adb_path),
                                      env=self.adb.env, stdout=log_file, stderr=log_file,
                                      stdin=subprocess.DEVNULL)
        log_file.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _port_open(ADB_SERVER_PORT):
                log.info("adb server started (pid %s)", self._proc.pid)
                return True
            if self._proc.poll() is not None:
                log.error("adb server exited early with %s", self._proc.returncode)
                return False
            time.sleep(0.1)
        log.error("adb server did not open port %d", ADB_SERVER_PORT)
        return False

    def stop(self) -> None:
        if self._proc is None:
            return                  # reused someone else's server (the running app's): leave it up
        self.adb.kill_server()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

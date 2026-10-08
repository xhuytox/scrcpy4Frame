"""Toggle the phone's display mid-mirror through a separate control-only scrcpy session.

The scrcpy client doesn't share its control socket. The server restores the display when our
session ends, so a crash never leaves the phone dark. Blocking: call from a worker thread.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
from typing import Callable

from . import paths, vrmirror
from .adb import Adb

log = logging.getLogger(__name__)

TYPE_SET_DISPLAY_POWER = 10
DEVICE_NAME_LENGTH = 64
CONNECT_TIMEOUT_S = 10


def display_power_message(on: bool) -> bytes:
    return bytes([TYPE_SET_DISPLAY_POWER, 1 if on else 0])


def control_only_command(version: str, scid: str) -> str:
    return (f"CLASSPATH={vrmirror.SERVER_REMOTE_PATH} app_process / com.genymobile.scrcpy.Server {version} "
            f"scid={scid} log_level=info tunnel_forward=true video=false audio=false control=true "
            "clipboard_autosync=false power_on=false")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


class ScreenSession:
    """One control-only scrcpy session, opened on first use and kept until close()."""

    def __init__(self, adb: Adb, serial: str,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                 connect: Callable[..., socket.socket] = socket.create_connection):
        self.adb, self.serial = adb, serial
        self._popen, self._connect = popen, connect
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._port: int | None = None
        self._closed = False

    def set_display_power(self, on: bool) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._ensure()
            self._sock.sendall(display_power_message(on))
            log.info("phone display turned %s", "on" if on else "off")
            return on

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._sock is not None:
                try:
                    self._sock.close()          # the server exits and restores the display
                except OSError:
                    pass
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(3)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            if self._port:
                self.adb.forward_remove(self.serial, self._port)
            self._sock = self._proc = self._port = None

    def _ensure(self) -> None:
        if self._sock is not None:
            return
        version = vrmirror.push_server(self.adb, self.serial)
        scid = vrmirror.make_scid()
        self._port = self.adb.forward(self.serial, f"localabstract:scrcpy_{scid}")
        if not self._port:
            raise RuntimeError("adb forward failed")
        # The server's log ("Device display turned off") is the only proof the panel changed:
        # dumpsys keeps reporting the display as ON.
        server_log = open(paths.cache_dir() / "screen-control.log", "ab")
        self._proc = self._popen(
            [self.adb.adb_path, "-s", self.serial, "shell", control_only_command(version, scid)],
            env=self.adb.env, stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT)
        server_log.close()
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while True:
            # adb forward accepts connections before the server listens. Only a real connection
            # receives the dummy byte and device name.
            try:
                sock = self._connect(("127.0.0.1", self._port), timeout=2)
                if len(_recv_exact(sock, 1 + DEVICE_NAME_LENGTH)) == 1 + DEVICE_NAME_LENGTH:
                    self._sock = sock
                    return
                sock.close()
            except OSError:
                pass
            if time.monotonic() > deadline or (self._proc.poll() is not None):
                raise RuntimeError("couldn't reach the device to change its screen")
            time.sleep(0.2)

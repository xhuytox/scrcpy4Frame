"""Live connection to the companion's notification socket, tunnelled through `adb forward`."""

from __future__ import annotations

import base64
import logging
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

from . import companion
from .adb import Adb
from .protocol import LineBuffer, encode
from .tasks import TaskRunner

log = logging.getLogger(__name__)

PING_EVERY_MS = 15_000
SILENCE_LIMIT_S = 45
RETRY_MS = 10_000
BYE_WAIT_MS = 1000


class CompanionLink(QObject):
    posted = Signal(str, dict)          # device_id, message
    removed = Signal(str, str)          # device_id, key
    stateChanged = Signal(str, str)     # device_id, connecting | connected | waiting | stopped
    hello = Signal(str, dict)

    def __init__(self, device_id: str, serial: str, adb: Adb, runner: TaskRunner,
                 wake_key: bytes | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.device_id = device_id
        self.serial = serial
        self.adb = adb
        self.runner = runner
        self.wake_key = wake_key
        self.state = "stopped"
        self.info: dict = {}
        self._port: int | None = None
        self._buffer = LineBuffer()
        self._last_rx = 0.0
        self._stopped = True
        self._socket = QTcpSocket(self)
        self._socket.readyRead.connect(self._read)
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_lost)
        self._socket.errorOccurred.connect(lambda _err: self._on_lost())
        self._ping = QTimer(self, interval=PING_EVERY_MS, timeout=self._keepalive)
        self._retry = QTimer(self, singleShot=True, interval=RETRY_MS, timeout=self._open)

    def start(self) -> None:
        if not self._stopped:
            return
        self._stopped = False
        self._open()

    def stop(self, bye: bool = False) -> None:
        """With `bye`, first tells the companion this app is leaving, so it turns Wireless debugging
        off, and waits up to BYE_WAIT_MS for it to hang up, which shows the message arrived."""
        self._stopped = True
        self._ping.stop()
        self._retry.stop()
        if bye and self.state == "connected" and self.can_wake:
            self.send({"type": "bye"})
            self._socket.flush()
            self._socket.waitForDisconnected(BYE_WAIT_MS)
        self._socket.abort()
        if self._port:
            self.runner.run(self.adb.forward_remove, self.serial, self._port)
            self._port = None
        self._set_state("stopped")

    @property
    def can_wake(self) -> bool:
        """The companion takes `wakeKey` and `bye`. It says so with `wakePort` (protocol 2)."""
        return "wakePort" in self.info

    def send(self, message: dict) -> None:
        if self._socket.state() == QAbstractSocket.SocketState.ConnectedState:
            self._socket.write(encode(message))

    def dismiss(self, key: str) -> None:
        self.send({"type": "dismiss", "key": key})

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.stateChanged.emit(self.device_id, state)

    def _open(self) -> None:
        if self._stopped:
            return
        self._set_state("connecting")
        if self._port:
            self._connect_socket(self._port)
        else:
            self.runner.run(self.adb.forward, self.serial, f"localabstract:{companion.SOCKET_NAME}",
                            on_done=self._connect_socket)

    def _connect_socket(self, port: int | None) -> None:
        if self._stopped:
            return
        if not port:
            log.info("adb forward to the companion failed for %s", self.device_id)
            self._schedule_retry()
            return
        self._port = port
        self._buffer = LineBuffer()
        self._socket.abort()
        self._socket.connectToHost("127.0.0.1", port)

    def _on_connected(self) -> None:
        self._last_rx = time.monotonic()
        self._ping.start()

    def _read(self) -> None:
        self._last_rx = time.monotonic()
        for message in self._buffer.feed(bytes(self._socket.readAll().data())):
            kind = message["type"]
            if kind == "hello":
                self.info = message
                if self.wake_key and self.can_wake:
                    self.send({"type": "wakeKey", "key": base64.b64encode(self.wake_key).decode()})
                self._set_state("connected")
                self.hello.emit(self.device_id, message)
            elif kind == "posted":
                self.posted.emit(self.device_id, message)
            elif kind == "removed":
                self.removed.emit(self.device_id, str(message.get("key", "")))
            elif kind == "error":
                log.warning("companion on %s: %s", self.device_id, message.get("message"))

    def _keepalive(self) -> None:
        if time.monotonic() - self._last_rx > SILENCE_LIMIT_S:
            log.info("companion on %s went quiet; reconnecting", self.device_id)
            self._socket.abort()
            self._on_lost()
            return
        self.send({"type": "ping"})

    def _on_lost(self) -> None:
        self._ping.stop()
        if not self._stopped:
            self._schedule_retry()

    def _schedule_retry(self) -> None:
        self._set_state("waiting")
        if not self._retry.isActive():
            self._retry.start()

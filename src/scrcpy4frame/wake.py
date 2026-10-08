"""Wakes companions over UDP so their device turns Wireless debugging on (see docs/CONTRACTS.md).

The companion keeps Wireless debugging on only while this app is around, so adb can't reach a
device until it's woken. A companion answers `wake` with a single-use nonce and turns Wireless
debugging on once we prove, with an HMAC over that nonce, that we hold the key we gave it over adb.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

log = logging.getLogger(__name__)

KEY_BYTES = 32
MAX_DATAGRAM = 512
MAC_PREFIX = b"scrcpy4frame-wake:"
# Challenges are answered only this soon after a wake went out, so nobody can use this app to
# wake a device while it isn't trying to.
ANSWER_WINDOW_S = 5.0


def load_or_create_key(path: Path) -> bytes | None:
    """This Frame's wake key, created on first use. None if it can't be read or written."""
    try:
        key = path.read_bytes()
        if 16 <= len(key) <= 64:
            return key
        log.warning("ignoring a wake key of %d bytes in %s", len(key), path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.error("could not read %s: %s", path, exc)
        return None
    key = secrets.token_bytes(KEY_BYTES)
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        os.replace(tmp, path)
    except OSError as exc:
        log.error("could not write %s: %s", path, exc)
        return None
    return key


def mac(key: bytes, nonce: str) -> str:
    return hmac.new(key, MAC_PREFIX + nonce.encode(), hashlib.sha256).hexdigest()


def parse(data: bytes) -> dict | None:
    try:
        message = json.loads(data)
    except ValueError:
        return None
    return message if isinstance(message, dict) and isinstance(message.get("type"), str) else None


class WakeSender(QObject):
    """Sends `wake` datagrams and answers the companions' challenges."""

    woke = Signal(str)      # address of a companion that accepted

    def __init__(self, key: bytes, parent: QObject | None = None):
        super().__init__(parent)
        self.key = key
        self._last_wake = float("-inf")
        self._socket = QUdpSocket(self)
        self._socket.readyRead.connect(self._read)
        if not self._socket.bind(QHostAddress(QHostAddress.SpecialAddress.AnyIPv4), 0):
            log.warning("wake socket: %s", self._socket.errorString())

    def wake(self, targets: Iterable[tuple[str, int]]) -> None:
        """Send `wake` to each (address, port). Broadcast addresses work too."""
        self._last_wake = time.monotonic()
        for host, port in targets:
            self._send(host, port, {"type": "wake"})

    def close(self) -> None:
        self._socket.close()

    def _send(self, host: str, port: int, message: dict) -> None:
        data = json.dumps(message, separators=(",", ":")).encode()
        if self._socket.writeDatagram(data, QHostAddress(host), port) < 0:
            log.debug("wake to %s:%d failed: %s", host, port, self._socket.errorString())

    def _read(self) -> None:
        while self._socket.hasPendingDatagrams():
            datagram = self._socket.receiveDatagram(MAX_DATAGRAM)
            message = parse(bytes(datagram.data().data()))
            if message is None or time.monotonic() - self._last_wake > ANSWER_WINDOW_S:
                continue
            host, port = datagram.senderAddress().toString(), datagram.senderPort()
            nonce = message.get("nonce")
            if message["type"] == "challenge" and isinstance(nonce, str) and 0 < len(nonce) <= 64:
                self._send(host, port, {"type": "wake", "nonce": nonce, "mac": mac(self.key, nonce)})
            elif message["type"] == "awake":
                log.info("companion at %s turned Wireless debugging on", host)
                self.woke.emit(host)

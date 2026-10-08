"""Waking companions over UDP, and the companion link's `wakeKey` and `bye`."""

import base64
import json
import os
import socket
import stat
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scrcpy4frame import wake  # noqa: E402
from scrcpy4frame.netinfo import broadcast_address  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication([])


def _wait_for(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.005)
    return predicate()


def test_mac_matches_the_companion():
    # Same vector as the companion's WakeHandshakeTest.
    assert wake.mac(bytes(range(32)), "000102030405060708090a0b0c0d0e0f") == (
        "2912d1a5bebfed62d2685a0b1c529aabdc7e3394494eb02ca48b47dd1281e6b4")


def test_key_is_created_once_and_private(tmp_path):
    path = tmp_path / "wake-key"
    key = wake.load_or_create_key(path)
    assert key is not None and len(key) == wake.KEY_BYTES
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert wake.load_or_create_key(path) == key


def test_a_bad_key_file_is_replaced(tmp_path):
    path = tmp_path / "wake-key"
    path.write_bytes(b"short")
    key = wake.load_or_create_key(path)
    assert key is not None and len(key) == wake.KEY_BYTES and path.read_bytes() == key


def test_broadcast_address():
    assert broadcast_address("192.168.1.23", 24) == "192.168.1.255"
    assert broadcast_address("10.1.2.3", 16) == "10.1.255.255"
    assert broadcast_address("10.1.2.3", 32) == ""
    assert broadcast_address("", 24) == ""


class FakeCompanion:
    """The device side of the handshake on a localhost UDP port."""

    def __init__(self, key: bytes):
        self.key = key
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(3)
        self.port = self.sock.getsockname()[1]

    def receive(self):
        data, sender = self.sock.recvfrom(512)
        return json.loads(data), sender

    def send(self, message, to):
        self.sock.sendto(json.dumps(message).encode(), to)


def test_handshake_with_a_companion(app):
    key = bytes(range(32))
    sender = wake.WakeSender(key)
    woke = []
    sender.woke.connect(woke.append)
    companion = FakeCompanion(key)
    try:
        sender.wake([("127.0.0.1", companion.port)])
        message, frame = companion.receive()
        assert message == {"type": "wake"}
        companion.send({"type": "challenge", "nonce": "00ff" * 8}, frame)
        assert _wait_for(app, lambda: _readable(companion.sock))
        answer, _ = companion.receive()
        assert answer == {"type": "wake", "nonce": "00ff" * 8, "mac": wake.mac(key, "00ff" * 8)}
        companion.send({"type": "awake"}, frame)
        assert _wait_for(app, lambda: woke == ["127.0.0.1"])
    finally:
        companion.sock.close()
        sender.close()


def test_challenges_are_only_answered_right_after_a_wake(app, monkeypatch):
    sender = wake.WakeSender(bytes(range(32)))
    companion = FakeCompanion(sender.key)
    try:
        sender.wake([("127.0.0.1", companion.port)])
        _, frame = companion.receive()
        monkeypatch.setattr(wake, "ANSWER_WINDOW_S", -1.0)      # the wake is long past
        companion.send({"type": "challenge", "nonce": "ab" * 16}, frame)
        _wait_for(app, lambda: False, timeout=0.3)
        assert not _readable(companion.sock)
    finally:
        companion.sock.close()
        sender.close()


def _readable(sock) -> bool:
    import select

    return bool(select.select([sock], [], [], 0)[0])


class FakeCompanionSocket(threading.Thread):
    """Stands in for the adb forward to the companion's socket: sends `hello`, records lines and
    hangs up after `bye`, as the companion does."""

    def __init__(self, hello: dict):
        super().__init__(daemon=True)
        self.hello = hello
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.lines: list[dict] = []

    def run(self):
        conn, _ = self.server.accept()
        conn.sendall((json.dumps(self.hello) + "\n").encode())
        buffer = b""
        with conn:
            while True:
                data = conn.recv(4096)
                if not data:
                    return
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self.lines.append(json.loads(line))
                    if self.lines[-1]["type"] == "bye":
                        return


class _NoAdb:
    def forward_remove(self, serial, port):
        pass


class _Runner:
    def run(self, fn, *args, on_done=None, on_error=None):
        fn(*args)


def _link(app, server, key):
    from scrcpy4frame.companion_link import CompanionLink

    link = CompanionLink("dev", "192.168.1.23:41235", _NoAdb(), _Runner(), wake_key=key)
    link._stopped = False
    link._connect_socket(server.port)
    assert _wait_for(app, lambda: link.state == "connected")
    return link


def test_link_gives_the_companion_its_key_and_says_bye(app):
    key = bytes(range(32))
    server = FakeCompanionSocket({"type": "hello", "protocol": 2, "wakePort": 38490})
    server.start()
    link = _link(app, server, key)
    assert _wait_for(app, lambda: server.lines)
    assert server.lines[0] == {"type": "wakeKey", "key": base64.b64encode(key).decode()}
    started = time.monotonic()
    link.stop(bye=True)
    server.join(2)
    assert server.lines[-1] == {"type": "bye"}
    assert time.monotonic() - started < 0.9      # it waited for the hang-up, not the timeout


def test_old_companions_get_neither_key_nor_bye(app):
    server = FakeCompanionSocket({"type": "hello", "protocol": 1})
    server.start()
    link = _link(app, server, bytes(range(32)))
    link.send({"type": "ping"})
    assert _wait_for(app, lambda: server.lines)
    link.stop(bye=True)
    server.join(2)
    assert server.lines == [{"type": "ping"}]


def test_discovery_can_ask_a_woken_device_directly():
    from scrcpy4frame.discovery import CONNECT_TYPE, Discovery

    sent = []

    class FakeZeroconf:
        def send(self, out, addr=None, port=5353):
            sent.append((addr, [q.name for q in out.questions]))

    discovery = Discovery(on_change=lambda services: None)
    discovery.query_now("192.168.1.23")               # not started: nothing to send with
    discovery._zc = FakeZeroconf()
    discovery.query_now("192.168.1.23")
    assert sent == [(None, [CONNECT_TYPE]), ("192.168.1.23", [CONNECT_TYPE])]

import socket
import threading

from scrcpy4frame import screencontrol, vrmirror
from scrcpy4frame.adb import CmdResult


class FakeAdb:
    adb_path, env = "adb", {}

    def __init__(self):
        self.calls = []

    def run(self, *args, serial=None, timeout=15):
        self.calls.append(("run",) + args)
        return CmdResult(0, "1 file pushed", "")

    def forward(self, serial, remote):
        self.calls.append(("forward", remote))
        return 40123

    def forward_remove(self, serial, port):
        self.calls.append(("forward_remove", port))


class FakeProc:
    def __init__(self, argv, **kwargs):
        self.argv, self.terminated = argv, False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def test_control_only_session_sends_display_power(monkeypatch):
    monkeypatch.setattr(vrmirror, "scrcpy_version", lambda: "5.0")
    monkeypatch.setattr(vrmirror.paths, "scrcpy_server_file", lambda: "/app/share/scrcpy/scrcpy-server")
    received, attempts, procs = [], [], []
    server_side = {}

    def connect(address, timeout=2):
        attempts.append(address)
        ours, theirs = socket.socketpair()
        if len(attempts) == 1:
            theirs.close()                       # forward open, server not listening yet: EOF
            return ours
        theirs.sendall(b"\x00" + b"SM-G981U".ljust(64, b"\x00"))   # dummy byte + device name
        server_side["sock"] = theirs
        return ours

    def popen(argv, **kwargs):
        procs.append(FakeProc(argv))
        return procs[-1]

    adb = FakeAdb()
    session = screencontrol.ScreenSession(adb, "1.2.3.4:5555", popen=popen, connect=connect)
    assert session.set_display_power(False) is False
    assert session.set_display_power(True) is True
    server_side["sock"].settimeout(2)
    data = b""
    while len(data) < 4:
        data += server_side["sock"].recv(16)
    assert data == b"\x0a\x00\x0a\x01"
    assert len(attempts) == 2 and attempts[-1] == ("127.0.0.1", 40123)
    cmd = procs[0].argv[-1]
    assert procs[0].argv[:4] == ["adb", "-s", "1.2.3.4:5555", "shell"]
    assert "video=false" in cmd and "audio=false" in cmd and "control=true" in cmd and "Server 5.0" in cmd
    session.close()
    assert procs[0].terminated and ("forward_remove", 40123) in adb.calls
    assert session.set_display_power(False) is False      # closed sessions do nothing

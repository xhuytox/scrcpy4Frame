from scrcpy4frame.adb import ConnectResult
from scrcpy4frame.handoff import (WD_CONFIRM_S, HandoffResult, device_address, network_key, try_tcpip,
                                  try_wireless_debugging)
from scrcpy4frame.netinfo import NetInfo

NET = NetInfo(ipv4="192.168.1.5", prefix=24, ssid="Home")


class FakeAdb:
    def __init__(self, addrs=(), wd=(False,), port=0, connect_ok=True, tcpip_ok=True, raise_on="", locked=False):
        self.addrs, self.wd, self.port, self.locked = list(addrs), list(wd), port, locked
        self.connect_ok, self.tcpip_ok, self.raise_on = connect_ok, tcpip_ok, raise_on
        self.connected: set[str] = set()
        self.calls = []

    def _check(self, name):
        if name == self.raise_on:
            raise OSError("device 'R5CT' not found")

    def ipv4_addrs(self, serial):
        self._check("ipv4_addrs")
        return self.addrs

    def wireless_debugging_enabled(self, serial):
        self._check("wireless_debugging_enabled")
        return self.wd.pop(0) if len(self.wd) > 1 else self.wd[0]

    def set_wireless_debugging(self, serial, on):
        self.calls.append(("set_wd", on))

    def keyguard_showing(self, serial):
        return self.locked

    def wireless_debugging_port(self, serial):
        return self.port

    def connect(self, host, port):
        self.calls.append(("connect", host, port))
        if self.connect_ok:
            self.connected.add(f"{host}:{port}")
        return ConnectResult(self.connect_ok, "" if self.connect_ok else "auth", "")

    def wait_for_device(self, serial, timeout=10):
        return serial in self.connected

    def tcpip(self, serial, port=5555):
        self.calls.append(("tcpip", serial, port))
        return self.tcpip_ok


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_network_key():
    assert network_key(NET) == "Home"
    assert network_key(NetInfo(ipv4="10.0.0.7", prefix=16)) == "subnet:10.0.0.0/16"
    assert network_key(NetInfo()) == ""


def test_device_address_only_on_the_frames_network():
    adb = FakeAdb(addrs=[("10.12.0.5", 30), ("192.168.1.23", 24)])
    assert device_address(adb, "R5CT", NET) == "192.168.1.23"
    assert device_address(FakeAdb(addrs=[("10.12.0.5", 30)]), "R5CT", NET) == ""      # mobile data only
    assert device_address(FakeAdb(addrs=[("192.168.1.23", 24)]), "R5CT", NetInfo()) == ""
    assert device_address(FakeAdb(raise_on="ipv4_addrs"), "R5CT", NET) == ""


def test_wireless_debugging_waits_for_the_network_prompt():
    adb, clock = FakeAdb(wd=[False, False, False, True], port=37099), FakeClock()
    result = try_wireless_debugging(adb, "R5CT", "192.168.1.23", sleep=clock.sleep, clock=clock)
    assert result == HandoffResult(True, "192.168.1.23", 37099, enabled_wd=True)
    assert ("set_wd", True) in adb.calls and ("connect", "192.168.1.23", 37099) in adb.calls


def test_wireless_debugging_gives_up_after_the_wait():
    adb, clock = FakeAdb(wd=[False], port=0), FakeClock()
    assert not try_wireless_debugging(adb, "R5CT", "192.168.1.23", sleep=clock.sleep, clock=clock).ok
    assert clock.t >= WD_CONFIRM_S


def test_refused_connect_turns_it_back_off_only_if_we_turned_it_on():
    adb, clock = FakeAdb(wd=[False, True], port=37099, connect_ok=False), FakeClock()
    assert not try_wireless_debugging(adb, "R5CT", "192.168.1.23", sleep=clock.sleep, clock=clock).ok
    assert adb.calls[-1] == ("set_wd", False)
    already_on = FakeAdb(wd=[True], port=37099, connect_ok=False)
    try_wireless_debugging(already_on, "R5CT", "192.168.1.23", sleep=clock.sleep, clock=clock)
    assert not any(c[0] == "set_wd" for c in already_on.calls)


def test_pulled_cable_returns_failure():
    adb = FakeAdb(raise_on="wireless_debugging_enabled")
    assert try_wireless_debugging(adb, "R5CT", "192.168.1.23", sleep=lambda s: None) == HandoffResult(False)


def test_tcpip_connects_to_port_5555():
    adb = FakeAdb(tcpip_ok=True)
    assert try_tcpip(adb, "R5CT", "192.168.1.23", sleep=lambda s: None) == HandoffResult(True, "192.168.1.23", 5555)
    assert not try_tcpip(FakeAdb(tcpip_ok=False), "R5CT", "192.168.1.23", sleep=lambda s: None).ok


def test_port_from_mdns_when_the_property_is_empty():           # Samsung doesn't set it
    adb, clock = FakeAdb(wd=[True], port=0), FakeClock()
    result = try_wireless_debugging(adb, "R5CT", "192.168.1.23", find_port=lambda: 46285,
                                    sleep=clock.sleep, clock=clock)
    assert result.ok and result.port == 46285


def test_locked_phone_is_not_asked():
    adb = FakeAdb(wd=[False], port=37099, locked=True)
    assert try_wireless_debugging(adb, "R5CT", "192.168.1.23", sleep=lambda s: None) == HandoffResult(False, locked=True)
    assert ("set_wd", True) not in adb.calls

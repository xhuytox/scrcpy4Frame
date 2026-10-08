import pytest

from scrcpy4frame import wizard as W
from scrcpy4frame.adb import DeviceEntry, DeviceInfo, PairResult
from scrcpy4frame.companion import InstallReport
from scrcpy4frame.devices import SavedDevice
from scrcpy4frame.discovery import Service

PIXEL = DeviceInfo(serialno="1A2B3C4D", model="Pixel 8", manufacturer="Google", release="15", sdk=35)
XIAOMI = DeviceInfo(serialno="X1", model="Redmi Note 13", manufacturer="Xiaomi", release="14", sdk=34)
OLD = DeviceInfo(serialno="OLD1", model="Galaxy S9", manufacturer="samsung", release="10", sdk=29)

CONNECT = Service("connect", "adb-1A2B3C4D-vWgJpq", "192.168.1.23", 41235)
PAIRING = Service("pairing", "adb-1A2B3C4D-vWgJpq", "192.168.1.23", 37099)
OTHER = Service("connect", "adb-ZZZ-abcdef", "192.168.1.50", 40000)


class FakeActions:
    def __init__(self):
        self.calls = []
        self.saved = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
        return record

    def save_device(self, device):
        self.saved.append(device)

    def last(self, name):
        return [args for n, args in self.calls if n == name][-1]

    def names(self):
        return [n for n, _ in self.calls]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def setup():
    actions, clock = FakeActions(), Clock()
    wiz = W.Wizard(actions, companion_available=True, clock=clock)
    wiz.start("new")
    return wiz, actions, clock


def to_pair_code(wiz):
    wiz.choose_brand("pixel")
    assert wiz.step == W.DEVOPTS
    wiz.next()
    assert wiz.step == W.WIRELESS_ON and wiz.status == "waiting"
    wiz.services_changed([CONNECT])
    assert wiz.step == W.PAIR_OPEN and wiz.ctx.target_host == "192.168.1.23"
    wiz.services_changed([CONNECT, PAIRING])
    assert wiz.step == W.PAIR_CODE and wiz.view()["pairPort"] == 37099


def test_happy_path_wireless_with_companion_and_tile(setup):
    wiz, actions, _ = setup
    to_pair_code(wiz)
    wiz.submit_code("12345")
    assert wiz.status == "error" and "6 digits" in wiz.message
    wiz.submit_code("123456")
    assert actions.last("pair") == ("192.168.1.23", 37099, "123456") and wiz.busy
    wiz.pair_done(PairResult(True, guid="adb-1A2B3C4D-vWgJpq"))
    assert wiz.step == W.CONNECTING
    assert actions.last("connect") == ("192.168.1.23", 41235)
    wiz.services_changed([CONNECT])          # pairing dialog closed after success: no regression
    assert wiz.step == W.CONNECTING
    wiz.connect_done(True, "192.168.1.23:41235", PIXEL)
    assert wiz.step == W.COMPANION
    assert actions.saved[-1].id == "adb-1A2B3C4D-vWgJpq" and actions.saved[-1].last_port == 41235
    assert not wiz.view()["canBack"]
    wiz.accept_companion()
    assert actions.last("install_companion") == ("192.168.1.23:41235", 35)
    wiz.companion_done(InstallReport(ok=True, steps={"install": "ok", "reconnect": "ok",
                                                     "notifications": "ok", "battery": "ok"}))
    assert wiz.status == "ok" and actions.saved[-1].companion
    wiz.next()
    assert wiz.step == W.TILE
    wiz.add_tile()
    wiz.tile_done(True)
    assert wiz.status == "ok" and actions.saved[-1].qs_tile
    wiz.next()
    assert wiz.step == W.TEST
    wiz.start_mirror()
    assert wiz.step == W.DONE and actions.last("start_mirror") == ("adb-1A2B3C4D-vWgJpq",)


def test_wrong_code_then_dialog_closed(setup):
    wiz, actions, _ = setup
    to_pair_code(wiz)
    wiz.submit_code("000000")
    wiz.pair_done(PairResult(False, error="wrong_code"))
    assert wiz.step == W.PAIR_CODE and "didn't match" in wiz.message
    wiz.services_changed([CONNECT])          # user closed the dialog
    assert wiz.step == W.PAIR_OPEN and "closed" in wiz.message
    wiz.back()                               # automatic regressions aren't in history
    assert wiz.step == W.WIRELESS_ON


def test_pairing_dialog_already_open_skips_ahead(setup):
    wiz, _, _ = setup
    wiz.choose_brand("samsung")
    wiz.next()
    wiz.services_changed([CONNECT, PAIRING])
    assert wiz.step == W.PAIR_CODE


def test_multiple_phones_pick_and_not_mine(setup):
    wiz, _, _ = setup
    wiz.choose_brand("pixel")
    wiz.next()
    wiz.services_changed([CONNECT, OTHER])
    assert wiz.step == W.PICK_PHONE and len(wiz.view()["candidates"]) == 2
    wiz.pick("192.168.1.50")
    assert wiz.step == W.PAIR_OPEN and wiz.ctx.target_host == "192.168.1.50"
    wiz.not_mine()
    assert wiz.step == W.PAIR_OPEN and wiz.ctx.target_host == "192.168.1.23"


def test_help_appears_after_timeout(setup):
    wiz, _, clock = setup
    wiz.choose_brand("pixel")
    wiz.next()
    clock.t += W.HELP_AFTER_S + 1
    wiz.tick()
    assert wiz.help


def test_connect_waits_for_service_then_times_out(setup):
    wiz, actions, clock = setup
    wiz.choose_brand("pixel")
    wiz.next()
    wiz.services_changed([PAIRING])          # only the pairing service is visible
    wiz.submit_code("123456")
    wiz.pair_done(PairResult(True, guid="adb-1A2B3C4D-vWgJpq"))
    assert wiz.step == W.CONNECTING and "connect" not in actions.names()
    clock.t += W.CONNECT_WAIT_S + 1
    wiz.tick()
    assert wiz.status == "error"
    wiz.retry()
    wiz.services_changed([CONNECT])
    assert actions.last("connect") == ("192.168.1.23", 41235)


def test_auth_failure_goes_back_to_pairing(setup):
    wiz, _, _ = setup
    to_pair_code(wiz)
    wiz.submit_code("123456")
    wiz.pair_done(PairResult(True, guid="adb-1A2B3C4D-vWgJpq"))
    wiz.connect_done(False, error="auth")
    assert wiz.step == W.PAIR_OPEN


def test_xiaomi_input_check_loop(setup):
    wiz, actions, clock = setup
    to_pair_code(wiz)
    wiz.submit_code("123456")
    wiz.pair_done(PairResult(True, guid="adb-X1-abcdef"))
    wiz.connect_done(True, "192.168.1.23:41235", XIAOMI)
    assert wiz.step == W.OEM_INPUT and wiz.ctx.brand == "xiaomi"
    wiz.input_checked(blocked=True)
    assert wiz.status == "waiting"
    checks = actions.names().count("check_input")
    clock.t += W.INPUT_RECHECK_S + 1
    wiz.tick()
    assert actions.names().count("check_input") == checks + 1
    wiz.input_checked(blocked=False)
    assert wiz.step == W.COMPANION


def test_companion_failure_can_skip(setup):
    wiz, _, _ = setup
    to_pair_code(wiz)
    wiz.submit_code("123456")
    wiz.pair_done(PairResult(True, guid="adb-1A2B3C4D-vWgJpq"))
    wiz.connect_done(True, "192.168.1.23:41235", PIXEL)
    wiz.accept_companion()
    wiz.companion_done(InstallReport(ok=False, steps={"install": "failed"}, error="blocked"))
    assert wiz.status == "error" and wiz.message == "blocked"
    wiz.skip()
    assert wiz.step == W.TILE


def test_tile_fallback_to_manual(setup):
    wiz, actions, _ = setup
    to_pair_code(wiz)
    wiz.submit_code("123456")
    wiz.pair_done(PairResult(True, guid="adb-1A2B3C4D-vWgJpq"))
    wiz.connect_done(True, "192.168.1.23:41235", PIXEL)
    wiz.decline_companion()
    wiz.add_tile()
    wiz.tile_done(False)
    assert wiz.step == W.TILE_MANUAL
    wiz.check_again()
    wiz.tile_done(False)
    assert wiz.status == "error"
    wiz.next()
    assert wiz.step == W.TEST


def test_manual_entry(setup):
    wiz, actions, _ = setup
    wiz.choose_brand("pixel")
    wiz.next()
    wiz.use_manual()
    wiz.manual_submit("192.168.1", 41235, 37099, "123456")
    assert wiz.status == "error"
    wiz.manual_submit("192.168.1.23", 41235, 37099, "123456")
    assert actions.last("pair") == ("192.168.1.23", 37099, "123456")
    wiz.pair_done(PairResult(True, guid="adb-1A2B3C4D-vWgJpq"))
    assert actions.last("connect") == ("192.168.1.23", 41235)


def test_usb_path_for_android_10(setup):
    wiz, actions, _ = setup
    wiz.start("usb")
    wiz.choose_brand("samsung")
    assert wiz.step == W.USB_SETUP
    wiz.next()
    assert wiz.step == W.USB_PLUG and actions.last("usb_poll") == (True,)
    wiz.usb_devices([DeviceEntry("R5CT", "no permissions")])
    assert wiz.step == W.USB_PERMS
    wiz.usb_devices([DeviceEntry("R5CT", "unauthorized")])
    assert wiz.step == W.USB_AUTH
    wiz.usb_devices([DeviceEntry("R5CT", "device")])
    assert wiz.step == W.USB_TCPIP and actions.last("usb_tcpip") == ("R5CT",)
    wiz.tcpip_done(False, error="no_wifi")
    assert wiz.status == "error" and "Wi-Fi" in wiz.message
    wiz.retry()
    wiz.tcpip_done(True, "192.168.1.40:5555", OLD)
    assert wiz.step == W.USB_UNPLUG and actions.saved[-1].method == "tcpip"
    wiz.usb_devices([DeviceEntry("R5CT", "device"), DeviceEntry("192.168.1.40:5555", "device")])
    assert wiz.step == W.USB_UNPLUG
    wiz.usb_devices([DeviceEntry("192.168.1.40:5555", "device")])
    assert actions.last("usb_poll") == (False,)
    assert wiz.step == W.COMPANION              # Android 10 still gets notifications
    wiz.decline_companion()
    assert wiz.step == W.TEST                   # no Quick Settings tile on the cable path


def test_repair_mode_starts_at_pair_open():
    actions = FakeActions()
    wiz = W.Wizard(actions)
    device = SavedDevice(id="adb-1A2B3C4D-vWgJpq", manufacturer="Google", last_host="192.168.1.23")
    wiz.start("repair", device=device, services=[CONNECT])
    assert wiz.step == W.PAIR_OPEN and wiz.ctx.brand == "pixel"
    wiz.services_changed([CONNECT, PAIRING])
    assert wiz.step == W.PAIR_CODE


def test_wireless_already_on_skips_to_pair_open(setup):
    wiz, _, _ = setup
    wiz.services_changed([CONNECT])
    wiz.choose_brand("pixel")
    wiz.next()
    assert wiz.step == W.PAIR_OPEN and wiz.view()["targetFound"]


def test_repair_target_found_only_when_advertising():
    wiz = W.Wizard(FakeActions())
    device = SavedDevice(id="adb-1A2B3C4D-vWgJpq", manufacturer="Google", last_host="192.168.1.23")
    wiz.start("repair", device=device, services=[])
    assert wiz.step == W.PAIR_OPEN and wiz.ctx.target_host == "192.168.1.23"
    assert not wiz.view()["targetFound"]
    wiz.services_changed([OTHER])
    assert not wiz.view()["targetFound"]
    wiz.services_changed([CONNECT])
    assert wiz.view()["targetFound"]


def test_no_companion_bundle_skips_offer():
    actions = FakeActions()
    wiz = W.Wizard(actions, companion_available=False)
    wiz.start("new")
    to_pair_code(wiz)
    wiz.submit_code("123456")
    wiz.pair_done(PairResult(True, guid="adb-1A2B3C4D-vWgJpq"))
    wiz.connect_done(True, "192.168.1.23:41235", PIXEL)
    assert wiz.step == W.TILE


def test_progress_view(setup):
    wiz, _, _ = setup
    view = wiz.view()
    assert view["progress"] == 0 and view["progressTotal"] == len(W.WIRELESS_ORDER)
    to_pair_code(wiz)
    assert wiz.view()["progress"] == W.WIRELESS_ORDER.index(W.PAIR_CODE)

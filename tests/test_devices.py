import json

from scrcpy4frame.devices import NONE, SETUP_WIRED, SETUP_WIRELESS, TCPIP, WIRELESS, DeviceStore, SavedDevice


def test_old_files_migrate(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [
        {"id": "adb-AAA-111111", "method": "wireless", "serialno": "AAA", "last_host": "192.168.1.2"},
        {"id": "tcpip-OLD1", "method": "tcpip", "serialno": "OLD1", "last_host": "192.168.1.40"},
    ]}))
    store = DeviceStore(path)
    wireless, tcpip = store.get("adb-AAA-111111"), store.get("tcpip-OLD1")
    assert wireless.setup == SETUP_WIRELESS and wireless.wireless_guid == "adb-AAA-111111" and wireless.wifi_ok
    assert tcpip.setup == SETUP_WIRED and tcpip.method == TCPIP and tcpip.wireless_guid == ""


def test_duplicates_merge_at_load_keeping_the_oldest_id(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"devices": [
        {"id": "adb-AAA-111111", "serialno": "AAA", "added_at": 1, "last_connected_at": 5, "companion": True},
        {"id": "adb-AAA-222222", "serialno": "AAA", "added_at": 2, "last_connected_at": 9, "last_host": "192.168.1.9"},
    ]}))
    store = DeviceStore(path)
    assert [d.id for d in store.all()] == ["adb-AAA-111111"]
    kept = store.get("adb-AAA-111111")
    assert kept.companion and kept.last_host == "192.168.1.9"
    assert [d.id for d in DeviceStore(path).all()] == ["adb-AAA-111111"]     # saved


def test_wired_setup_of_a_saved_wireless_phone_keeps_one_entry(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    store.upsert(SavedDevice(id="adb-AAA-111111", serialno="AAA", wireless_guid="adb-AAA-111111",
                             last_host="192.168.1.2", last_port=41235, companion=True))
    stored = store.upsert(SavedDevice(id="usb-AAA", setup=SETUP_WIRED, method=NONE, serialno="AAA", usb_serial="AAA"))
    assert stored.id == "adb-AAA-111111" and [d.id for d in store.all()] == ["adb-AAA-111111"]
    assert stored.method == WIRELESS and stored.wireless_guid == "adb-AAA-111111"
    assert stored.usb_serial == "AAA" and stored.setup == SETUP_WIRED and stored.companion
    assert stored.last_host == "192.168.1.2"


def test_matches_guid_by_serial_without_a_stored_guid():
    device = SavedDevice(id="usb-AAA", setup=SETUP_WIRED, method=WIRELESS, serialno="AAA")
    assert device.matches_guid("adb-AAA-Zz9yXx")
    assert not device.matches_guid("adb-BBB-Zz9yXx")
    assert not SavedDevice(id="usb-AAA", method=NONE, serialno="AAA").matches_guid("adb-AAA-Zz9yXx")


def test_asked_networks_union_on_merge(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    store.upsert(SavedDevice(id="usb-AAA", serialno="AAA", wd_asked_networks=["Home"]))
    stored = store.upsert(SavedDevice(id="adb-AAA-1", serialno="AAA", wd_asked_networks=["Home", "Office"]))
    assert stored.wd_asked_networks == ["Home", "Office"]


def test_matches_guid_by_a_pairing_id_without_a_stored_guid():
    assert SavedDevice(id="adb-AAA-111111").matches_guid("adb-AAA-222222")


def test_setting_up_the_same_wired_phone_again_keeps_what_was_known(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    store.upsert(SavedDevice(id="usb-AAA", setup=SETUP_WIRED, method=WIRELESS, serialno="AAA", nickname="Mine",
                             companion=True, app_enabled_wireless_debugging=True, wd_asked_networks=["Home"],
                             added_at=1))
    stored = store.add(SavedDevice(id="usb-AAA", setup=SETUP_WIRED, method=NONE, serialno="AAA", usb_serial="AAA"))
    assert stored.method == WIRELESS and stored.nickname == "Mine" and stored.companion
    assert stored.app_enabled_wireless_debugging and stored.wd_asked_networks == ["Home"] and stored.added_at == 1
    assert stored.usb_serial == "AAA"

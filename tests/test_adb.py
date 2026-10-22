from scrcpy4frame.adb import (Adb, CmdResult, DeviceInfo, is_ignored_serial, is_tcp_serial,
                              make_env, parse_connect, parse_devices, parse_getprop_lines,
                              parse_ipv4_addrs, parse_pair, parse_tcpip, parse_wlan_ip,
                              server_argv)


def test_parse_devices_states_props_and_filters_local_instances():
    out = """List of devices attached
192.168.1.23:41235     device product:husky model:Pixel_8_Pro device:husky transport_id:4
R5CT123ABC             unauthorized usb:1-1 transport_id:5
0123456789ABCDEF       no permissions (missing udev rules? user is in the plugdev group); see [http://developer.android.com/tools/device.html] usb:1-2 transport_id:6
emulator-5554          device product:waydroid model:WayDroid transport_id:1
127.0.0.1:5555         device transport_id:2
adb-R5CT1234ABC-AbCdEf._adb-tls-connect._tcp device product:x1qsqx model:SM_G981U device:x1q transport_id:3
"""
    entries = parse_devices(out)
    assert [(e.serial, e.state) for e in entries] == [
        ("192.168.1.23:41235", "device"),
        ("R5CT123ABC", "unauthorized"),
        ("0123456789ABCDEF", "no permissions"),
    ]
    assert entries[0].props["model"] == "Pixel_8_Pro"
    assert entries[0].is_tcp and not entries[1].is_tcp


def test_serial_kinds():
    assert is_tcp_serial("10.0.0.5:5555")
    assert not is_tcp_serial("R5CT123ABC")
    assert is_ignored_serial("emulator-5556")
    assert is_ignored_serial("127.0.0.1:5557")
    assert is_ignored_serial("adb-R5CT1234ABC-AbCdEf._adb-tls-connect._tcp")
    assert not is_ignored_serial("192.168.1.2:5555")


def test_parse_pair_success_extracts_guid():
    result = parse_pair(CmdResult(0, "Successfully paired to 192.168.1.23:37099 [guid=adb-1A2B3C4D5E6F-vWgJpq]\n", ""))
    assert result.ok and result.guid == "adb-1A2B3C4D5E6F-vWgJpq"


def test_parse_pair_failures():
    assert parse_pair(CmdResult(1, "", "error: Failed: Wrong password or connection was dropped.")).error == "wrong_code"
    assert parse_pair(CmdResult(1, "", "error: Failed: Unable to start pairing client.")).error == "unreachable"
    assert parse_pair(CmdResult(124, "", "timed out after 25s")).error == "unreachable"
    assert parse_pair(CmdResult(1, "", "something odd")).error == "other"


def test_parse_connect_exit_code_is_ignored():
    assert parse_connect(CmdResult(0, "connected to 192.168.1.23:41235\n", "")).ok
    assert parse_connect(CmdResult(0, "already connected to 192.168.1.23:41235\n", "")).ok
    failed = parse_connect(CmdResult(0, "failed to connect to '192.168.1.23:41235': Connection refused\n", ""))
    assert not failed.ok and failed.error == "refused"
    auth = parse_connect(CmdResult(0, "failed to authenticate to 192.168.1.23:41235\n", ""))
    assert not auth.ok and auth.error == "auth"


def test_parse_wlan_ip_and_tcpip():
    out = "30: wlan0    inet 192.168.1.23/24 brd 192.168.1.255 scope global wlan0\\       valid_lft forever"
    assert parse_wlan_ip(out) == ("192.168.1.23", 24)
    assert parse_wlan_ip("1: lo    inet 127.0.0.1/8 scope host lo") is None
    assert parse_tcpip(CmdResult(0, "restarting in TCP mode port: 5555\n", ""))


def test_getprop_to_device_info():
    props = parse_getprop_lines("[ro.product.model]: [Pixel 8]\n[ro.product.manufacturer]: [Google]\n"
                                "[ro.build.version.sdk]: [35]\n[ro.build.version.release]: [15]\n"
                                "[ro.serialno]: [1A2B3C4D]\n")
    info = DeviceInfo.from_props(props)
    assert (info.model, info.manufacturer, info.sdk, info.release, info.serialno) == (
        "Pixel 8", "Google", 35, "15", "1A2B3C4D")


def test_env_isolates_adb(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRCPY4FRAME_ADB_HOME", str(tmp_path))
    env = make_env(game_mode=True)
    assert env["ANDROID_ADB_SERVER_PORT"] == "5038"
    assert env["ADB_EMU"] == "0"
    assert env["HOME"] == str(tmp_path)
    assert env["LOGNAME"] == "scrcpy4Frame" and env["HOSTNAME"] == "SteamFrame"
    assert env["SDL_VIDEODRIVER"] == "x11"


def test_client_builds_argv_with_serial():
    calls = []

    def runner(args, env, timeout):
        calls.append(list(args))
        return CmdResult(0, "", "")

    adb = Adb("/app/bin/adb", env={}, runner=runner)
    adb.shell("1.2.3.4:5555", "settings get global development_settings_enabled")
    assert calls[-1] == ["/app/bin/adb", "-s", "1.2.3.4:5555", "shell",
                         "settings get global development_settings_enabled"]


def test_server_argv_can_stay_off_usb(monkeypatch):
    assert server_argv("adb") == ["adb", "server", "nodaemon"]
    monkeypatch.setenv("SCRCPY4FRAME_NO_USB", "1")
    assert server_argv("adb")[:3] == ["adb", "--one-device", "scrcpy4frame-no-usb"]


def test_parse_ipv4_addrs_lists_every_interface():
    out = ("1: lo    inet 127.0.0.1/8 scope host lo\n"
           "12: rmnet_data0    inet 10.12.0.5/30 scope global rmnet_data0\n"
           "30: wlan0    inet 192.168.1.23/24 brd 192.168.1.255 scope global wlan0\n")
    assert parse_ipv4_addrs(out) == [("10.12.0.5", 30), ("192.168.1.23", 24)]
    assert parse_ipv4_addrs("") == []


def test_wireless_debugging_commands():
    calls, replies = [], iter(["1\n", "0\n", "null\n", "37099\n", "\n"])

    def runner(args, env, timeout):
        calls.append(args[-1])
        return CmdResult(0, next(replies, ""), "")

    adb = Adb("/app/bin/adb", env={}, runner=runner)
    assert adb.wireless_debugging_enabled("R5CT") is True
    assert adb.wireless_debugging_enabled("R5CT") is False
    assert adb.wireless_debugging_enabled("R5CT") is None
    assert adb.wireless_debugging_port("R5CT") == 37099
    assert adb.wireless_debugging_port("R5CT") == 0
    adb.set_wireless_debugging("R5CT", True)
    assert calls[-1] == "settings put global adb_wifi_enabled 1"
    adb.set_wireless_debugging("R5CT", False)
    assert calls[-1] == "settings put global adb_wifi_enabled 0"
    assert calls[3] == "getprop service.adb.tls.port"


def test_keyguard_showing():
    replies = iter(["    isKeyguardShowing=true\n", "    mDreamingLockscreen=true\n",
                    "    isKeyguardShowing=false mDreamingLockscreen=false\n"])
    adb = Adb("/app/bin/adb", env={}, runner=lambda args, env, timeout: CmdResult(0, next(replies), ""))
    assert adb.keyguard_showing("R5CT") and adb.keyguard_showing("R5CT")
    assert not adb.keyguard_showing("R5CT")


def test_responds_only_when_the_device_answers():
    replies = iter([CmdResult(0, "ok\n", ""), CmdResult(124, "", "timed out after 5s"),
                    CmdResult(1, "", "error: device offline")])
    calls = []
    adb = Adb("/app/bin/adb", env={}, runner=lambda args, env, timeout: calls.append((args[1:], timeout))
              or next(replies))
    assert adb.responds("192.168.1.23:5555", timeout=4)
    assert not adb.responds("192.168.1.23:5555") and not adb.responds("192.168.1.23:5555")
    assert calls[0] == (["-s", "192.168.1.23:5555", "shell", "echo ok"], 4)


def test_a_reused_server_is_left_running(monkeypatch):
    # `cli` over SSH reuses the app's server; stopping must not kill it under the running app.
    from scrcpy4frame import adb as adb_mod

    calls = []
    adb = Adb("/app/bin/adb", env={}, runner=lambda args, env, timeout: calls.append(args[1:]) or CmdResult(0, "", ""))
    monkeypatch.setattr(adb_mod, "_port_open", lambda port: True)
    server = adb_mod.AdbServer(adb)
    assert server.start()
    server.stop()
    assert ["kill-server"] not in calls

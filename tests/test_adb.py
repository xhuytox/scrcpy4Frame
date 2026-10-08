from scrcpy4frame.adb import (Adb, CmdResult, DeviceInfo, is_ignored_serial, is_tcp_serial,
                              make_env, parse_connect, parse_devices, parse_getprop_lines,
                              parse_pair, parse_tcpip, parse_wlan_ip, server_argv)


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

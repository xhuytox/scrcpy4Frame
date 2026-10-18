import json

from scrcpy4frame import companion
from scrcpy4frame.adb import CmdResult
from scrcpy4frame.devices import DeviceStore, SavedDevice
from scrcpy4frame.discovery import instance_name, same_phone, serial_from_guid
from scrcpy4frame.mirror import build_args, classify
from scrcpy4frame.netinfo import same_subnet
from scrcpy4frame.notify.router import Router
from scrcpy4frame.protocol import LineBuffer, encode
from scrcpy4frame.settings import Settings, SettingsStore


def test_guid_parsing():
    assert instance_name("adb-1A2B3C4D-vWgJpq._adb-tls-connect._tcp.local.",
                         "_adb-tls-connect._tcp.local.") == "adb-1A2B3C4D-vWgJpq"
    assert serial_from_guid("adb-1A2B3C4D-vWgJpq") == "1A2B3C4D"
    assert same_phone("adb-1A2B3C4D-vWgJpq", "adb-1A2B3C4D-Zz9yXx")
    assert not same_phone("adb-AAA-vWgJpq", "adb-BBB-vWgJpq")


def test_same_subnet():
    assert same_subnet("192.168.1.10", "192.168.1.200", 24)
    assert not same_subnet("192.168.1.10", "192.168.2.20", 24)


def test_mirror_args_defaults():
    args = build_args("scrcpy", "1.2.3.4:5555", "Pixel 8", Settings())
    assert "--serial=1.2.3.4:5555" in args
    assert "--fullscreen" not in args          # phone-shaped window: the app stays reachable beside it
    assert "--shortcut-mod=lalt,lsuper" in args and "--keep-active" in args
    assert "--no-audio" in args                # audio is cast separately, on request
    assert "--max-size=1280" in build_args("scrcpy", "s", "t", Settings(), max_size_override=1280)


def test_mirror_classify():
    assert classify(0, "").kind == "ok"
    assert classify(2, "").kind == "disconnected"
    assert classify(1, "java.lang.SecurityException: ... INJECT_EVENTS permission").kind == "input_blocked"
    encoder = classify(1, "[server] ERROR: Encoding error: android.media.MediaCodec$CodecException")
    assert encoder.kind == "encoder" and encoder.retry_smaller
    assert classify(1, "INFO: Using video encoder 'c2.qti.avc.encoder'\nERROR: Server connection failed").kind == "server"
    assert classify(1, "ERROR: Could not find any ADB device").kind == "no_device"


def test_install_parsing_and_hints():
    assert companion.parse_install("Performing Streamed Install\nSuccess\n") == (True, "")
    ok, code = companion.parse_install("adb: failed to install x.apk: Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]")
    assert not ok and code == "INSTALL_FAILED_USER_RESTRICTED" and code in companion.INSTALL_HINTS


def test_listener_settings():
    assert companion.listener_enabled("com.foo/.Bar:" + companion.LISTENER_FULL)
    assert not companion.listener_enabled("null")
    assert companion.merged_listeners("null") == companion.LISTENER_FULL
    assert companion.merged_listeners("com.foo/.Bar") == "com.foo/.Bar:" + companion.LISTENER_FULL
    dumpsys = "      android.permission.WRITE_SECURE_SETTINGS: granted=true\n"
    assert companion.permission_granted(dumpsys, companion.WRITE_SECURE_SETTINGS)


def test_install_and_grant_flow_with_fake_adb(tmp_path):
    class FakeAdb:
        def __init__(self):
            self.listeners = "null"
            self.shell_calls = []

        def install(self, serial, apk):
            return CmdResult(0, "Success\n", "")

        def uninstall(self, serial, pkg):
            return CmdResult(0, "Success\n", "")

        def shell(self, serial, cmd, timeout=15):
            self.shell_calls.append(cmd)
            if cmd.startswith("dumpsys package"):
                return CmdResult(0, f"{companion.WRITE_SECURE_SETTINGS}: granted=true\nversionCode=1", "")
            if cmd.startswith("cmd notification allow_listener"):
                self.listeners = companion.LISTENER_FULL
            if cmd == "settings get secure enabled_notification_listeners":
                return CmdResult(0, self.listeners + "\n", "")
            if cmd == "cmd deviceidle whitelist":
                return CmdResult(0, f"user,{companion.PACKAGE},10234\n", "")
            return CmdResult(0, "", "")

    fake = FakeAdb()
    report = companion.install_and_grant(fake, "1.2.3.4:5555", tmp_path / "c.apk", sdk=34)
    assert report.ok, report
    assert report.steps == {"install": "ok", "reconnect": "ok", "notifications": "ok", "battery": "ok"}
    old = companion.install_and_grant(FakeAdb(), "s", tmp_path / "c.apk", sdk=29)
    assert old.steps["reconnect"] == "skipped"


LIVE_DUMP = """\
  Notification listeners:
    All notification listeners (2) enabled for current profiles:
      ComponentInfo{com.google.android.gms/com.google.android.gms.nearby.NotificationListener}
      ComponentInfo{%(pkg)s/%(pkg)s.NotifListener}
    Live notification listeners (%(n)d):
      ComponentInfo{com.google.android.gms/com.google.android.gms.nearby.NotificationListener} (user 0): x
%(live)s    Snoozed notification listeners (0):
  Notification assistant services:
    Live notification assistant services (1):
      ComponentInfo{%(pkg)s/%(pkg)s.Fake} (user 0): x
"""


def _dump(live: bool) -> str:
    entry = f"      ComponentInfo{{{companion.PACKAGE}/{companion.LISTENER_FULL.split('/')[1]}}} (user 0): x\n"
    return LIVE_DUMP % {"pkg": companion.PACKAGE, "n": 2 if live else 1, "live": entry if live else ""}


def test_listener_live_reads_only_the_live_listeners():
    assert companion.listener_live(_dump(live=True)) is True
    # Enabled, and listed among the assistants, but not bound as a listener.
    assert companion.listener_live(_dump(live=False)) is False
    assert companion.listener_live("Can't find service: notification\n") is None


class RestartAdb:
    def __init__(self, installed=True, live=False, grants=True, rc=0):
        self.installed, self.live, self.grants, self.rc = installed, live, grants, rc
        self.listeners = companion.LISTENER_FULL
        self.calls = []

    def shell(self, serial, cmd, timeout=15):
        self.calls.append(cmd)
        if cmd.startswith("pm list packages"):
            return CmdResult(self.rc, f"package:{companion.PACKAGE}\n" if self.installed else "", "")
        if cmd == "dumpsys notification":
            return CmdResult(0, _dump(self.live), "")
        if cmd.startswith("cmd notification disallow_listener"):
            self.listeners = "null"
        if cmd.startswith("cmd notification allow_listener") and self.grants:
            self.listeners = companion.LISTENER_FULL
        if cmd == "settings get secure enabled_notification_listeners":
            return CmdResult(0, self.listeners + "\n", "")
        return CmdResult(0, "", "")


def test_restart_listener_rebinds_only_a_listener_android_dropped():
    adb = RestartAdb()
    assert companion.restart_listener(adb, "s") == "restarted"
    toggles = [c for c in adb.calls if c.startswith("cmd notification")]
    assert toggles == [f"cmd notification disallow_listener {companion.LISTENER}",
                       f"cmd notification allow_listener {companion.LISTENER}"]
    running = RestartAdb(live=True)
    assert companion.restart_listener(running, "s") == "running"
    assert not any(c.startswith("cmd notification") for c in running.calls)
    assert companion.restart_listener(RestartAdb(grants=False), "s") == "failed"
    revoked = RestartAdb()
    revoked.listeners = "null"                        # the user turned access off: leave it off
    assert companion.restart_listener(revoked, "s") == "revoked"
    assert not any(c.startswith("cmd notification") for c in revoked.calls)
    missing = RestartAdb(installed=False)
    assert companion.restart_listener(missing, "s") == "missing"
    assert companion.restart_listener(RestartAdb(rc=1), "s") == "error"
    assert not any(c.startswith("cmd notification") for c in missing.calls)


def test_tile_detection():
    assert companion.tile_present(
        "wifi,bt,custom(com.android.settings/.development.qstile.DevelopmentTiles$WirelessDebugging)")
    assert not companion.tile_present("wifi,bt,flashlight")


def test_line_buffer_splits_and_skips_garbage():
    buf = LineBuffer()
    data = encode({"type": "hello", "protocol": 1}) + b"not json\n" + b'{"type":"posted","ke'
    assert buf.feed(data) == [{"type": "hello", "protocol": 1}]
    assert buf.feed(b'y":"k"}\n') == [{"type": "posted", "key": "k"}]
    assert buf.feed(b'[1,2]\n{"no_type":1}\n') == []


def test_router_filters_and_dedupes():
    clock = [100.0]
    router = Router(clock=lambda: clock[0])
    s = Settings()
    msg = {"type": "posted", "key": "k1", "pkg": "com.whatsapp", "appLabel": "WhatsApp",
           "title": "Alice", "text": "Hi", "when": 1}
    toast = router.posted("dev", msg, s)
    assert toast and toast.title == "Alice" and toast.text == "Hi"
    assert router.posted("dev", msg, s) is None                  # unchanged re-post stays quiet
    assert router.posted("dev", {**msg, "text": "Hi again"}, s)  # real update shows again
    assert router.posted("dev", {**msg, "key": "k2"}, s, visible_mirror_device_id="dev") is None
    s.muted_packages = ["com.whatsapp"]
    assert router.posted("dev", {**msg, "key": "k3"}, s) is None
    s.muted_packages, s.hide_content = [], True
    hidden = router.posted("dev", {**msg, "key": "k4"}, s)
    assert hidden.text == "New notification" and "Alice" not in hidden.title
    assert len(router.recent_list()) == 4
    assert router.removed("dev", "k1") and not router.removed("dev", "k1")


def test_router_forgets_a_device():
    router, s = Router(clock=lambda: 100.0), Settings()
    msg = {"type": "posted", "key": "k1", "pkg": "com.whatsapp", "title": "Alice", "text": "Hi"}
    assert router.posted("dev", msg, s) and router.posted("other", msg, s)
    router.forget_device("dev")
    assert [n["deviceId"] for n in router.recent_list()] == ["other"]
    assert router.posted("dev", msg, s)              # set up again, it's not a quiet re-post
    assert router.posted("other", msg, s) is None


def test_device_store_roundtrip_and_repair_dedupe(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    store.upsert(SavedDevice(id="adb-AAA-111111", serialno="AAA", model="Pixel 8", companion=True))
    store.upsert(SavedDevice(id="adb-AAA-222222", serialno="AAA", model="Pixel 8"))
    reloaded = DeviceStore(tmp_path / "devices.json")
    assert [d.id for d in reloaded.all()] == ["adb-AAA-222222"]
    assert reloaded.get("adb-AAA-222222").companion          # carried over from the old pairing
    assert reloaded.find_by_guid("adb-AAA-999999").id == "adb-AAA-222222"


def test_settings_store_types(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.update("bitrate_mbps", "8")
    store.update("keep_active", 0)
    assert store.value.audio_buffer_ms == 50
    store.update("audio_buffer_ms", 20.0)                   # QML numbers can arrive as floats
    assert not store.update("nope", 1)
    raw = json.loads((tmp_path / "settings.json").read_text())
    assert raw["bitrate_mbps"] == 8 and raw["keep_active"] is False and raw["audio_buffer_ms"] == 20
    assert SettingsStore(tmp_path / "settings.json").value.bitrate_mbps == 8


def test_renamed_settings_keep_their_value(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"suppress_while_mirroring": False, "audio": True}))
    value = SettingsStore(tmp_path / "settings.json").value
    assert value.suppress_while_visible is False
    assert not hasattr(value, "audio")         # the old saved audio choice is ignored


def test_hostspawn_copies_binary_and_companions_only_in_flatpak(tmp_path, monkeypatch):
    from scrcpy4frame import hostspawn, paths

    binary = tmp_path / "vr-mirror"
    binary.write_bytes(b"\x7fELF v1")
    manifest = tmp_path / "vr-mirror.vrmanifest"
    manifest.write_text("{}")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(paths, "data_dir", lambda: data)
    monkeypatch.setattr(paths, "in_flatpak", lambda: False)
    assert hostspawn.command(str(binary), [manifest]) == (str(binary), [])
    monkeypatch.setattr(paths, "in_flatpak", lambda: True)
    program, args = hostspawn.command(str(binary), [manifest, "", None])
    assert program == "flatpak-spawn" and args == ["--host", "--watch-bus", str(data / "vr-mirror")]
    assert (data / "vr-mirror").read_bytes() == b"\x7fELF v1" and (data / "vr-mirror.vrmanifest").exists()
    binary.write_bytes(b"\x7fELF v2")                      # same size, new content: refreshed
    hostspawn.command(str(binary), [])
    assert (data / "vr-mirror").read_bytes() == b"\x7fELF v2"


def test_phone_screen_stays_on_by_default():
    assert "--turn-screen-off" not in build_args("scrcpy", "s", "t", Settings())
    assert "--turn-screen-off" in build_args("scrcpy", "s", "t", Settings(turn_screen_off=True))


def test_vr_mirror_data_files_travel_together():
    from scrcpy4frame import paths

    files = paths.vr_mirror_data_files()
    assert [f.name for f in files] == ["vr-mirror.vrmanifest", "vr-mirror-actions.json",
                                       "vr-mirror-bindings-frame-controller.json"]
    assert len({f.parent for f in files}) == 1
    manifest = json.loads(files[0].read_text())["applications"][0]
    actions = json.loads(files[1].read_text())
    assert manifest["action_manifest_path"] == files[1].name
    assert actions["default_bindings"][0]["binding_url"] == files[2].name
    bindings = json.loads(files[2].read_text())["bindings"]["/actions/panel"]
    outputs = {i["output"] for s in bindings["sources"] for i in s["inputs"].values()}
    outputs |= {p["output"] for p in bindings["poses"]}
    assert outputs == {a["name"] for a in actions["actions"]}   # every action is bound, nothing else


def test_png_app_icon_is_128px_and_installed():
    import struct

    from scrcpy4frame import paths

    png = (paths.REPO_DIR / "flatpak/data/icons/128x128.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (128, 128)
    assert png[25] == 6                                       # RGBA
    manifest = (paths.REPO_DIR / f"flatpak/{paths.APP_ID}.yml").read_text()
    assert "hicolor/128x128/apps/${FLATPAK_ID}.png" in manifest

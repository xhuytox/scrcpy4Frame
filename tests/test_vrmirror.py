import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scrcpy4frame import vrmirror  # noqa: E402
from scrcpy4frame.settings import Settings  # noqa: E402


def test_version_and_scid():
    assert vrmirror.parse_scrcpy_version("scrcpy 5.0 <https://github.com/Genymobile/scrcpy>") == "5.0"
    assert vrmirror.parse_scrcpy_version("scrcpy 5.1.2 <…>") == "5.1.2"
    assert vrmirror.parse_scrcpy_version("garbage") is None
    scid = vrmirror.make_scid()
    assert len(scid) == 8 and int(scid, 16) < 2 ** 31


def test_server_command_matches_the_contract():
    s = Settings(max_size=1920, bitrate_mbps=8, max_fps=60, video_codec="h265")
    cmd = vrmirror.server_command("5.0", "0badcafe", s)
    assert cmd.startswith("CLASSPATH=/data/local/tmp/scrcpy4frame-server.jar app_process / "
                          "com.genymobile.scrcpy.Server 5.0 ")
    for param in ("scid=0badcafe", "tunnel_forward=true", "audio=false", "control=true",
                  "video_codec=h264", "max_size=1920", "video_bit_rate=8000000", "max_fps=60",
                  "keep_active=true"):
        assert param in cmd.split(), param


@pytest.fixture
def session():
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.instance() or QGuiApplication([])

    class Runner:
        def run(self, fn, *args, on_done=None, on_error=None):
            pass

    sess = vrmirror.VrMirrorSession(adb=None, serial="s", title="Phone", settings=Settings(),
                                    runner=Runner())
    ended = []
    sess.ended.connect(lambda kind, message: ended.append((kind, message)))
    return sess, ended


@pytest.mark.parametrize("event,kind", [
    ({"event": "unavailable", "reason": "VRInitError_Init_HmdNotFound"}, "vr_unavailable"),
    ({"event": "disconnected", "reason": "eof"}, "disconnected"),
])
def test_session_ends_once_with_the_right_kind(session, event, kind):
    sess, ended = session
    sess._handle(event)
    sess._handle(event)
    sess.stop()
    assert [k for k, _ in ended] == [kind]


def test_first_frame_marks_streaming(session):
    sess, ended = session
    seen = []
    sess.event.connect(lambda name, payload: seen.append(name))
    sess._handle({"event": "size", "width": 864, "height": 1920})
    sess._handle({"event": "first_frame"})
    assert sess.state == "streaming" and seen == ["size", "first_frame"] and not ended


def test_connect_message_opens_in_front_at_the_preset():
    s = Settings(panel_size="small", turn_screen_off=True)
    msg = vrmirror.connect_message(40123, "Pixel 7", s)
    assert msg == {"cmd": "connect", "host": "127.0.0.1", "port": 40123, "title": "Pixel 7",
                   "turn_screen_off": True, "panel_size_m": 0.22, "theater_size_m": 3.5,
                   "scroll_screens_per_click": 0.1488, "dashboard_button": True}
    msg = vrmirror.connect_message(40123, "Pixel 7", s, 914.2857)
    assert "pose" not in msg and msg["panel_size_m"] == 0.22 and msg["screen_long_dp"] == 914.3


@pytest.mark.parametrize("text,dp", [
    ("Physical size: 1080x2400\nPhysical density: 420\n", 2400 * 160 / 420),
    ("Physical size: 1080x2400\nOverride size: 900x2000\nPhysical density: 420\nOverride density: 480\n",
     2000 * 160 / 480),                                          # Android's display size settings win
    ("Physical size: 2560x1600\nPhysical density: 320\n", 1280.0),   # a landscape tablet
    ("Physical size: 1080x2400\n", 0.0),
    ("error: closed", 0.0),
])
def test_screen_size_in_dp(text, dp):
    assert vrmirror.parse_screen_long_dp(text) == pytest.approx(dp)


def test_audio_state_reaches_the_helper_once_per_change(session):
    sess, _ = session
    sent = []
    sess._send = sent.append
    sess.set_audio_state(False, True)               # what the helper starts with
    sess.set_audio_state(True, False)
    sess.set_audio_state(True, False)
    sess.set_audio_state(True, True)
    assert sent == [{"cmd": "audio", "on": True, "enabled": False}, {"cmd": "audio", "on": True, "enabled": True}]


def test_placement_events_are_forwarded(session):
    sess, ended = session
    seen = []
    sess.event.connect(lambda name, payload: seen.append((name, payload.get("pose"))))
    sess._handle({"event": "placement", "pose": "1,0,0,0,0,1,0,1.5,0,0,1,-0.6"})
    assert seen == [("placement", "1,0,0,0,0,1,0,1.5,0,0,1,-0.6")] and not ended


def test_dashboard_button_setting_and_command(session):
    assert vrmirror.connect_message(1, "x", Settings(dashboard_button=False))["dashboard_button"] is False
    sess, ended = session
    sent = []
    sess._send = sent.append
    sess.set_dashboard_button(False)
    sess.set_dashboard_button(True)
    assert sent == [{"cmd": "dashboard_button", "enabled": False}, {"cmd": "dashboard_button", "enabled": True}]
    assert not ended


def test_recenter_and_theater_commands(session):
    sess, ended = session
    sent = []
    sess._send = sent.append
    sess.recenter()
    sess.recenter(now=True)
    sess.set_theater(True)
    sess.set_theater(False)
    assert sent == [{"cmd": "recenter"}, {"cmd": "recenter", "now": True},
                    {"cmd": "theater", "on": True}, {"cmd": "theater", "on": False}]
    assert not ended


def test_panel_visibility_commands_and_events(session):
    sess, ended = session
    sent, seen = [], []
    sess._send = sent.append
    sess.event.connect(lambda name, payload: seen.append((name, payload.get("visible"))))
    sess.set_panel_visible(False)
    sess.set_panel_visible(True)
    assert sent == [{"cmd": "hide"}, {"cmd": "show"}]
    sess._handle({"event": "visibility", "visible": False})
    assert seen == [("visibility", False)] and not ended        # hidden is not the end of the session

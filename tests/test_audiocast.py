"""The audio-only scrcpy client, with a stand-in scrcpy script."""

import os
import stat
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scrcpy4frame import audiocast  # noqa: E402


def test_command_is_audio_only():
    assert audiocast.command("scrcpy", "1.2.3.4:5555", 50) == [
        "scrcpy", "--serial=1.2.3.4:5555", "--no-window", "--no-video", "--no-control",
        "--require-audio", "--audio-buffer=50"]


def test_needs_android_11():
    assert audiocast.supported(30) and audiocast.supported(35)
    assert not audiocast.supported(29)
    assert audiocast.supported(0)                 # unknown version: give it a try


@pytest.fixture
def app():
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication([])


def _fake_scrcpy(tmp_path, monkeypatch, body):
    script = tmp_path / "scrcpy"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SCRCPY4FRAME_SCRCPY", str(script))


def _wait_for(app, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.02)
    return predicate()


def test_goes_live_once_its_server_logs(app, tmp_path, monkeypatch):
    _fake_scrcpy(tmp_path, monkeypatch, 'sleep 0.2\necho "[server] INFO: Device: [Google] Pixel 8"\nexec sleep 30')
    cast = audiocast.AudioCast("s", 50)
    started = []
    cast.started.connect(lambda: started.append(True))
    cast.start()
    assert not cast.is_live
    assert _wait_for(app, lambda: started)
    assert cast.is_live and started == [True]
    cast.stop()


def test_goes_live_after_a_while_without_the_server_log(app, tmp_path, monkeypatch):
    monkeypatch.setattr(audiocast, "LIVE_FALLBACK_MS", 200)
    _fake_scrcpy(tmp_path, monkeypatch, "exec sleep 30")
    cast = audiocast.AudioCast("s", 50)
    cast.start()
    assert _wait_for(app, lambda: cast.is_live)
    cast.stop()


def test_reports_a_client_that_exits_by_itself(app, tmp_path, monkeypatch):
    _fake_scrcpy(tmp_path, monkeypatch, 'echo "[server] ERROR: Audio capture failed"\nexit 1')
    cast = audiocast.AudioCast("s", 50)
    ended = []
    cast.ended.connect(lambda code, reason: ended.append((code, reason)))
    cast.start()
    assert _wait_for(app, lambda: ended)
    assert ended == [(1, "Audio capture failed")]


def _record(cast):
    events = []
    cast.stopped.connect(lambda: events.append("stopped"))
    cast.ended.connect(lambda code, reason: events.append(("ended", code, reason)))
    return events


def test_stop_finishes_in_the_background(app, tmp_path, monkeypatch):
    args_file = tmp_path / "args.txt"
    _fake_scrcpy(tmp_path, monkeypatch, f'echo "$@" > {args_file}\nexec sleep 30')
    cast = audiocast.AudioCast("1.2.3.4:5555", 50)
    events = _record(cast)
    cast.start()
    assert _wait_for(app, lambda: args_file.exists() and args_file.read_text())
    cast.stop()
    assert cast.is_stopping and events == []                # returns before the client exits
    assert _wait_for(app, lambda: events)
    assert events == ["stopped"]                             # not reported as a failure
    assert "--serial=1.2.3.4:5555 --no-window --no-video --no-control" in args_file.read_text()


def test_blocking_stop_waits_for_the_client(app, tmp_path, monkeypatch):
    _fake_scrcpy(tmp_path, monkeypatch, "exec sleep 30")
    cast = audiocast.AudioCast("s", 50)
    events = _record(cast)
    cast.start()
    cast.stop(blocking=True)
    assert events == ["stopped"]


def test_a_client_that_ignores_stop_is_killed(app, tmp_path, monkeypatch):
    monkeypatch.setattr(audiocast, "STOP_KILL_MS", 300)
    started = tmp_path / "started"
    _fake_scrcpy(tmp_path, monkeypatch, f'trap "" TERM\ntouch {started}\nwhile true; do sleep 0.1; done')
    cast = audiocast.AudioCast("s", 50)
    events = _record(cast)
    cast.start()
    assert _wait_for(app, started.exists)
    cast.stop()
    assert _wait_for(app, lambda: events)
    assert events == ["stopped"]

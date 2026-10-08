import os
import wave

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scrcpy4frame.notify import sound  # noqa: E402
from scrcpy4frame.notify.sound import Chime  # noqa: E402


class FakePlayer:
    def __init__(self):
        self.plays = 0

    def play(self):
        self.plays += 1


def test_bundled_sound_is_a_short_wav():
    with wave.open(str(sound.SOUND_FILE)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 48000
        assert 0.2 < w.getnframes() / w.getframerate() < 3.0


def test_chime_plays_once_per_burst():
    now = [100.0]
    player, made = FakePlayer(), []
    chime = Chime(player_factory=lambda path: made.append(path) or player, clock=lambda: now[0])
    assert chime.play() is True
    now[0] += 0.4
    assert chime.play() is False               # same burst
    now[0] += 1.2
    assert chime.play() is True
    assert player.plays == 2 and made == [sound.SOUND_FILE]   # loaded once


def test_chime_is_silent_without_qt_multimedia_or_file(tmp_path):
    chime = Chime(player_factory=lambda path: None)
    assert chime.play() is False
    made = []
    missing = Chime(path=tmp_path / "nope.wav", player_factory=lambda path: made.append(path) or FakePlayer())
    assert missing.play() is False and made == []


@pytest.fixture
def center(tmp_path):
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.instance() or QGuiApplication([])
    from scrcpy4frame.notify.center import NotificationCenter
    from scrcpy4frame.settings import SettingsStore

    player, clock = FakePlayer(), [0.0]
    c = NotificationCenter(SettingsStore(tmp_path / "settings.json"))
    c.chime = Chime(player_factory=lambda path: player, clock=lambda: clock[0])
    toasts = []
    c.inAppToast.connect(toasts.append)
    yield c, player, clock, toasts
    c.shutdown()


def _post(c, key, pkg="com.chat"):
    c.posted("adb-SER0-abcdef", {"key": key, "pkg": pkg, "appLabel": "Chat", "title": "Hi", "text": key}, "")


def test_a_shown_notification_chimes(center):
    c, player, clock, toasts = center
    c.settings.update("notify_display", "app")
    _post(c, "k1")
    assert len(toasts) == 1 and player.plays == 1
    clock[0] += 5
    c.settings.update("muted_packages", ["com.muted"])
    _post(c, "k2", pkg="com.muted")            # not shown: no sound either
    assert len(toasts) == 1 and player.plays == 1
    c.settings.update("notify_sound", False)
    _post(c, "k3")
    assert len(toasts) == 2 and player.plays == 1


def test_desktop_popups_chime_too(center, monkeypatch):
    c, player, clock, toasts = center
    c.settings.update("notify_display", "desktop")
    shown = []
    monkeypatch.setattr(c.desktop, "available", lambda: True)
    monkeypatch.setattr(c.desktop, "show", shown.append)
    _post(c, "k1")
    assert len(shown) == 1 and not toasts and player.plays == 1

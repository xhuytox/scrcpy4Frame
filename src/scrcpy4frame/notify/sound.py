"""A short chime when a forwarded notification is shown (VR toast, desktop pop-up or in-app)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

SOUND_FILE = Path(__file__).resolve().parent.parent / "content" / "sounds" / "notification.wav"
MIN_GAP_S = 1.5        # a burst of notifications chimes once
VOLUME = 0.7


def _qt_player(path: Path):
    """A QSoundEffect for `path`, or None if Qt Multimedia is missing, as in PySide6-Essentials."""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect
    except ImportError as exc:
        log.info("notification sound unavailable: %s", exc)
        return None
    effect = QSoundEffect()
    effect.setSource(QUrl.fromLocalFile(str(path)))   # loads in the background
    effect.setVolume(VOLUME)
    return effect


class Chime:
    def __init__(self, path: Path = SOUND_FILE, player_factory=_qt_player, clock=time.monotonic):
        self.path, self.clock = path, clock
        self._factory = player_factory
        self._player = None
        self._tried = False
        self._last = float("-inf")

    def preload(self) -> None:
        """Load the sound now, so the first notification isn't silent while it loads."""
        if not self._tried:
            self._tried = True
            if self.path.is_file():
                self._player = self._factory(self.path)
            else:
                log.warning("notification sound missing: %s", self.path)

    def play(self) -> bool:
        now = self.clock()
        if now - self._last < MIN_GAP_S:
            return False
        self.preload()
        if self._player is None:
            return False
        self._last = now
        self._player.play()
        return True

"""Casts the phone's audio to the Frame with scrcpy's own client running audio-only.

scrcpy's default audio source silences the phone while it captures, so the phone plays its own
sound again once the client exits. Turned on and off from Home; never saved between launches.
"""

from __future__ import annotations

import logging
import re
from collections import deque

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from . import paths
from .adb import make_env

log = logging.getLogger(__name__)

MIN_SDK = 30                 # scrcpy's audio capture needs Android 11
LIVE_FALLBACK_MS = 5000      # still running without its server's log line: take it as casting
STOP_KILL_MS = 4000          # asked to exit but still running: kill it
_LOG_LEVEL = re.compile(r"^(\[server\] )?(VERBOSE|DEBUG|INFO|WARN|ERROR): ")


def supported(sdk: int) -> bool:
    """Whether a phone can cast its audio. An unknown version (0) gets a try."""
    return not sdk or sdk >= MIN_SDK


def command(scrcpy: str, serial: str, buffer_ms: int) -> list[str]:
    # --no-window only stops video playback, so video capture and control are turned off too.
    # --require-audio makes the client exit, rather than idle, when the phone can't capture.
    return [scrcpy, f"--serial={serial}", "--no-window", "--no-video", "--no-control",
            "--require-audio", f"--audio-buffer={buffer_ms}"]


class AudioCast(QObject):
    """The audio-only client for one phone.

    Emits `started` once the client's server is running on the phone (`is_live`), `stopped` once
    it has exited after stop() (`is_stopping` until then), and `ended(exit_code, reason)` only if
    it exits on its own: exit code -1 if it never started, and the last line it logged.
    """

    started = Signal()
    stopped = Signal()
    ended = Signal(int, str)

    def __init__(self, serial: str, buffer_ms: int, parent: QObject | None = None):
        super().__init__(parent)
        self.serial = serial
        self.buffer_ms = buffer_ms
        self.is_live = False
        self.is_stopping = False
        self._proc: QProcess | None = None
        self._log: deque[str] = deque(maxlen=20)
        self._live_fallback = QTimer(self, singleShot=True, interval=LIVE_FALLBACK_MS,
                                     timeout=self._went_live)
        self._stop_kill = QTimer(self, singleShot=True, interval=STOP_KILL_MS, timeout=self._kill)

    def start(self) -> None:
        if self._proc is not None:
            return
        scrcpy = paths.tool_path("scrcpy")
        if scrcpy is None:
            self.ended.emit(-1, "The bundled scrcpy tool is missing.")
            return
        args = command(scrcpy, self.serial, self.buffer_ms)
        env = QProcessEnvironment()
        for key, value in make_env().items():
            env.insert(key, value)
        proc = QProcess(self)
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.readyReadStandardOutput.connect(lambda: self._read(proc))
        proc.finished.connect(lambda code, _status: self._ended(proc, code))
        proc.errorOccurred.connect(lambda error: error == QProcess.ProcessError.FailedToStart
                                   and self._ended(proc, -1, proc.errorString()))
        self._proc = proc
        log.info("casting audio from %s with a %d ms buffer", self.serial, self.buffer_ms)
        proc.start()
        self._live_fallback.start()

    def stop(self, blocking: bool = False) -> None:
        """Ask the client to exit, which can take a second or so. With `blocking` (app shutdown),
        wait for it here instead of in the background."""
        proc = self._proc
        if proc is None:
            return
        if not self.is_stopping:
            self.is_stopping = True
            self._live_fallback.stop()
            log.info("stopping audio casting from %s", self.serial)
            proc.terminate()
            self._stop_kill.start()
        if blocking and not proc.waitForFinished(1500):
            proc.kill()
            proc.waitForFinished(500)

    def _kill(self) -> None:
        if self._proc is not None:
            log.warning("audio client for %s ignored the request to exit, killing it", self.serial)
            self._proc.kill()

    def _read(self, proc: QProcess) -> None:
        text = bytes(proc.readAllStandardOutput().data()).decode(errors="replace")
        for line in text.splitlines():
            if line.strip():
                self._log.append(line.strip())
                log.debug("scrcpy audio: %s", line)
            if line.startswith("[server]"):
                self._went_live()

    def _went_live(self) -> None:
        if self._proc is not None and not self.is_live and not self.is_stopping:
            self.is_live = True
            self.started.emit()

    def _ended(self, proc: QProcess, code: int, reason: str = "") -> None:
        if proc is not self._proc:
            return
        self._proc = None
        self._live_fallback.stop()
        self._stop_kill.stop()
        self._read(proc)
        if self.is_stopping:
            log.info("stopped casting audio from %s", self.serial)
            self.stopped.emit()
            return
        reason = reason or (_LOG_LEVEL.sub("", self._log[-1]) if self._log else "")
        log.warning("audio casting from %s stopped (exit %s): %s", self.serial, code, reason)
        self.ended.emit(code, reason)

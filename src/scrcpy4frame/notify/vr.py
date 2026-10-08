"""Drives the `vr-toast` OpenVR overlay helper (protocol in docs/CONTRACTS.md)."""

from __future__ import annotations

import base64
import json
import logging
import time

from PySide6.QtCore import QObject, QProcess, Signal

from .. import hostspawn, paths
from .card import render_card, rgba_bytes
from .router import Toast

log = logging.getLogger(__name__)

RETRY_AFTER_S = 60
TOAST_MS = 5000


class VrToaster(QObject):
    """States: unknown -> starting -> ready | unavailable."""

    stateChanged = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.state = "unknown"
        self._proc: QProcess | None = None
        self._buffer = b""
        self._retry_at = 0.0

    def can_try(self) -> bool:
        if paths.tool_path("vr-toast") is None:
            return False
        return self.state != "unavailable" or time.monotonic() >= self._retry_at

    def ensure_started(self) -> None:
        if self.state in ("starting", "ready") or not self.can_try():
            return
        self._launch()

    def show(self, toast: Toast) -> None:
        if self.state != "ready" or self._proc is None:
            return
        image = render_card(toast)
        self._send({"cmd": "show", "id": toast.uid, "width": image.width(), "height": image.height(),
                    "rgba": base64.b64encode(rgba_bytes(image)).decode(), "duration_ms": TOAST_MS})

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        # Detach first: during app shutdown this object may be gone before the process reports back.
        for signal in (proc.finished, proc.readyReadStandardOutput, proc.readyReadStandardError):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        proc.write(b'{"cmd":"quit"}\n')
        if not proc.waitForFinished(3000):  # vr-toast waits for SteamVR before VR_Shutdown
            proc.terminate()            # flatpak-spawn forwards SIGTERM to the host process
            if not proc.waitForFinished(1500):
                proc.kill()
                proc.waitForFinished(500)
        self._set_state("unknown")

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            log.info("vr-toast: %s", state)
            self.stateChanged.emit(state)

    def _launch(self) -> None:
        binary = paths.tool_path("vr-toast")
        if binary is None:
            self._unavailable()
            return
        proc = QProcess(self)
        program, arguments = hostspawn.command(binary)
        proc.setProgram(program)
        proc.setArguments(arguments)
        proc.readyReadStandardOutput.connect(self._read)
        proc.readyReadStandardError.connect(
            lambda: log.debug("vr-toast: %s", bytes(proc.readAllStandardError().data()).decode(errors="replace")))
        proc.finished.connect(lambda code, _status: self._finished(proc, code))
        self._proc = proc
        self._buffer = b""
        self._set_state("starting")
        proc.start()

    def _read(self) -> None:
        if self._proc is None:
            return
        self._buffer += bytes(self._proc.readAllStandardOutput().data())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("event")
            if kind == "ready":
                self._set_state("ready")
            elif kind == "unavailable":
                log.info("vr-toast unavailable: %s", event.get("reason"))
            elif kind == "error":
                log.warning("vr-toast error: %s", event.get("message"))

    def _finished(self, proc: QProcess, code: int) -> None:
        if proc is not self._proc:
            return
        self._proc = None
        if self.state == "ready":
            self._set_state("unknown")        # SteamVR quit: try again on the next toast
        else:
            self._unavailable()

    def _unavailable(self) -> None:
        self._retry_at = time.monotonic() + RETRY_AFTER_S
        self._set_state("unavailable")

    def _send(self, message: dict) -> None:
        if self._proc is not None:
            self._proc.write((json.dumps(message, separators=(",", ":")) + "\n").encode())

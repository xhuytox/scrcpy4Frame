"""Phone-shaped SteamVR panel: we run scrcpy's server ourselves and `vr-mirror` renders the video.

The scrcpy window is stuck inside the Frame's fixed landscape virtual screen, hence the overlay.
Audio is cast separately, on request (audiocast.py). Protocol: docs/CONTRACTS.md.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import subprocess
from collections import deque

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from . import hostspawn, paths, scrolling
from .adb import Adb, make_env
from .settings import Settings
from .tasks import TaskRunner

log = logging.getLogger(__name__)

SERVER_REMOTE_PATH = "/data/local/tmp/scrcpy4frame-server.jar"
PANEL_SIZES_M = {"small": 0.22, "medium": 0.30, "large": 0.40}
THEATER_SIZE_M = 3.5       # the panel's long side when theater mode starts, standing on the floor 3.8 m away
CONNECT_TIMEOUT_MS = 20_000
# vr-mirror waits for SteamVR to finish copying its last frame before VR_Shutdown. Killing it
# early can crash SteamVR's compositor.
HELPER_EXIT_GRACE_MS = 5000

_retiring: set[QProcess] = set()     # helpers still exiting after their session ended
KEYS = ("back", "home", "app_switch", "power")

_version_cache: str | None = None


def make_scid() -> str:
    return f"{secrets.randbits(31):08x}"


def parse_scrcpy_version(text: str) -> str | None:
    match = re.search(r"scrcpy (\d+\.\d+(?:\.\d+)?)", text)
    return match.group(1) if match else None


def scrcpy_version() -> str | None:
    """Version of the bundled scrcpy. The server must be started with exactly this version."""
    global _version_cache
    if _version_cache is None:
        scrcpy = paths.tool_path("scrcpy")
        if scrcpy:
            try:
                out = subprocess.run([scrcpy, "--version"], capture_output=True, text=True, timeout=10)
                _version_cache = parse_scrcpy_version(out.stdout + out.stderr)
            except (OSError, subprocess.TimeoutExpired):
                _version_cache = None
    return _version_cache


def push_server(adb: Adb, serial: str) -> str:
    """Copy the bundled scrcpy-server to the phone and return the scrcpy version. Blocking."""
    version = scrcpy_version()
    server = paths.scrcpy_server_file()
    if not version or server is None:
        raise RuntimeError("the bundled scrcpy server is missing")
    push = adb.run("push", str(server), SERVER_REMOTE_PATH, serial=serial, timeout=60)
    if push.rc != 0:
        raise RuntimeError(push.text or "adb push failed")
    return version


def parse_screen_long_dp(text: str) -> float:
    """Long side of the device's screen in dp, from `wm size; wm density`, or 0 if unknown.

    Overrides (Android's display size and resolution settings) win over the physical values.
    """
    def pick(kind: str, pattern: str):
        found = dict(re.findall(rf"(Physical|Override) {kind}: {pattern}", text))
        return found.get("Override") or found.get("Physical")

    size, density = pick("size", r"(\d+x\d+)"), pick("density", r"(\d+)")
    if not size or not density or int(density) <= 0:
        return 0.0
    return max(int(side) for side in size.split("x")) * 160 / int(density)


def server_command(version: str, scid: str, settings: Settings) -> str:
    """`adb shell` command that starts scrcpy's server for our panel (video + control, no audio)."""
    params = [
        f"scid={scid}", "log_level=info", "tunnel_forward=true", "audio=false", "control=true",
        "video_codec=h264",          # vr-mirror's decoder is H.264-only
        f"max_size={settings.max_size}", f"video_bit_rate={settings.bitrate_mbps * 1_000_000}",
        f"max_fps={settings.max_fps}", "clipboard_autosync=false",
    ]
    if settings.keep_active:
        params.append("keep_active=true")
    return (f"CLASSPATH={SERVER_REMOTE_PATH} app_process / com.genymobile.scrcpy.Server {version} "
            + " ".join(params))


def panel_size_m(settings: Settings) -> float:
    """Long side of the panel in meters when it opens: the chosen preset."""
    return PANEL_SIZES_M.get(settings.panel_size, PANEL_SIZES_M["medium"])


def connect_message(port: int, title: str, settings: Settings, screen_long_dp: float = 0.0) -> dict:
    """The panel opens in front of the user at the Settings preset, every session.

    The device scrolls as fast as this app's pages: the same share of its screen per wheel click,
    which needs its screen's size in dp (`screen_long_dp`, 0 if unknown).
    """
    message = {"cmd": "connect", "host": "127.0.0.1", "port": port, "title": title,
               "turn_screen_off": settings.turn_screen_off,
               "panel_size_m": panel_size_m(settings),
               "theater_size_m": THEATER_SIZE_M,
               "scroll_screens_per_click": round(scrolling.PAGE_SHARE_PER_CLICK, 4),
               "dashboard_button": settings.dashboard_button}
    if screen_long_dp > 0:
        message["screen_long_dp"] = round(screen_long_dp, 1)
    return message


def available() -> bool:
    return bool(paths.tool_path("vr-mirror") and paths.scrcpy_server_file() and paths.tool_path("scrcpy"))


class VrMirrorSession(QObject):
    """One phone shown as a VR panel. Emits `ended(kind, message)` exactly once.

    kind: stopped (by us) | disconnected | vr_unavailable | failed
    """

    event = Signal(str, "QVariantMap")
    ended = Signal(str, str)

    def __init__(self, adb: Adb, serial: str, title: str, settings: Settings, runner: TaskRunner,
                 parent: QObject | None = None):
        super().__init__(parent)
        self.adb, self.serial, self.title = adb, serial, title
        self.settings, self.runner = settings, runner
        self.screen_long_dp = 0.0
        self._audio_state = (False, True)   # what the panel's audio button shows: (on, enabled)
        self.scid = make_scid()
        self.port: int | None = None
        self.state = "starting"          # starting | streaming | ended
        self._server: QProcess | None = None
        self._helper: QProcess | None = None
        self._buffer = b""
        self._server_log: deque[str] = deque(maxlen=50)
        self._timeout = QTimer(self, singleShot=True, interval=CONNECT_TIMEOUT_MS,
                               timeout=lambda: self.stop("failed", "The device's video didn't start in time."))

    def start(self) -> None:
        self._timeout.start()
        self.runner.run(self._prepare, on_done=self._prepared,
                        on_error=lambda e: self.stop("failed", f"Couldn't start the device's video: {e}"))

    def send_key(self, key: str) -> None:
        if key in KEYS:
            self._send({"cmd": "key", "key": key})

    def recenter(self, now: bool = False) -> None:
        """Move the panel in front of the user, showing it if hidden. Only Recenter itself counts
        down first; `now` skips that (showing a hidden panel, which in theater shows where it was
        instead)."""
        self._send({"cmd": "recenter", "now": True} if now else {"cmd": "recenter"})

    def set_theater(self, on: bool) -> None:
        """Theater on or off, as the panel's own Theater button does; shows a hidden panel."""
        self._send({"cmd": "theater", "on": bool(on)})

    def set_panel_visible(self, visible: bool) -> None:
        """Hide the panel while the phone keeps streaming, or show it again."""
        self._send({"cmd": "show" if visible else "hide"})

    def set_audio_state(self, on: bool, enabled: bool) -> None:
        """What the panel's audio button shows: casting or not, and whether it can be pressed."""
        state = (bool(on), bool(enabled))
        if state != self._audio_state:
            self._audio_state = state
            self._send_audio_state()

    def _send_audio_state(self) -> None:
        self._send({"cmd": "audio", "on": self._audio_state[0], "enabled": self._audio_state[1]})

    def set_dashboard_button(self, enabled: bool) -> None:
        """Offer (or stop offering) the dashboard button that shows the hidden panel."""
        self._send({"cmd": "dashboard_button", "enabled": bool(enabled)})

    def stop(self, kind: str = "stopped", message: str = "", blocking: bool = False) -> None:
        """End the session. With `blocking` (app shutdown) wait for the helper to exit, otherwise
        let it finish in the background."""
        if self.state == "ended":
            return
        self.state = "ended"
        self._timeout.stop()
        if self._helper is not None:
            self._helper.write(b'{"cmd":"quit"}\n')
            if blocking:
                _stop_process(self._helper, graceful=True, grace_ms=HELPER_EXIT_GRACE_MS)
            else:
                _retire(self._helper)                  # closing its sockets makes the server exit
        _stop_process(self._server, graceful=False)
        self._helper = self._server = None
        if self.port:
            self.runner.run(self.adb.forward_remove, self.serial, self.port)
        log.info("VR panel session ended: %s %s", kind, message)
        self.ended.emit(kind, message)

    def _prepare(self) -> int:
        """Push the server and open the tunnel. Runs on a worker thread, returns the local port."""
        push_server(self.adb, self.serial)
        port = self.adb.forward(self.serial, f"localabstract:scrcpy_{self.scid}")
        if not port:
            raise RuntimeError("adb forward failed")
        # For scrolling at the same speed as this app (see connect_message).
        self.screen_long_dp = parse_screen_long_dp(self.adb.shell(self.serial, "wm size; wm density").out)
        return port

    def _prepared(self, port: int) -> None:
        self.port = port
        if self.state == "ended":
            self.runner.run(self.adb.forward_remove, self.serial, port)
            return
        env = QProcessEnvironment()
        for key, value in make_env().items():
            env.insert(key, value)

        command = server_command(scrcpy_version() or "", self.scid, self.settings)
        self._server = QProcess(self)
        self._server.setProcessEnvironment(env)
        self._server.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._server.setProgram(self.adb.adb_path)
        self._server.setArguments(["-s", self.serial, "shell", command])
        self._server.readyReadStandardOutput.connect(self._read_server)
        self._server.finished.connect(self._server_finished)
        self._server.start()

        program, arguments = hostspawn.command(paths.tool_path("vr-mirror"), paths.vr_mirror_data_files())
        self._helper = QProcess(self)
        self._helper.setProgram(program)
        self._helper.setArguments(arguments)
        self._helper.readyReadStandardOutput.connect(self._read_helper)
        self._helper.readyReadStandardError.connect(self._log_helper_stderr)
        self._helper.finished.connect(self._helper_finished)
        self._helper.start()
        self._send(connect_message(port, self.title, self.settings, self.screen_long_dp))
        self._send_audio_state()       # anything set before the helper started was dropped

    def _send(self, message: dict) -> None:
        if self._helper is not None:
            self._helper.write((json.dumps(message, separators=(",", ":")) + "\n").encode())

    def _read_helper(self) -> None:
        if self._helper is None:
            return
        self._buffer += bytes(self._helper.readAllStandardOutput().data())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and isinstance(message.get("event"), str):
                self._handle(message)

    def _handle(self, message: dict) -> None:
        kind = message["event"]
        if kind == "unavailable":
            self.stop("vr_unavailable", str(message.get("reason", "")))
            return
        if kind == "first_frame":
            self._timeout.stop()
            self.state = "streaming"
        elif kind == "disconnected":
            self.stop("disconnected", str(message.get("reason", "")))
            return
        elif kind == "error":
            log.warning("vr-mirror: %s", message.get("message"))
        elif kind != "stats":
            log.info("vr-mirror: %s", message)
        self.event.emit(kind, message)

    def _log_helper_stderr(self) -> None:
        if self._helper is not None:
            for line in bytes(self._helper.readAllStandardError().data()).decode(errors="replace").splitlines():
                log.debug("vr-mirror: %s", line)

    def _read_server(self) -> None:
        if self._server is None:
            return
        for line in _drain(self._server).splitlines():
            self._server_log.append(line)
            log.info("scrcpy-server: %s", line)

    def _helper_finished(self, code: int, _status) -> None:
        if self.state != "ended":
            self.stop("failed", f"The overlay helper stopped unexpectedly (exit {code}).")

    def _server_finished(self, code: int, _status) -> None:
        if self.state != "ended":
            tail = " ".join(list(self._server_log)[-3:])
            self.stop("disconnected" if self.state == "streaming" else "failed",
                      tail or f"scrcpy's server on the device stopped (exit {code}).")


def _drain(proc: QProcess | None) -> str:
    if proc is None:
        return ""
    return bytes(proc.readAllStandardOutput().data()).decode(errors="replace")


def _retire(proc: QProcess) -> None:
    """Let a helper finish its slow, safe exit in the background, forcing it only if it hangs.

    The session may be deleted first, so the process is unparented and kept alive in `_retiring`.
    """
    for signal in (proc.finished, proc.readyReadStandardOutput, proc.readyReadStandardError):
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass
    proc.setParent(None)
    _retiring.add(proc)
    proc.closeWriteChannel()

    def done(*_args) -> None:
        _retiring.discard(proc)
        proc.deleteLater()

    proc.finished.connect(done)
    QTimer.singleShot(HELPER_EXIT_GRACE_MS, lambda: proc.state() != QProcess.ProcessState.NotRunning
                      and proc.terminate())
    QTimer.singleShot(HELPER_EXIT_GRACE_MS + 3000, lambda: proc.state() != QProcess.ProcessState.NotRunning
                      and proc.kill())


def _stop_process(proc: QProcess | None, graceful: bool, grace_ms: int = 1500) -> None:
    if proc is None:
        return
    for signal in (proc.finished, proc.readyReadStandardOutput, proc.readyReadStandardError):
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass
    if proc.state() == QProcess.ProcessState.NotRunning:
        return
    if graceful:
        proc.closeWriteChannel()       # after "quit", stdin EOF ends the helper too
        if proc.waitForFinished(grace_ms):
            return
    proc.terminate()                   # flatpak-spawn forwards SIGTERM to the host process
    if not proc.waitForFinished(1500):
        proc.kill()
        proc.waitForFinished(500)

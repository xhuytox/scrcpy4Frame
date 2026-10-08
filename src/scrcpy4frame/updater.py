"""Finds a newer release on GitHub at startup and installs it over the running Flatpak when asked.

The install is install.sh's: download the release bundle, then `flatpak install --user --reinstall
--bundle` on the host. The running copy keeps its files until it quits, so restarting starts the
new version.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QCoreApplication, QObject, QProcess, Signal

from . import __version__, paths

log = logging.getLogger(__name__)

REPO = "xhuytox/scrcpy4Frame"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
ASSET = f"scrcpy4frame-{platform.machine()}.flatpak"
FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"
TIMEOUT_S = 30
CHUNK = 256 * 1024
INSTALL_TIMEOUT_S = 30 * 60     # a release on a new runtime pulls that runtime from Flathub
RELAUNCH_WAIT_MS = 5000
# Runs on the host: says it's up, waits (up to 30 s) for this instance to exit, then starts the
# installed version. Its output goes nowhere once we've quit, so nothing may write to it.
RELAUNCH = ('echo up; exec </dev/null >/dev/null 2>&1; '
            'for _ in $(seq 150); do flatpak ps --columns=application | grep -qxF "$1" || break; sleep 0.2; done; '
            'exec flatpak run "$1"')


class UpdateError(Exception):
    """A failure whose message can be shown as is."""


@dataclass(frozen=True)
class Release:
    version: tuple[int, int, int]
    url: str            # the bundle's download URL
    size: int
    sha256: str = ""    # GitHub's digest of the bundle, when it has one

    @property
    def name(self) -> str:
        return ".".join(map(str, self.version))


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def newest(releases: list[dict], asset: str = ASSET) -> Release | None:
    """The published release with the highest version that has the bundle, in whatever order
    GitHub lists them. Drafts and pre-releases don't count."""
    best = None
    for release in releases:
        version = parse_version(release.get("tag_name") or "")
        if version is None or release.get("draft") or release.get("prerelease"):
            continue
        for item in release.get("assets") or []:
            if item.get("name") != asset or not item.get("browser_download_url"):
                continue
            digest = item.get("digest") or ""
            found = Release(version, item["browser_download_url"], int(item.get("size") or 0),
                            digest.removeprefix("sha256:") if digest.startswith("sha256:") else "")
            if best is None or found.version > best.version:
                best = found
    return best


def supported() -> bool:
    """Only the Flatpak can install a release bundle over itself."""
    return paths.in_flatpak() and parse_version(__version__) is not None


def _open(url: str, accept: str = "*/*"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"scrcpy4Frame/{__version__}"})
    return urllib.request.urlopen(request, timeout=TIMEOUT_S)


# Runs on a worker thread.
def find_update() -> Release | None:
    """The newest release if it's newer than this version. None when there's none, or GitHub
    can't be asked (offline, or the repo isn't public)."""
    try:
        with _open(RELEASES_URL, "application/vnd.github+json") as response:
            data = json.load(response)
        release = newest(data) if isinstance(data, list) else None
    except (OSError, ValueError) as exc:
        log.warning("couldn't check for updates: %s", exc)
        return None
    current = parse_version(__version__)
    log.info("newest release: %s (running %s)", release.name if release else "none", __version__)
    return release if release and current and release.version > current else None


def download_dir() -> Path:
    return paths.cache_dir() / "update"


# Runs on a worker thread.
def download(release: Release, target: Path, progress: Callable[[float], None],
             cancelled: Callable[[], bool]) -> Path:
    """Download the bundle to `target`, checking its size and digest. `progress` gets 0..1."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest, done = hashlib.sha256(), 0
    try:
        with _open(release.url) as response, partial.open("wb") as out:
            total = release.size or int(response.headers.get("Content-Length") or 0)
            while chunk := response.read(CHUNK):
                if cancelled():
                    raise UpdateError("The download was cancelled.")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if total:
                    progress(min(done / total, 1.0))
        if release.size and done != release.size:
            raise UpdateError("The download was cut short. Try again.")
        if release.sha256 and digest.hexdigest() != release.sha256:
            raise UpdateError("The download doesn't match the release. Try again.")
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def _host(*args: str) -> list[str]:
    return ["flatpak-spawn", "--host", *args]


# Runs on a worker thread.
def install_bundle(bundle: Path) -> None:
    """Install the bundle for the user over this app, as install.sh does, then delete it."""
    commands = [
        _host("flatpak", "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO),
        # The host sees the cache dir at the same path.
        _host("flatpak", "install", "--user", "--noninteractive", "--reinstall", "--bundle", str(bundle)),
    ]
    try:
        for command in commands:
            log.info("running %s", " ".join(command))
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=INSTALL_TIMEOUT_S)
            output = (result.stdout + result.stderr).strip()
            if output:
                log.info("flatpak: %s", output)
            if result.returncode != 0:
                last = output.splitlines()[-1] if output else f"exit code {result.returncode}"
                raise UpdateError(f"Flatpak couldn't install it: {last}")
    except subprocess.TimeoutExpired:
        raise UpdateError("Installing took too long.") from None
    finally:
        bundle.unlink(missing_ok=True)


def describe(error: BaseException) -> str:
    if isinstance(error, UpdateError):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return f"GitHub answered with error {error.code}. Try again later."
    if isinstance(error, OSError):
        return "Couldn't download it. Check the Frame's internet connection."
    return str(error)


class Updater(QObject):
    """The update shown in the banner and in Settings › About: checked for once, at startup."""

    changed = Signal()
    _progressFromThread = Signal(float)

    def __init__(self, runner, game_mode: bool, parent: QObject | None = None):
        super().__init__(parent)
        self.runner = runner
        self.game_mode = game_mode
        self.release: Release | None = None     # newer than this version
        self.state = "idle"                     # idle | downloading | installing | installed
        self.progress = 0.0
        self.error = ""
        self.banner = False
        self._checked = False
        self._closing = False
        self._progressFromThread.connect(self._on_progress)

    def view(self) -> dict:
        return {"available": self.release is not None, "state": self.state,
                "current": __version__, "latest": self.release.name if self.release else "",
                "progress": self.progress, "error": self.error, "banner": self.banner,
                # Under gamescope Steam has to start it, so it can only quit.
                "canRestart": not self.game_mode}

    def check(self) -> None:
        if self._checked or not supported():
            return
        self._checked = True
        shutil.rmtree(download_dir(), ignore_errors=True)       # left by a run that quit mid-update
        self.runner.run(find_update, on_done=self._found)

    def _found(self, release: Release | None) -> None:
        if release is not None:
            self.release, self.banner = release, True
            self.changed.emit()

    def install(self) -> None:
        if self.release is None or self.state != "idle":
            return
        self.state, self.progress, self.error, self.banner = "downloading", 0.0, "", True
        self.changed.emit()
        self.runner.run(download, self.release, download_dir() / ASSET, self._progressFromThread.emit,
                        lambda: self._closing, on_done=self._downloaded, on_error=self._failed)

    def _on_progress(self, value: float) -> None:
        if self.state == "downloading" and int(value * 100) != int(self.progress * 100):
            self.progress = value
            self.changed.emit()

    def _downloaded(self, bundle: Path) -> None:
        self.state, self.progress = "installing", 1.0
        self.changed.emit()
        self.runner.run(install_bundle, bundle, on_done=self._installed, on_error=self._failed)

    def _installed(self, _result) -> None:
        log.info("installed scrcpy4Frame %s", self.release.name)
        self.state, self.banner = "installed", True
        self.changed.emit()

    def _failed(self, error: BaseException) -> None:
        self.state, self.error, self.banner = "idle", describe(error), True
        self.changed.emit()

    def close_banner(self) -> None:
        if self.banner:
            self.banner = False
            self.changed.emit()

    def restart(self) -> None:
        """Quit, and in Desktop Mode start the installed version once this one has gone."""
        if self.state != "installed":
            return
        if self.game_mode:
            QCoreApplication.quit()
            return
        # No --watch-bus: the relauncher has to outlive us.
        program, *args = _host("sh", "-c", RELAUNCH, "sh", paths.APP_ID)
        proc = QProcess(self)
        proc.start(program, args)
        if proc.waitForReadyRead(RELAUNCH_WAIT_MS):
            QCoreApplication.quit()
            return
        log.warning("couldn't start the relauncher: %s", proc.errorString())
        proc.kill()
        self.error = "Couldn't restart scrcpy4Frame. Quit it and open it again."
        self.changed.emit()

    def shutdown(self) -> None:
        self._closing = True        # stops a download at its next chunk

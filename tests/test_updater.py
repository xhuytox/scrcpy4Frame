"""The startup update check, the download and install steps, and the Updater's states."""

import hashlib
import io
import json
import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from scrcpy4frame import companion, updater  # noqa: E402
from scrcpy4frame.updater import CompanionTarget, Release, UpdateError  # noqa: E402

ASSET = "scrcpy4frame-aarch64.flatpak"


def _release(tag, asset=ASSET, draft=False, prerelease=False, digest=None):
    assets = [{"name": asset, "browser_download_url": f"https://example.invalid/{tag}/{asset}", "size": 10,
               "digest": digest}] if asset else []
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": assets}


def test_newest_goes_by_version_number_not_list_order():
    releases = [
        _release("v1.2.0"),
        _release("v1.10.0", digest="sha256:abc"),
        _release("v1.9.9"),
        _release("v2.0.0", prerelease=True),
        _release("v3.0.0", draft=True),
        _release("v1.11.0", asset="something-else.zip"),
        _release("nightly"),
    ]
    best = updater.newest(releases, ASSET)
    assert best.version == (1, 10, 0) and best.name == "1.10.0"
    assert best.sha256 == "abc" and best.url.endswith("/v1.10.0/" + ASSET)
    assert updater.newest([], ASSET) is None


def test_parse_version():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("1.2.3") == (1, 2, 3)
    assert updater.parse_version("v1.2") is None
    assert updater.parse_version("v1.2.3-rc1") is None


def test_find_update_offers_only_a_newer_release(monkeypatch):
    listing = [_release("v1.0.0", asset=updater.ASSET), _release("v1.1.0", asset=updater.ASSET)]
    monkeypatch.setattr(updater, "_open", lambda url, accept="*/*": io.BytesIO(json.dumps(listing).encode()))
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    assert updater.find_update().name == "1.1.0"
    monkeypatch.setattr(updater, "__version__", "1.1.0")
    assert updater.find_update() is None
    monkeypatch.setattr(updater, "__version__", "1.2.0")
    assert updater.find_update() is None

    def offline(url, accept="*/*"):
        raise OSError("Name or service not known")
    monkeypatch.setattr(updater, "_open", offline)
    assert updater.find_update() is None


def test_download_checks_size_and_digest(tmp_path):
    payload = os.urandom(updater.CHUNK * 2 + 123)
    source = tmp_path / "source.flatpak"
    source.write_bytes(payload)
    target = tmp_path / "update" / ASSET
    good = Release((1, 1, 0), source.as_uri(), len(payload), hashlib.sha256(payload).hexdigest())
    seen = []
    assert updater.download(good, target, seen.append, lambda: False) == target
    assert target.read_bytes() == payload and seen[-1] == 1.0 and seen == sorted(seen)
    target.unlink()

    for bad in (Release((1, 1, 0), source.as_uri(), len(payload), "0" * 64),
                Release((1, 1, 0), source.as_uri(), len(payload) + 1)):
        with pytest.raises(UpdateError):
            updater.download(bad, target, lambda _: None, lambda: False)
    with pytest.raises(UpdateError):
        updater.download(good, target, lambda _: None, lambda: True)
    assert list(target.parent.iterdir()) == []           # no bundle or partial left behind


def test_install_runs_flatpak_on_the_host(tmp_path, monkeypatch):
    bundle = tmp_path / ASSET
    bundle.write_bytes(b"bundle")
    commands = []
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda command, **kw: commands.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    updater.install_bundle(bundle)
    assert commands[0][:3] == ["flatpak-spawn", "--host", "flatpak"] and "remote-add" in commands[0]
    assert commands[1] == ["flatpak-spawn", "--host", "flatpak", "install", "--user", "--noninteractive",
                           "--reinstall", "--bundle", str(bundle)]
    assert not bundle.exists()

    bundle.write_bytes(b"bundle")
    monkeypatch.setattr(updater.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(
        command, 1, "", "Installing…\nerror: Not enough disk space\n"))
    with pytest.raises(UpdateError, match="Not enough disk space"):
        updater.install_bundle(bundle)
    assert not bundle.exists()


DEPLOY = "/home/deck/.local/share/flatpak/app/io.github.xhuytox.Scrcpy4Frame/aarch64/master/0a1b2c"


def test_copies_the_companion_out_of_the_installed_version(tmp_path, monkeypatch):
    commands = []

    def run(command, **kw):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, DEPLOY + "\n" if "info" in command else "", "")
    monkeypatch.setattr(updater.subprocess, "run", run)
    target = tmp_path / "update" / "companion.apk"
    assert updater.copy_installed_companion(target) == target
    assert commands == [
        ["flatpak-spawn", "--host", "flatpak", "info", "--user", "--show-location", "io.github.xhuytox.Scrcpy4Frame"],
        ["flatpak-spawn", "--host", "cp", "--", DEPLOY + "/files/share/scrcpy4frame/companion.apk", str(target)],
    ]
    monkeypatch.setattr(updater.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(
        command, 1, "", "error: io.github.xhuytox.Scrcpy4Frame/*unspecified*/*unspecified* not installed"))
    with pytest.raises(UpdateError, match="not installed"):
        updater.copy_installed_companion(target)


def test_update_companions_installs_the_new_companion_where_it_is_older(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    copies = []

    def copy(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"apk")
        copies.append(target)
        return target
    monkeypatch.setattr(updater, "copy_installed_companion", copy)
    monkeypatch.setattr(updater.companion, "apk_version", lambda apk: 3)
    reports = {"old": companion.InstallReport(ok=True),
               "blocked": companion.InstallReport(error="Your device blocked the install.")}
    seen = []
    monkeypatch.setattr(updater.companion, "update", lambda adb, serial, apk, version, sdk: seen.append(
        (adb, serial, apk.read_bytes(), version, sdk)) or reports.get(serial))
    targets = [CompanionTarget("Pixel 8", "old", 35), CompanionTarget("Tablet", "current", 33),
               CompanionTarget("Redmi", "blocked", 34)]
    results = updater.update_companions("adb", targets)
    assert [(name, report.ok) for name, report in results] == [("Pixel 8", True), ("Redmi", False)]
    assert seen == [("adb", "old", b"apk", 3, 35), ("adb", "current", b"apk", 3, 33), ("adb", "blocked", b"apk", 3, 34)]
    assert not copies[0].exists()

    monkeypatch.setattr(updater.companion, "apk_version", lambda apk: None)
    with pytest.raises(UpdateError):
        updater.update_companions("adb", targets)
    assert len(seen) == 3 and not copies[1].exists()


def test_companion_note():
    ok, blocked = companion.InstallReport(ok=True), companion.InstallReport(error="Your device blocked the install.")
    assert updater.companion_note([]) == ""
    assert updater.companion_note([("Pixel 8", ok)]) == "The companion app on Pixel 8 is updated too."
    assert updater.companion_note([("A", ok), ("B", ok), ("C", ok)]) == \
        "The companion apps on A, B and C are updated too."
    assert updater.companion_note([("Pixel 8", ok), ("Redmi", blocked)]) == (
        "The companion app on Pixel 8 is updated too. Updating the companion app on Redmi didn't finish: "
        "Your device blocked the install. scrcpy4Frame tries again the next time it starts.")


class _Quit:
    def __init__(self):
        self.calls = 0

    def quit(self):
        self.calls += 1


@pytest.fixture
def qt():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def _sync_runner():
    class Runner:
        def run(self, fn, *args, on_done=None, on_error=None):
            try:
                result = fn(*args)
            except Exception as exc:
                if on_error:
                    on_error(exc)
                return
            if on_done:
                on_done(result)
    return Runner()


@pytest.fixture
def upd(qt, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(updater, "supported", lambda: True)
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    monkeypatch.setattr(updater, "find_update", lambda: Release((1, 1, 0), "https://example.invalid/b", 6))
    return updater.Updater(_sync_runner(), game_mode=False)


def test_updater_offers_downloads_and_installs(upd, monkeypatch):
    changes = []
    upd.changed.connect(lambda: changes.append(upd.view()["state"]))
    assert upd.view()["available"] is False
    upd.check()
    view = upd.view()
    assert view["available"] and view["banner"] and view["latest"] == "1.1.0" and view["current"] == "1.0.0"
    upd.check()                                         # once per run
    assert len(changes) == 1

    upd.close_banner()
    assert upd.view()["banner"] is False and upd.view()["available"] is True

    def fake_download(release, target, progress, cancelled):
        progress(0.5)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"bundle")
        return target
    installed = []
    monkeypatch.setattr(updater, "download", fake_download)
    monkeypatch.setattr(updater, "install_bundle", installed.append)
    upd.install()
    assert installed and installed[0].name == updater.ASSET
    assert changes[-4:] == ["downloading", "downloading", "installing", "installed"]
    assert upd.view()["banner"] is True                 # shown again: it needs a restart now
    upd.install()                                       # nothing more to do
    assert len(installed) == 1


def test_updater_reports_failures_and_can_try_again(upd, monkeypatch):
    upd.check()

    def broken(bundle):
        raise UpdateError("Flatpak couldn't install it: error: Not enough disk space")
    monkeypatch.setattr(updater, "download", lambda release, target, progress, cancelled: target)
    monkeypatch.setattr(updater, "install_bundle", broken)
    upd.close_banner()
    upd.install()
    view = upd.view()
    assert view["state"] == "idle" and "disk space" in view["error"] and view["banner"]
    monkeypatch.setattr(updater, "install_bundle", lambda bundle: None)
    upd.install()
    assert upd.view()["state"] == "installed" and upd.view()["error"] == ""


def test_updater_updates_older_companions_once_installed(upd, monkeypatch):
    targets = [CompanionTarget("Pixel 8", "192.168.1.23:41235", 35)]
    upd.adb, upd.companions = "adb", lambda: targets
    states, calls = [], []
    upd.changed.connect(lambda: states.append(upd.view()["state"]))
    monkeypatch.setattr(updater, "download", lambda release, target, progress, cancelled: target)
    monkeypatch.setattr(updater, "install_bundle", lambda bundle: None)
    monkeypatch.setattr(updater, "update_companions", lambda adb, found: calls.append((adb, found)) or [
        ("Pixel 8", companion.InstallReport(ok=True))])
    upd.check()
    upd.install()
    assert calls == [("adb", targets)] and states[-3:] == ["installing", "companion", "installed"]
    view = upd.view()
    assert view["note"] == "The companion app on Pixel 8 is updated too." and view["banner"] and not view["error"]

    def broken(adb, found):
        raise UpdateError("cp: No such file or directory")
    monkeypatch.setattr(updater, "update_companions", broken)
    upd.state = "idle"
    upd.install()
    view = upd.view()                                   # the app update still went through
    assert view["state"] == "installed" and not view["error"] and "checks again" in view["note"]

    upd.companions = lambda: []                         # no connected device with the companion
    upd.state = "idle"
    upd.install()
    assert states[-2:] == ["installing", "installed"] and upd.view()["note"] == ""


def test_no_check_outside_the_flatpak(qt, monkeypatch):
    calls = []
    monkeypatch.setattr(updater.paths, "in_flatpak", lambda: False)
    monkeypatch.setattr(updater, "find_update", lambda: calls.append(1))
    updater.Updater(_sync_runner(), game_mode=False).check()
    assert calls == []


def test_restart(upd, monkeypatch):
    stub = _Quit()
    monkeypatch.setattr(updater, "QCoreApplication", stub)
    monkeypatch.setattr(updater, "_host", lambda *args: list(args))     # run the relauncher here
    upd.restart()
    assert stub.calls == 0                              # nothing installed yet

    upd.state = "installed"
    monkeypatch.setattr(updater, "RELAUNCH", "exit 1")
    upd.restart()
    assert stub.calls == 0 and "Quit it and open it again" in upd.view()["error"]
    monkeypatch.setattr(updater, "RELAUNCH", "echo up")
    upd.restart()
    assert stub.calls == 1

    upd.game_mode = True                                # Steam has to start it again
    monkeypatch.setattr(updater, "RELAUNCH", "exit 1")
    upd.restart()
    assert stub.calls == 2 and upd.view()["canRestart"] is False

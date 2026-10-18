# scrcpy4Frame

> NOTICE: A large portion of this project is AI generated.

Mirror and control your Android device (phone or tablet) on the **Valve Steam Frame**, over Wi-Fi, using
[scrcpy](https://github.com/Genymobile/scrcpy).

- **Self-contained:** adb, scrcpy and the scrcpy server are bundled in the Flatpak. Nothing else needs
  to be installed on the Frame, and nothing is installed on the Android device unless you opt in to the
  companion app.
- **Guided setup:** The setup wizard walks you through every step on the Android device.
- **Optional companion app:** (opt-in) Installed and granted over adb with no taps on the device:
  - turns Wireless debugging on when you open scrcpy4Frame and off when you close it, so it's only
    on while you use it
  - forwards the Android device's notifications to the Frame while scrcpy4Frame runs: pop-ups in VR,
    or KDE pop-ups in Desktop Mode

## Installation

On the Frame, in Desktop Mode, open Konsole and run:

```sh
curl -sSL https://raw.githubusercontent.com/xhuytox/scrcpy4Frame/main/install.sh | bash
```

This downloads `scrcpy4frame-aarch64.flatpak` from the [latest release](https://github.com/xhuytox/scrcpy4Frame/releases/latest)
and installs it. You'll find scrcpy4Frame under Utilities in the
application menu.

When a newer release is out, scrcpy4Frame offers it in a banner as it starts, and in Settings › About.
Updating there downloads and installs it, then restarts the app. Running the same command again
also updates it. Your settings are kept. scrcpy4Frame also brings the companion app up to date: on
connected devices right after an update, and on other devices when they connect.

To uninstall:

```sh
flatpak uninstall --user io.github.xhuytox.Scrcpy4Frame
```

## Setup
** On the Android device 
- Turn on Developer options (tap Build number 7×)
- Turn on Wireless debugging
- Tap "Pair device with pairing code"

** On the Frame
- Type the 6-digit pairing code on the Frame 
- (optional Install companion app
- (optional) Add Quick Settings tile for 1-tap wireless reconnect

**Android 5–10:** a one-time cable setup. Plug the device into the Frame's USB-C port and tap
"Allow". The app then reconnects over Wi-Fi. This lasts until the device restarts.

## Building

### Release bundle

>NOTICE: The release bundle and the development build run on the Frame over SSH. Turn on Developer Mode
>on the Frame (Steam Settings › System) and set a password so SSH works. To avoid typing the password
>every time, install an SSH key once with `ssh-copy-id <user>@<frame-host>`.

```sh
scripts/release-build.sh [<user>@<frame-host>]  # -> dist/scrcpy4frame-aarch64.flatpak
```

### Flatpak for development, built on the Frame itself (no Docker needed)

```sh
scripts/frame-build.sh <user>@<frame-host>      # rsync, build with org.flatpak.Builder, install
```

### Flatpak, built in an arm64 container (Docker or Podman)

```sh
scripts/build.sh                                  # -> dist/scrcpy4frame-aarch64.flatpak
scripts/deploy.sh <user>@<frame-host>           # copy + flatpak install --user
```

### Diagnosing on the Frame

```sh
scripts/frame-diagnose.sh <user>@<frame-host>   # USB host mode, firewall, SteamVR paths, …
ssh <user>@<frame-host> flatpak run io.github.xhuytox.Scrcpy4Frame cli diagnose
ssh <user>@<frame-host> flatpak run io.github.xhuytox.Scrcpy4Frame cli discover --seconds 20
```

### Companion APK (needs JDK 17+ and the Android SDK)

```sh
scripts/build-companion.sh          # -> dist/companion.apk
```

## Developing on macOS or Linux

```sh
scripts/run-dev.sh            # runs from source (creates .venv on first run)
scripts/run-dev.sh --test     # pytest: parsers, wizard state machine, controller, every QML screen
scripts/run-dev.sh cli discover
```

Outside the Flatpak, tools come from `PATH` or the Android SDK. You can override them with
`SCRCPY4FRAME_ADB`, `SCRCPY4FRAME_SCRCPY`, `SCRCPY4FRAME_VR_TOAST` and
`SCRCPY4FRAME_COMPANION_APK`. Mirroring needs scrcpy on `PATH` (for example `brew install scrcpy`
on macOS). If Android devices are plugged into your machine for other work, set
`SCRCPY4FRAME_NO_USB=1` so the app's adb server leaves them alone. The dev loop uses its own adb
key (in the app's data dir) and its own adb server on port 5038, so it doesn't touch your normal
adb setup.

## Layout

```
src/scrcpy4frame/   adb.py (private server, parsers) · discovery.py (mDNS) · wizard.py (setup state machine)
                    controller.py (QML-facing) · mirror.py · companion.py · companion_link.py · updater.py
                    notify/ (router, VR overlay, desktop pop-ups, chime) · guides.py + content/guides.json
                    qml/ (Main, Home, Wizard, Settings, Logs, components/)
companion/          optional Android companion (Kotlin)
vr-mirror/          SteamVR overlay that shows the device in VR (C++)
vr-toast/           OpenVR notification overlay helper (C++)
flatpak/            manifest + modules
install.sh          the curl installer: downloads the release bundle, flatpak install --user
docs/CONTRACTS.md   IDs, install paths and wire protocols shared by all the parts
```

## License

scrcpy4Frame is licensed under the [MIT License](LICENSE).

The Flatpak bundles third-party software under its own licenses, including
[scrcpy](https://github.com/Genymobile/scrcpy) (Apache-2.0),
[android-tools](https://github.com/nmeum/android-tools) (Apache-2.0),
[FFmpeg](https://ffmpeg.org) (LGPL-2.1+),
[OpenVR](https://github.com/ValveSoftware/openvr) (BSD-3-Clause) and
[PySide6](https://www.qt.io/qt-for-python) (LGPL-3.0). See
[vr-mirror/THIRD_PARTY_NOTICES.md](vr-mirror/THIRD_PARTY_NOTICES.md) for code adapted into this repository.

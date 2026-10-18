# scrcpy4Frame — shared contracts

This is the single source of truth for names, paths and wire protocols shared by the Python app
(`src/scrcpy4frame`), the Android companion (`companion/`), the OpenVR helper (`vr-toast/`) and
the Flatpak packaging (`flatpak/`). If you change something here, update every side.

## Identifiers

| What | Value |
|---|---|
| Flatpak app ID | `io.github.xhuytox.Scrcpy4Frame` |
| Companion Android package | `io.github.xhuytox.scrcpy4frame.companion` |
| Companion notification listener | `io.github.xhuytox.scrcpy4frame.companion/.NotifListener` (class `io.github.xhuytox.scrcpy4frame.companion.NotifListener`) |
| Companion abstract socket | `scrcpy4frame` (Android `LocalServerSocket("scrcpy4frame")`; Frame side: `adb forward tcp:0 localabstract:scrcpy4frame`) |
| Private adb server port | `5038` |
| Companion wake port (UDP, on the device) | `38490` |
| adb key comment shown on the device | `scrcpy4Frame@SteamFrame` (from env `LOGNAME=scrcpy4Frame HOSTNAME=SteamFrame`) |

## Install layout inside the Flatpak (`/app`)

| Path | Contents |
|---|---|
| `/app/bin/scrcpy4frame` | launcher shell script: `exec python3 -m scrcpy4frame "$@"` with `PYTHONPATH=/app/lib/scrcpy4frame` |
| `/app/lib/scrcpy4frame/scrcpy4frame/` | the Python package (copied from `src/scrcpy4frame/`, including `qml/` and `content/`) |
| `/app/bin/adb` | adb built from nmeum/android-tools |
| `/app/bin/scrcpy` | scrcpy client |
| `/app/share/scrcpy/scrcpy-server` | scrcpy server matching the client version exactly |
| `/app/share/scrcpy4frame/companion.apk` | companion APK (built by `scripts/build-companion.sh` into `dist/companion.apk`) |
| `/app/bin/vr-toast` | OpenVR overlay helper, statically linked against openvr_api and libstdc++ so it can also run on the host |

Python deps (`zeroconf`, `ifaddr`, `jeepney`) are installed into `/app` with pip. PySide6 comes from `io.qt.PySide.BaseApp`.

## GitHub release assets

Every release has one asset, `scrcpy4frame-aarch64.flatpak`, built by `scripts/release-build.sh`
(which runs `scripts/frame-build.sh --bundle --no-install` on the Frame).

Users install with `curl -sSL https://raw.githubusercontent.com/xhuytox/scrcpy4Frame/main/install.sh | bash`.
`install.sh` is served from `main` and downloads
`https://github.com/xhuytox/scrcpy4Frame/releases/latest/download/scrcpy4frame-aarch64.flatpak`
(or `releases/download/v<version>/…` when given a version). It then runs
`flatpak install --user --reinstall --bundle`. The repo path, the `main` branch, `install.sh` at the repo root
and the asset name are part of that command. Renaming any of them breaks the command for everyone,
including copies already in old release notes. Both URLs need the repo to be public.

The app version is the same in `pyproject.toml`, `src/scrcpy4frame/__init__.py` (`__version__`) and the
newest metainfo `<release>`. `scripts/release-build.sh` sets all three. The companion has its own
`versionName`/`versionCode` in `companion/app/build.gradle.kts`, and `versionCode` only goes up.

Tags are `v<version>`, with the same version as the metainfo `<release>`.

The app updates itself from the same asset (`updater.py`, Flatpak only). Once it's online after
startup, it lists `https://api.github.com/repos/xhuytox/scrcpy4Frame/releases` and takes the release
with the highest `vX.Y.Z` tag that has the asset and isn't a draft or pre-release, whatever order
GitHub lists them in. If that's newer than the running version, it offers it. Updating downloads the
asset, checks it against GitHub's sha256 `digest`, then runs `install.sh`'s
`flatpak install --user --reinstall --bundle` on the host through `flatpak-spawn --host`. Next it
copies `files/share/scrcpy4frame/companion.apk` out of the installed version (the directory
`flatpak info --user --show-location` prints, on the host, since the sandbox only sees its own `/app`)
and reads its `versionCode` from the APK's binary manifest. On each connected device whose saved
entry has the companion and whose installed companion (`dumpsys package`) has a lower `versionCode`,
it runs the same install and grants as a first install. A companion failure doesn't undo the app
update; the banner says which devices were updated and which weren't. Devices that weren't
connected get the new companion when they next connect (see `hello` below). In Desktop
Mode it then restarts with `flatpak run` once this instance has exited; in Game Mode Steam has to
start it again, so it only quits. The tag format and asset name are part of that check too.

## Companion ⇄ Frame protocol (JSON lines over the abstract socket)

- **Transport:** UTF-8 JSON, one object per line terminated by `\n`, at most 1 MiB per line. The companion
  accepts a connection only if `LocalSocket.getPeerCredentials().uid` is `2000` (shell) or `0` (root),
  and closes any other connection immediately.
- **On accept, the companion sends `hello` first:**

```json
{"type":"hello","protocol":2,"versionName":"1.0.0","versionCode":1,"sdk":34,
 "listenerConnected":true,"autoWirelessDebugging":true,"canWriteSecureSettings":true,"wakePort":38490}
```

- **`versionCode`** lower than the bundled APK's makes the Frame install the bundled APK over it, with
  the same grants as a first install, after checking the installed `versionCode` with
  `dumpsys package`. It tries once per device each time the app runs, so a failed install isn't
  retried until the next start. It never installs an older APK over a newer companion.
- **`wakePort`** (protocol 2) is the UDP port of the wake listener, or `0` on Android 10 and older. It's
  reported while the listener still retries binding, because the Frame needs it stored before the
  first time Wireless debugging goes off. Its presence tells the Frame the companion takes `wakeKey`
  and `bye`. The Frame stores it with the device (`wake_port` in `devices.json`). Protocol 1
  companions have neither.

### Companion → Frame

```json
{"type":"posted","key":"0|com.whatsapp|1|null|10123","pkg":"com.whatsapp","appLabel":"WhatsApp",
 "title":"Alice","text":"Are you coming?","when":1767225600000,"icon":"<base64 PNG, <=96x96, or null>"}
{"type":"removed","key":"0|com.whatsapp|1|null|10123"}
{"type":"pong"}
{"type":"error","message":"..."}
```

- **`posted` is sent only for notifications posted or updated after the client connected.** There is no
  snapshot of existing notifications.
- **The companion skips:**
  - ongoing notifications
  - `FLAG_FOREGROUND_SERVICE`
  - `FLAG_GROUP_SUMMARY`
  - ranking importance `<= IMPORTANCE_MIN`
  - its own package
  - notifications with an empty title and an empty text
- **`text` precedence:** `android.bigText`, then `android.text`, then the last of `android.textLines`.
- **`title` precedence:** `android.title`, then `android.conversationTitle`.

### Frame → Companion

```json
{"type":"ping"}
{"type":"dismiss","key":"..."}
{"type":"config","autoWirelessDebugging":true}
{"type":"wakeKey","key":"<base64 of 16 to 64 bytes>"}
{"type":"bye"}
```

- **`ping`** every 15 s. The Frame reconnects after 45 s without anything from the companion.
- **`wakeKey`** is sent right after every `hello` that has `wakePort`. It's the Frame's key for the UDP
  wake (below): 32 random bytes in `wake-key` in the Frame's config dir, mode 0600, the same for
  every device. The companion keeps the 4 newest keys it was given.
- **`bye`** means the Frame app is quitting. The companion sends what it has queued and hangs up, so
  the Frame knows it arrived; the Frame waits up to 1 s for that before stopping its adb server. The
  Frame sends it after everything else that needs adb, since Wireless debugging goes off. Not sent
  when the Frame removes the companion or forgets the device (it uninstalls the companion over adb
  next) or when it replaces a link. When it forgets a Wireless debugging device, the Frame then
  writes `adb_wifi_enabled=0` itself over adb, so the device ends up as `bye` would have left it.

### Restarting the listener

The socket server and the wake listener run only while Android has `NotifListener` bound. When its
process dies, Android tries to bind it again once, 10 s later. Some ROMs (Xiaomi's among them) block
that, and the listener stays down while notification access still shows as granted.

- **The Frame notices** when two connections in a row get through `adb forward` to the device but
  get no `hello`, and then checks over adb, at most every 5 minutes per link:
  `pm list packages`, the "Live notification listeners" in `dumpsys notification`, and
  `enabled_notification_listeners`.
- **If the companion is installed and has access, but isn't bound**, the Frame runs
  `cmd notification disallow_listener` then `allow_listener`. That's a fresh bind, as reinstalling
  would make. Access the user turned off is left off.
- **The companion's status screen** says "Granted, but Android has stopped it" in this state, rather
  than only "Granted".

### Wireless debugging only while the Frame app is around (Android 11+, needs `WRITE_SECURE_SETTINGS`)

Only when the `autoWirelessDebugging` preference is on, which is the default. Otherwise the companion
leaves Wireless debugging alone.

- **A Frame is present** for 60 s after any line from one of its socket clients, and for 60 s after a
  UDP wake (below). `bye` ends that client's presence and any wake's at once.
- **While a Frame is present**, a wake, Wi-Fi becoming available and `ACTION_SCREEN_ON` while on Wi-Fi
  write `Settings.Global` `adb_wifi_enabled=1`. If Android switches it straight back off on an
  untrusted network, the companion skips that network until Wi-Fi reconnects.
- **When no Frame is left** after one was present, the companion writes `adb_wifi_enabled=0`. A
  `setAndAllowWhileIdle` alarm at the moment presence would run out does this even in Doze, so a
  Frame that went away without `bye` (crashed, slept, out of range) turns it off about a minute
  later. The session is stored, so after a companion restart it still ends 60 s later unless a Frame
  shows up.
- **Nothing else** changes Wireless debugging: not a reboot (Android switches it off itself) and not
  the user turning it on by hand while no Frame is around. The companion never writes anything else.

## Waking the companion (UDP)

The Frame can't reach adb while Wireless debugging is off, so it asks the companion over UDP. One
compact JSON object per datagram, at most 512 bytes, to and from the companion's port `38490`.

1. Frame → companion: `{"type":"wake"}`
2. Companion → the sender's address and port: `{"type":"challenge","nonce":"<32 hex digits>"}`. Only if it
   holds a key, has `WRITE_SECURE_SETTINGS` and the preference is on; otherwise it doesn't answer.
3. Frame → companion: `{"type":"wake","nonce":"<the nonce>","mac":"<hex HMAC-SHA256(key, "scrcpy4frame-wake:" + nonce)>"}`
4. Companion → Frame, if the MAC matches any of its keys: `{"type":"awake"}`. It counts as a wake (see
   above).

- **Nonces** are single-use and expire after 30 s. At most 8 are outstanding; a new one replaces the
  oldest.
- **The Frame answers challenges only within 5 s of sending a wake**, so it can't be used to wake a
  device while it isn't trying to.
- **Who gets woken:** every 5 s while the app runs, each saved Wireless debugging device with the
  companion and a `wake_port` whose companion link isn't connected: unicast to its last address, plus the
  subnet broadcast (for a device whose address changed; many phones drop broadcasts while the
  screen is off). After `awake` from an address, that address isn't woken again for 30 s.
- Once Wireless debugging is on, the device advertises `_adb-tls-connect._tcp` and the Frame connects
  as usual. The port changes each time Wireless debugging goes on, so the Frame relies on mDNS here.

## Frame → vr-toast protocol (JSON lines on stdin / stdout)

`vr-toast` with no arguments, stdin and stdout as pipes.

stdin commands:

```json
{"cmd":"show","id":"<string>","width":512,"height":128,"rgba":"<base64 of width*height*4 bytes, RGBA8888, top-down>","duration_ms":5000}
{"cmd":"hide","id":"<string>"}
{"cmd":"quit"}
```

stdout events:

```json
{"event":"ready"}
{"event":"unavailable","reason":"<VR init error name>"}
{"event":"shown","id":"..."}
{"event":"expired","id":"..."}
{"event":"vr_quit"}
{"event":"error","message":"..."}
```

### Startup

1. Probe with `VR_Init(VRApplication_Background)`.
2. **If the probe fails** (for example because SteamVR isn't running), print `unavailable` and exit with code 3.
3. **If it succeeds,** shut down, re-init as `VRApplication_Overlay`, and print `ready`.

**Where it runs:** inside the Flatpak the app always starts the helper on the host. It copies the
helper to `~/.var/app/<id>/data/scrcpy4frame/vr-toast` and runs `flatpak-spawn --host`, because
OpenVR rejects sandboxed clients (PID namespace, see the manifest). Outside the Flatpak it runs
directly.

### Toasts

- Anchored to the HMD, 1.2 m out along the middle of the view, the newest one's centre 0.18 m
  (about 8.5°) below it. The middle of the view comes from the lenses' projection, as for the VR
  panel (see "Placement" there): about 15° below the HMD's forward axis on the Steam Frame.
- About 0.45 m wide.
- At most 3 are stacked, newest on top.
- They fade in and out over about 250 ms, and their sort order is high.

### Exit

- On `VREvent_Quit` the helper acknowledges the quit, prints `vr_quit` and exits 0.
- It also exits 0 on stdin EOF or `quit`.

## Environment for every adb/scrcpy child process (never the Qt process)

```
ANDROID_ADB_SERVER_PORT=5038
ADB=<bundled adb path>
ADB_EMU=0                             # don't probe 127.0.0.1:5555-5585 (Lepton instances)
ADB_MDNS_AUTO_CONNECT=0               # we discover and connect ourselves
HOME=<app data dir>/adb-home          # adb stores its key in $HOME/.android
LOGNAME=scrcpy4Frame
USER=scrcpy4Frame
HOSTNAME=SteamFrame
SCRCPY_SERVER_PATH=<bundled server>   # Flatpak only
SDL_VIDEODRIVER=x11                   # Game Mode (gamescope) only
```

## VR mirror panel: `vr-mirror` helper

A native helper that shows the device as a SteamVR overlay panel shaped like its screen. It runs on the
**host** (OpenVR rejects sandboxed clients, see the manifest), started with
`flatpak-spawn --host --watch-bus`, from a copy at `~/.var/app/<id>/data/scrcpy4frame/vr-mirror`.
`vr-mirror.vrmanifest` is copied next to it.

### Who does what

1. **The Python app (sandbox)** sets up the scrcpy session over its private adb server:
   1. `adb push /app/share/scrcpy/scrcpy-server /data/local/tmp/scrcpy4frame-server.jar`
   2. `adb forward tcp:0 localabstract:scrcpy_<SCID>`, where `<SCID>` is a random 31-bit value as 8 hex digits.
   3. It starts the server with `adb shell` and keeps it running:

      ```
      CLASSPATH=/data/local/tmp/scrcpy4frame-server.jar app_process / com.genymobile.scrcpy.Server <VERSION> \
        scid=<SCID> log_level=info tunnel_forward=true audio=false control=true video_codec=h264 \
        max_size=<N> video_bit_rate=<bps> max_fps=<fps> keep_active=true clipboard_autosync=false
      ```

      `<VERSION>` must equal the bundled scrcpy version (`scrcpy --version`, for example `5.0`).
   4. Audio casting is off at every launch and never saved. Home's "Audio casting" button runs the
      official client audio-only (`scrcpy --no-window --no-video --no-control --require-audio …`,
      Android 11+) from the panel's first frame, and stops it when switched off. The phone is silent
      while it runs. Its `--audio-buffer` is Settings › Audio › "Audio buffer" (20, 30, 40 or 50 ms;
      50 by default); changing it while casting stops the client and starts it again on the new buffer. In window mode scrcpy itself gets `--no-audio`, and the audio client starts once
      the window's `[server]` log appears, so the two never push scrcpy's server at the same time.
2. **`vr-mirror` (host)** connects to `127.0.0.1:<port>`. The sandbox shares the host's network
   namespace, so the adb forward is reachable from the host.
   1. It opens **two** sockets in order: video, then control.
   2. On the video socket it reads the 1-byte dummy (forward tunnel) and the 64-byte device name.
   3. Then the stream follows the scrcpy 5.0 format:
      - the u32 codec ID
      - 12-byte headers: a **session** packet (MSB set; `u32 flags, u32 width, u32 height`) for each
        capture session or rotation, otherwise a **media** packet header (`u64 pts|flags`: bit62
        config, bit61 key frame; then a `u32` size and the payload)
   4. **Retrying the connection:** until the dummy byte arrives the server may not be listening
      yet, so it retries the connect for up to 10 s.
   5. It decodes H.264 with a statically linked FFmpeg, converts to RGBA and uploads through
      Vulkan with `SetOverlayTexture`. **Never `SetOverlayRaw` per frame:** at video rate it
      flickers, leaks and fails with `RequestFailed` on the Frame.

### Control messages it sends (big-endian, scrcpy 5.0 `control_msg.c`)

| Message | Bytes |
|---|---|
| touch (32 B) | `[2][action u8][pointer_id i64 = -2 (generic finger)][x i32][y i32][w u16][h u16][pressure u16 fixed-point][action_button u32 = 0][buttons u32 = 0]` |
| scroll (21 B) | `[3][x i32][y i32][w u16][h u16][hscroll i16][vscroll i16][buttons u32]`. Scroll values are fixed-point of value/16, clamped to [-1, 1]. |
| back (2 B) | `[4][action]`, sent as down then up |
| keycode (14 B) | `[0][action][keycode u32][repeat u32][metastate u32]`, for HOME = 3, APP_SWITCH = 187, POWER = 26 |
| display power (2 B) | `[10][on u8]` |

- Touch actions are DOWN = 0, UP = 1, MOVE = 2.
- `w × h` must equal the current session's video size. The server drops mismatched events, which
  handles rotation races.

### Panel behavior

- **One panel overlay.** Its Vulkan texture is the device image (top left) plus transparent margins that
  hold the **controls**, styled after the Steam Frame's own windows (`src/panel_chrome.cpp`, drawn
  with `src/ui_draw.cpp`):
  - a margin column right of the image (wide enough for the open More menu's last button, which
    can reach past the image in portrait) and, under it, a **button row** (a button is 13% of the
    image's short side across), a **bar row** (9% of the short side) and the few rows the open
    More menu reaches into;
  - **Back**, **Home**, **Recents**, **Hide** and **More** (three vertical dots): round buttons
    centered under the image, in two groups: the device keys (Back, Home, Recents), then after a
    wider gap the panel controls (Hide, More). Dark disc with a white icon; white disc with a dark
    icon while hovered or pressed;
  - the **More menu:** More opens a tray under it: a dark capsule from More down to a row of
    the same round buttons, the first right under More and the rest going right, over the drag
    bar's row. In portrait the row reaches past the image's right edge, so the margin column is
    that wide (1062 px of texture for an 864 px wide image, about 3 cm right of the phone at
    Medium). More closes it again. Left to right it holds:
    - **Theater**, whose icon shows what pressing it does: expand (four corners pointing out)
      while theater is off, shrink (corners pointing in) while it's on;
    - **Recenter** (the app's recenter icon: a ring with four ticks), which does what `recenter`
      does, countdown included;
    - **Audio casting**, whose icon shows what's true now, as Home's button does: headphones
      while casting, headphones with a slash while not. No accent colour. It's faint and can't be
      pressed while casting starts or stops, or if the device can't cast (the app's `audio`
      command says which). Pressing it emits `audio_cast` and leaves the menu open, so the change
      shows.
    Theater and Recenter close the menu. A click anywhere else on the panel closes it and still
    does what it does there. Closing the SteamVR dashboard, hiding the panel and `recenter` close
    it too. The menu's rows are always part of the texture (drag bar while it's closed): changing
    the texture's size blanks the overlay for a moment;
  - the **drag bar:** a translucent white pill under the buttons, at the bottom;
  - the **resize bracket:** a rounded corner (two arms, the same stroke as the pill) around the
    device image's bottom-right corner, just outside it.
  The pill and bracket brighten while hovered or in use. The only other overlay is the
  dashboard button row (below), and **nothing uses `SetOverlayRaw`** (see Exit below).
- **Placement:** world-locked. It starts 0.65 m along the line of sight, facing the user, every
  session: the app doesn't send `pose` in `connect`, so pressing Connect always opens it there.
  (The helper still accepts a `pose`, and starts there instead.)
  - **The line of sight** runs through the middle of the view, which isn't always the HMD's
    forward axis. It comes from both eyes' `GetProjectionRaw` (averaged; OpenVR's "bottom" is
    the upward bound). The Steam Frame's lenses reach 49.6° up and 59.9° down, so its middle
    is about 15° below the forward axis. On the forward axis, the panel showed that much too
    high: its bottom edge where the user looked. The helper logs the angle at startup.
  - **Facing the user** works like the Steam Frame's dashboard (`facing_upright`). The panel turns
    about the vertical to face the line from the head to the middle of the phone screen, which
    is what the user looks at, and stands upright while that line is within 25° of level
    (`kUprightWithinRadians`, a first guess to tune by feel). Past 25° above or below the head
    it's square to that line. Crossing 25° either way starts it turning between the two, eased
    in and out over 0.3 s (`kTiltSeconds`, by feel; `panel::TiltAnimation`), not snapping. Crossing
    back mid-turn changes nothing until that turn is done, then it turns back if it's still on
    the other side, so crossing again before then cancels that. A turn under way when the panel
    is let go finishes, facing the head as it was then. A tilt eased in with the angle (none at
    25°, square overhead or underfoot) looked too little tilted in the headset. The middle of the
    Frame's view is 15° down, so a recentered panel stands upright unless the user looks well
    up or down. The middle of the phone screen isn't the overlay's centre: the controls under
    the screen and the bracket's margin on its right put the centre lower and to the right.
    Turned toward the overlay's centre, the screen's left side leaned away (about 0.4° for
    Medium in portrait). X stays level, so the panel never rolls.
  - It's then **turned 2° left side toward the user** (`kSideTurnRadians`, chosen by feel after
    trying 4°). Squared exactly, it
    measured square within 0.5° on the Frame, like SteamVR's dashboard bar, yet looked turned
    left side away to either eye at any distance. The turn corrects what the user sees.
    Of the two level X axes, the one closer to the head's right is used, so a panel overhead
    isn't upside down for someone lying back.
- **Size:** the device image's long side is `panel_size_m` (clamped to 0.12–2 m; 0.12–8 m in
  theater), so portrait panels are about that tall and landscape panels about that wide. The app sends Settings'
  Overlay size (0.30 m for Medium, the default) every session, whatever size the last one was
  left at. On rotation the panel is reshaped around the same center, except in theater, where
  the phone screen's bottom edge stays put (so a screen on the floor stays there).
- **Recenter** (`recenter`, Home's Recenter button, the More menu's Recenter). Only Recenter
  counts down: showing a hidden panel (Home's "Hidden" button, sending `recenter` with
  `"now":true`; the dashboard button) does the same at once, except in theater, where it shows
  the panel where it was hidden:
  - **Countdown first:** a round badge counts 3, 2, 1, one second each, in the middle of the
    view, where the panel will land, so the user can look where they want it. It's a third
    overlay, `Countdown` (`src/countdown.cpp`): 9 cm across, 1 m along the middle of the view,
    attached to the HMD, with no input (the laser passes through it) and sort order 1, over the
    panel. Same style as the buttons: a dark disc with a white digit.
  - **Then** the panel moves onto the user's line of sight, facing them, and shows if it was
    hidden. Only the position changes: 0.65 m away, or 3.8 m in theater, which stays on.
  - Another recenter while it counts doesn't restart it. Hiding the panel, carrying or resizing it,
    or turning theater on cancels it: the user has placed it. If the countdown's overlay can't be
    created, recenter happens at once.
  - It reports `placement`.
- **Resizing:** drag the bracket. Like the Steam Frame's windows, the centre stays where it was
  when the bracket was grabbed and the panel grows or shrinks evenly on every side, in theater
  too (a screen on the floor sinks into it as it grows and lifts off as it shrinks). The grabbed
  point follows the laser (its position comes from the overlay's own mouse events, so it matches
  the beam), projected onto the line from the centre through it since the aspect ratio is fixed.
  Dragging to the centre or past it makes it as small as it goes.
  - **On release,** if the size changed, the panel turns to face the user again about its
    centre: the phone screen's centre has moved, so it may now cross 25° and turn over
    `kTiltSeconds` to upright or square. Tilted about its foot, a tall theater screen close by
    would lean over the user: a 9 m portrait screen 2 m away tilts about 67°.
  - `placement` then reports the pose and the new `panel_size_m`.
- **Moving it:** press the bar with the laser and the panel is attached
  to that controller (`SetOverlayTransformTrackedDeviceRelative` with the panel's pose in the
  controller's space), so SteamVR moves it smoothly with the hand. Every tick, that relative pose
  is recomputed from the controller's and the head's poses so the panel keeps facing the user
  wherever it's carried. On release it is pinned world-locked where it is, and `placement`
  reports the new pose.
  - **In theater** it moves the same way, off the floor too. Only the stick's range is larger:
    0.25–8 m from the head.
- **Theater:** the More menu's Theater button sets the long side to `theater_size_m` (3.5 m;
  not a Settings choice) and moves the panel 3.8 m straight ahead along the head's level heading,
  **standing on the floor:** upright, the phone screen's bottom edge at the standing universe's
  floor (y = 0), the height of the Steam Frame's own theater screen, turned about the vertical
  to face the user. The controls under the screen (buttons, drag bar and the menu's rows) go
  below the floor, where they still show and work. It starts like this every time, wherever the
  panel was.
  - **While it's on** the panel can be carried anywhere, off the floor too, and resized up to
    8 m. `recenter` keeps theater on and puts the panel on the line of sight 3.8 m away, like a
    normal recenter. Showing it after Hide (Home's "Hidden" button, the dashboard button) is the
    exception: it comes back where it was hidden, not in front of the user.
  - **Ending it:** only pressing Theater again (it shows shrink while theater is on), or
    `theater` with `"on":false`. The panel goes back to its size from before, in front of the user
    at once (no countdown).
  - **From the app:** `theater` turns it on or off, as the button does. Turning it on also shows
    a hidden panel, and so does ending it. Each change, from either side, emits `theater`, which
    Home's "Theater on" / "Theater off" button shows (accented while on, like Home's other
    toggles, with the More menu button's icon: expand while off, shrink while on). If theater
    can't start (no frame yet, or no HMD pose), nothing is emitted.
  - Both report `placement`.
  - **Never carried over:** a new session always starts with theater off, in front of the user
    at Settings' Overlay size. While theater is on, `placement` reports an empty `pose` and the
    size from before theater.
- **No laser while moving or resizing:** OpenVR can hide the laser's dot on an overlay
  (`HideLaserIntersection`) but not its beam. Outside the SteamVR dashboard the laser comes from
  our own `MakeOverlaysInteractiveIfVisible`, so while the panel is carried or resized that flag
  is switched off: SteamVR draws neither beam nor dot. Meanwhile overlay events are ignored, and
  the hand's trigger, stick and pointing pose are read through **SteamVR Input**
  (`vr-mirror-actions.json` next to the vrmanifest, which names it in `action_manifest_path`;
  default bindings for `frame_controller` in `vr-mirror-bindings-frame-controller.json`):
  - the action set `/actions/panel` is updated every tick, restricted to that hand, at overlay
    global priority (`k_nActionSetOverlayGlobalPriorityMin`), so the game doesn't get the
    trigger and stick meanwhile;
  - `/in/hold` (trigger click): its release ends the drag or resize, and the laser comes back;
  - `/in/depth` (thumbstick): forward = farther, 0.2 dead zone, then a squared curve up to
    1 m/s, applied every tick;
  - `/in/aim` (`/pose/tip`): when resizing, the corner moves by as much as the point it aims at
    on the panel's plane moved since the start.
  - **Fallbacks** keep the laser path below: inside the dashboard (its laser can't be switched
    off), without the action files or handles, or for a hand that isn't left/right. If the
    trigger isn't reported within 0.3 s (or stops being reported), the laser comes back and the
    action goes on with overlay events. If a drag ends twice without the trigger ever showing as
    held, the laser stays on for the rest of the session.
- **Scrolling:** the overlay takes only **smooth** scroll events (`SendVRSmoothScrollEvents`).
  Discrete scroll is off: it moves in steps and ticks the controller's haptics on every one. On
  the image, each smooth event is sent on as a scroll at the pointer, so the device scrolls
  smoothly as the stick is held, as fast as the app's own pages:
  - Through gamescope one unit of smooth scroll is one wheel click for the app (`angleDelta` 120),
    and Home and Settings scroll 144 px of their 968 visible px per click
    (`scrolling.PAGE_SHARE_PER_CLICK`, sent as `scroll_screens_per_click`).
  - Android scrolls 64 dp per click, so the device gets `scroll_screens_per_click` ×
    (its visible screen height in dp) / 64 clicks per unit, both axes. The height comes from
    `screen_long_dp` (the app reads `wm size; wm density` when the session starts, overrides
    first) and the current orientation; 900 dp is assumed if it's unknown.
- **Depth while held (laser on):** the stick glides the panel away or closer along the eye-to-panel line
  (0.25–3 m; 0.25–8 m in theater), with no haptic ticks. Each smooth scroll unit adds 0.05 m to a
  distance still to travel (at most 0.15 m). Every main-loop tick covers part of it (an exponential
  glide, time constant 60 ms, at most 2 m/s), so the motion is smooth whatever the event rate.
  The laser's dot is hidden on the panel (`HideLaserIntersection`) while it is held or resized.
  On release, the log says whether the laser was off, the distance moved, and the scroll event
  count, for tuning.
- **Hiding:** the Hide button (or `hide`) hides the overlay. The device keeps streaming (and casting
  its audio, if on). Frames are still decoded but not uploaded. Laser input stops, and so does
  `kMakeInteractiveIfVisible`'s hold on game input. `show` (or `recenter`) shows it again with the
  latest frame. Each change emits `visibility`. A new session always starts visible.
  - **In the app:** Home shows "Shown" / "Hidden" under Controls. Pressing "Shown" hides the panel;
    pressing "Hidden" sends `recenter` with `"now":true`, so it shows in front of the user at once,
    with no countdown (in theater, where it was hidden). While
    the panel is hidden, that device's forwarded notifications pop up again (the "Don't pop up while
    the overlay is visible" setting, `suppress_while_visible`, only applies while it is shown).
    Opening one of its notifications shows the panel again.
- **Dashboard button:** while the session streams with the panel hidden and `dashboard_button` is
  on, a round button with a phone icon floats under SteamVR's dashboard bar whenever the dashboard
  is open. Clicking it does what `recenter` does, at once with no countdown since it's a quick
  view: the panel shows in front of the user, emitting `visibility` and `placement`. In theater
  it shows where it was hidden instead, emitting only `visibility`. The button goes away once the
  panel shows.
  - It's a second overlay, `DashBar` (`src/dash_bar.cpp`): a generic row of round buttons, each
    with an id and an icon, so more buttons only need adding to the list `main.cpp` offers. It
    has the panel's button style and the same press-and-release rules, and uploads through the
    panel's `SteamFrameVulkanTexture`, which keeps one image set per overlay on a single Vulkan
    device. A second device in the process makes OpenVR's client throw `std::bad_alloc` (exit
    134) on the next upload.
  - **Anchor:** the bar is the overlay `valve.steam.gamepadui.bar`. Its key isn't part of the
    OpenVR SDK (Desktop+ and other overlays rely on it too); if it's missing, the row never shows.
    The bar is a scene-graph panel whose width can't be read and grows with open tabs, so only its
    centre is used: `GetTransformForOverlayCoordinates` at the middle of its mouse area, which
    includes the dashboard's scale (0.369 on the Frame).
  - **Placement** (`src/dash_math.cpp`, tuned in the headset): the bar leans back about 40° from
    the dashboard panel (measured against `valve.steam.gamepadui.main` when Steam's tab is shown),
    and the dashboard pitches toward the head when placed high or low. In the dashboard's frame
    (the bar's with the lean undone) the row's centre sits 11 cm below the bar's centre and 6 cm
    out toward the user, then 2 cm along the line to the head. It faces the head with no roll.
    The button is 6 cm across. Distances and sizes scale with the bar's scale.
  - **Following:** OpenVR 2.x has no overlay-relative transforms, so the row is placed every tick
    (at most every 4 ms; every 50 ms while hidden). The bar's pose updates every compositor frame,
    about 20–36 ms behind the controller, so the row trails a fast dashboard drag slightly.
    Attaching it to the controller during drags was tried and jittered. The head position it
    faces is only refreshed when it appears and while the bar moves, so it doesn't swivel when
    the head moves.
  - **In the app:** Settings › Display › "Show quick-view button" (on by default) sets
    `dashboard_button`, live for a running session.
- **Input:** `VROverlayInputMethod_Mouse` with the mouse scale set to the texture size. Mouse Y is
  bottom-up, so the area under the image is `y < strip_h` and the touch on the device is
  `y_touch = h - (y - strip_h)`. Buttons act on release, and only if the laser is still on them.
  While one control is in use (or a finger is down on the device), the others don't react.

| Laser action | Device action |
|---|---|
| left button down / move / up on the image | finger touch down / move / up |
| left button down on the bar | grab (move the panel) |
| left button down on the bracket | resize the panel |
| click on the Back button | Back |
| click on the Home button | Home |
| click on the Recents button | App switch (Recents) |
| click on the More button | open or close the More menu |
| click on Theater (More menu) | theater on; when it's on, end it |
| click on Recenter (More menu) | as `recenter` (after the countdown) |
| click on Audio casting (More menu) | `audio_cast` with the opposite of what it shows |
| click elsewhere while the More menu is open | close the menu, then the usual action |
| click on the Hide button | hide the panel (mirroring continues) |
| click on the dashboard button | show the hidden panel in front of the user at once (as `recenter`, no countdown); in theater, where it was |
| right button | Back |
| middle button | Home |
| smooth scroll on the image | scroll at the last pointer position |
| smooth scroll while grabbing | depth (see above) |

- **Registration:** `AddApplicationManifest(<dir>/vr-mirror.vrmanifest)` then `IdentifyApplication(pid,
  "io.github.xhuytox.scrcpy4frame.mirror")`. The manifest has `is_dashboard_overlay: true` and
  `binary_path_linux_arm`. Without it SteamVR sends the overlay a quit request when a game starts.

### stdin commands (JSON lines)

```json
{"cmd":"connect","host":"127.0.0.1","port":40123,"title":"Pixel 8","turn_screen_off":true,"panel_size_m":0.30,
 "theater_size_m":3.5,                       // optional, default 3.5 (0.10–8): the long side when theater starts
 "scroll_screens_per_click":0.1488,          // optional: without it, one wheel click per unit of smooth scroll
 "screen_long_dp":914.3,                     // optional: the device screen's long side in dp (see Scrolling)
 "dashboard_button":true,                    // optional, default false: see "Dashboard button"
 "pose":"1,0,0,0.1,0,1,0,1.5,0,0,1,-0.6"}   // optional: 12 floats, row-major 3x4, from an earlier `placement`. The app never sends it
{"cmd":"key","key":"back"}          // back | home | app_switch | power
{"cmd":"recenter"}                  // after a 3 s countdown, on the user's line of sight again (shows it if hidden; theater stays on)
{"cmd":"recenter","now":true}       // the same at once, no countdown ("now" optional, default false): Home's "Hidden" button.
                                    // In theater a hidden panel shows where it was instead
{"cmd":"hide"}                      // hide the panel, keep streaming
{"cmd":"show"}
{"cmd":"theater","on":true}         // theater on or off, as the More menu's button; shows the panel if hidden
{"cmd":"dashboard_button","enabled":false}   // offer the dashboard button or not, any time
{"cmd":"audio","on":true,"enabled":true}     // what the More menu's audio button shows: casting or not, and whether it
                                             // can be pressed ("enabled" optional, default true). Before any: off, enabled
{"cmd":"quit"}
```

### stdout events (JSON lines, flushed)

```json
{"event":"ready"}                                  // VR initialised, overlays created
{"event":"unavailable","reason":"VRInitError_..."} // then exit 3
{"event":"connected","device":"SM-G981U"}
{"event":"size","width":864,"height":1920}
{"event":"first_frame"}
{"event":"stats","fps":59.8,"decode_ms":3.1,"upload_ms":1.4}   // every ~5 s
{"event":"placement","pose":"<12 floats>","panel_size_m":0.300}  // after a drag, resize, recenter or theater; the app ignores it ("" pose and the size from before theater while theater is on)
{"event":"visibility","visible":false}             // hidden or shown (Hide button, hide/show, recenter)
{"event":"theater","on":true}                      // theater turned on or off (More menu or `theater`); a session starts with it off
{"event":"audio_cast","on":true}                   // the More menu's audio button: the app turns casting on (off) and answers with `audio`
{"event":"disconnected","reason":"..."}            // stream ended; exit 0 shortly after
{"event":"error","message":"..."}
```

### Exit

- On stdin EOF, `quit` or SIGTERM: close the sockets (the server then exits), then a **safe
  teardown**: hide the overlay and `ClearOverlayTexture`, wait 300 ms, `DestroyOverlay`, wait 300 ms,
  `VR_Shutdown`, and only then release Vulkan. The dashboard button row goes first the same way
  (its 300 ms wait only if it ever had a texture). Exit 0.
  - **Why the waits:** SteamVR's compositor copies overlay pixels out of the client's shared
    memory asynchronously (`SetOverlayRaw`, and the Vulkan transfer ring buffer). If the client
    exits with a copy pending, the compositor reads freed memory and dies with **SIGBUS**
    (seen with SteamVR 2.18).
  - The app therefore never force-kills the helper early: it allows 5 s before SIGTERM. `vr-toast`
    has the same waits.
- On `VREvent_Quit`: acknowledge, print `disconnected`, exit 0.
- `--no-vr` (dev/test): no OpenVR. Connects, decodes and prints the same events. With
  `--snapshot <file.ppm>` it writes the first decoded frame. With `--tap X,Y` it sends one tap
  after the first frame.

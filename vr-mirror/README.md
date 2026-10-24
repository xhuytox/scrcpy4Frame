# vr-mirror

`vr-mirror` shows an Android device as a SteamVR overlay panel shaped like its screen on the Steam Frame.
The panel reshapes when the device rotates. Laser-pointer input on the panel reaches the device as
touches.

scrcpy4Frame starts the scrcpy 5.0 server on the device and an adb forward. `vr-mirror` then
connects to that forward and does the rest:

- It decodes the H.264 stream with a static FFmpeg.
- It uploads the frames through Vulkan with `SetOverlayTexture`.
- It sends scrcpy control messages back to the device.

It runs on the host via `flatpak-spawn --host`, because OpenVR rejects sandboxed clients. The
JSON-lines protocol on stdin/stdout, the wire format and the panel behaviour are specified in
`docs/CONTRACTS.md`, under "VR mirror panel".

- **Threads.** The main thread owns stdin/stdout and every OpenVR call, with a 5 ms tick and a
  wake-up pipe. A network thread connects, retrying for up to 10 s until the server's dummy byte
  arrives. It then demuxes and decodes into a latest-frame mailbox (three RGBA buffers; the
  newest frame wins). Control messages are written under a mutex.
- **Panel.** It is world-locked: 0.65 m along the line of sight (the middle of the view, from the
  lenses' projection), facing the user, every session (or at a `pose` from `connect`, which the
  app doesn't send). The long side is the app's Overlay size (0.30 m by default) unless resized.
  It is one overlay whose Vulkan texture is the device image plus its controls: Back, Home,
  Recents, Hide and More buttons, a drag bar under them, a resize bracket and the More menu with
  Theater, Recenter and audio casting in a row (`src/panel_chrome.cpp`).
  - It faces the user like the Steam Frame's dashboard: turned about the vertical, upright,
    until it's more than 25° above or below the head, then square to them. Crossing 25° turns it
    between the two over 0.3 s rather than snapping.
  - Pressing the bar attaches the panel to the controller and releasing pins it. While held, it
    keeps turning to face the user and the stick moves it nearer or farther, with no haptics.
  - Theater stands it on the floor 3.8 m straight ahead (the phone screen's bottom edge on the
    floor, like the Frame's own theater screen, with the controls below it) at a 3.5 m long side,
    every time it starts. In theater it can be carried anywhere, resized up to 8 m and
    pushed up to 8 m away, and recenter keeps theater on. Showing it after Hide (Home, the
    dashboard button) brings it back where it was, not in front of the user. Pressing it again (shrink) brings it
    back in front of the user at its size from before theater. The app's `theater` command does
    the same (Home's Theater button), and each change is reported as `theater`.
  - Recenter counts 3, 2, 1 on a small head-locked badge (`src/countdown.cpp`) in the middle of
    the view first, so the user can look where the panel should go.
  - The menu's audio button shows what the app's `audio` command says (casting or not, and
    whether it can be pressed) and asks the app to cast with an `audio_cast` event.
  - The stick scrolls the device with smooth scroll events only: discrete ones tick the haptics.
  - Clicking the stick with the laser on the device turns zoom mode on, with a ring marking that
    spot. The stick then pinches two fingers there (forward zooms in) instead of scrolling, the
    trigger held on the device turns them about it as the laser goes round, and a second click
    turns it off. Both come through the laser (the click as the right mouse button,
    the stick as smooth scroll), and the pinch math is `panel::Pinch`.
  - While moving or resizing, the laser is switched off (no beam, no dot), and the trigger, stick
    and pointing pose come from SteamVR Input (`manifest/vr-mirror-actions.json`, bindings for
    the Frame controller). Without them it falls back to the laser and smooth scroll
    (`panel::DepthGlide`).
  - Dragging the bracket resizes the panel around its centre, which stays where it was when the
    bracket was grabbed (in theater too), like a Steam Frame window. On release it turns to face
    the user again, so it tilts if its new size needs it to.
  - The new pose and size are reported as `placement` (the app ignores it). In theater it's no
    pose and the size from before theater. Hide/show are reported as `visibility`.
  - `VrPanel::kMakeInteractiveIfVisible` in `src/vr_panel.h` decides whether the panel takes laser
    input outside the dashboard.
- **Dashboard button.** While the panel is hidden, `DashBar` (`src/dash_bar.cpp`) floats a
  button under SteamVR's dashboard bar that shows it again, like Recenter but with no countdown
  (in theater, where it was).
  `DashBar` is a generic
  row of round buttons (id + icon, drawn with `src/ui_draw.cpp` like the panel's controls) that
  follows the bar every tick. Its placement math is in `src/dash_math.cpp`, which has unit
  tests.
- **No `SetOverlayRaw`, and a slow exit.** SteamVR copies overlay pixels out of our memory
  asynchronously, and exiting mid-copy crashes its compositor (SIGBUS). So shutdown clears the
  texture, waits, destroys the overlay, waits, and only then calls `VR_Shutdown`.

## Build

```sh
# Steam Frame / Linux: OpenVR from a static checkout or an install prefix.
cmake -S vr-mirror -B vr-mirror/build -DOPENVR_ROOT=/path/to/openvr
cmake --build vr-mirror/build && ctest --test-dir vr-mirror/build
```

| Option | Default | Meaning |
|---|---|---|
| `VR_MIRROR_WITH_OPENVR` | ON on Linux | OFF builds a binary that only supports `--no-vr`, for example on a Mac |
| `OPENVR_ROOT` | `$OPENVR_ROOT` | OpenVR checkout or prefix; a static `libopenvr_api.a` is required |
| `VULKAN_INCLUDE_DIR` | found automatically | Vulkan headers only; `libvulkan.so.1` is dlopen()ed at run time |
| `VR_MIRROR_FFMPEG_STATIC` | OFF | Link the `.a` files of the FFmpeg that pkg-config finds; the Flatpak sets it ON with `CMAKE_PREFIX_PATH=/app/vrmirror-ffmpeg` |
| `VR_MIRROR_STATIC_RUNTIME` | ON | `-static-libstdc++ -static-libgcc` on Linux |
| `VR_MIRROR_BUILD_TESTS` | ON | Builds `protocol_test` and registers it with CTest |

FFmpeg (`libavcodec`, `libavutil`, `libswscale`) is found with pkg-config. The Flatpak build is
in `flatpak/modules/vr-mirror.yml`.

## Dev mode without VR (for example on macOS)

```sh
PKG_CONFIG_PATH=/opt/homebrew/opt/ffmpeg/lib/pkgconfig \
  cmake -S vr-mirror -B /tmp/vrm -DVR_MIRROR_WITH_OPENVR=OFF && cmake --build /tmp/vrm
# Start the server on the device and run `adb forward tcp:27183 localabstract:scrcpy_<SCID>`
# (see CONTRACTS.md). Then:
(echo '{"cmd":"connect","host":"127.0.0.1","port":27183}'; sleep 10) |
  /tmp/vrm/vr-mirror --no-vr --snapshot /tmp/first.ppm --tap 300,600
```

- **`--no-vr`** prints the same events (`ready`, `connected`, `size`, `first_frame`, `stats`,
  `disconnected`) without OpenVR.
- **`--snapshot FILE`** writes the first decoded frame as a binary PPM.
- **`--tap X,Y`** sends one touch down and up at that video pixel after the first frame.
- **Exit codes:**
  - 0: normal exit, including `disconnected`
  - 2: bad arguments
  - 3: `unavailable`, meaning no SteamVR
  - 4: fatal error, for example Vulkan initialisation failed
- **`VR_MIRROR_DEBUG=1`** turns on debug logs on stderr. stdout carries only protocol JSON.

## Attribution

`src/vulkan_texture.*` is adapted from MIT-licensed code. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

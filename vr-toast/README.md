# vr-toast

`vr-toast` is a small OpenVR overlay helper. It shows notification cards as toasts anchored to the HMD while SteamVR runs. scrcpy4Frame starts it as a child process and talks to it in JSON lines over stdin/stdout. The protocol is in `docs/CONTRACTS.md`.

- **When SteamVR isn't running**, it prints `{"event":"unavailable",...}` and exits with code 3. Otherwise it prints `ready`.
- **Every `shown`** is later paired with exactly one `expired`: the toast timed out, was hidden, or was pushed out by a fourth toast. Showing an id that is already on screen replaces its image and restarts its timer.
- **Inside the Flatpak**, the helper points OpenVR at the host's `~/.config/openvr/openvrpaths.vrpath` (via `VR_PATHREG_OVERRIDE`) when the sandbox's own config dir has none.

Build. The OpenVR library must be static. libstdc++ and libgcc are linked statically too, so the same binary also runs on the host via `flatpak-spawn --host`:

```sh
# in an OpenVR checkout: cmake -B build -DBUILD_SHARED=OFF -DUSE_LIBCXX=OFF && cmake --build build
cmake -S vr-toast -B vr-toast/build -DOPENVR_ROOT=/path/to/openvr
cmake --build vr-toast/build      # -> vr-toast/build/vr-toast (the dev path the app looks for)
```

Quick manual test with SteamVR running:

```sh
(python3 -c 'import base64,json;print(json.dumps({"cmd":"show","id":"t","width":64,"height":16,"rgba":base64.b64encode(b"\xff\x80\x00\xff"*64*16).decode(),"duration_ms":3000}))'; sleep 4) | vr-toast
```

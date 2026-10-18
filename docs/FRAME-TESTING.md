# Testing on the Steam Frame

Set `FRAME=<user>@<frame-host>`. SSH needs Developer Mode turned on, with a password set.

## 0. Facts about the device (no install needed)

```sh
scripts/frame-diagnose.sh $FRAME
```

| Question | What to look for | If it's "no" |
|---|---|---|
| Is the USB-C port host-capable? | `lsusb` lists a USB keyboard plugged into the Frame; `/sys/class/typec/*/data_role` shows `[host]` | The cable setup (Android ≤10) can't work. Hide it or document "use a PC once". |
| Does the uaccess rule for adb exist? | systemd ≥ 258, or an android/adb rule in `/usr/lib/udev/rules.d` | The app shows the one-time udev command (wizard step `usb_perms`). |
| Is a firewall active? | empty `nft list ruleset` | Allow UDP 5353 (mDNS) inbound. |
| Is Lepton exposing ADB? | ports 5555–5599 listening | Fine: the app sets `ADB_EMU=0` and filters local serials. |
| Is the SteamVR runtime present? | `openvrpaths.vrpath` plus `bin/linuxarm64/vrclient.so` | The VR popups fall back to desktop pop-ups. |

## 1. Build and install

```sh
scripts/build-companion.sh
scripts/frame-build.sh $FRAME
ssh $FRAME 'which adb scrcpy; flatpak run io.github.xhuytox.Scrcpy4Frame cli diagnose'
```

`which` should find nothing. `cli diagnose` should list `/app/bin/adb`, `/app/bin/scrcpy` and
`companion.apk`.

## 2. mDNS inside the sandbox

Turn on Wireless debugging on the device, then:

```sh
ssh $FRAME flatpak run io.github.xhuytox.Scrcpy4Frame cli discover --seconds 20
```

- **Expected:** a `connect` line for the device. Opening "Pair device with pairing code" adds a
  `pairing` line.
- **If nothing shows:** compare with `avahi-browse -rt _adb-tls-connect._tcp` on the host. If avahi
  sees the device but the sandbox doesn't, it's a port-5353 conflict: check avahi's
  `disallow-other-stacks`.

## 3. Full wizard

Run each scenario in Desktop Mode, then again in Game Mode (added as a non-Steam game).

1. Wireless pairing from a clean state: run `flatpak run --command=rm … ~/.var/app/<id>/data/scrcpy4frame/adb-home -rf`, or forget the device and revoke debugging authorizations on the device.
2. Wrong code, then right code. The pairing window closing (screen locked) should send the wizard back a step.
3. Xiaomi/OPPO device: the input-control step appears and clears itself.
4. Companion opt-in: all four checklist items turn ✓. Then `adb shell cmd notification post -t Test tag Hello` from a second machine, or any real notification, should produce a pop-up.
5. Quick Settings tile is added, and the device shows it in the tile editor.
6. Test mirror: the scrcpy window takes focus fullscreen, and **Alt+Q** returns to the app. Audio starts off;
   Home's "Audio casting off" button turns it on (the device goes quiet and its sound plays on the Frame).

## 4. Coming back later

- Without the companion: turn Wireless debugging off. The device card says "Wireless debugging is off". Tap the tile on the device and the card turns "Ready" without any action on the Frame. After a reboot, use the tile too.
- With the companion (Android 11+), Wireless debugging is on only while the app runs. Watch it with
  `adb logcat -s S4F.NotifListener S4F.WakeServer S4F.WirelessDebugging` over USB from a second machine:
  - Close the app (Steam menu › Exit). Wireless debugging turns off on the device within a second or two (`frame-bye`).
  - Open the app with the device's screen off and locked. Wireless debugging turns on (`woken by …`) and the card turns "Ready" within about 10 seconds.
  - Reboot the device with the app open. It reconnects by itself on trusted Wi-Fi.
  - Put the headset to sleep, or take the device out of Wi-Fi range. Wireless debugging turns off about a minute later (`frame-timeout`), also with the device's screen off.
  - Turn Wireless debugging on by hand with the app closed: the companion leaves it on.
  - On a Xiaomi device with the app open, run `adb shell am force-stop io.github.xhuytox.scrcpy4frame.companion`.
    The card's companion line goes back to waiting. Within about 30 seconds it says notifications are on
    again (`listener connected`), with a note about Autostart. Where Android binds the listener again by
    itself, it comes back without the note.
- Close the app (Steam menu › Exit). `ssh $FRAME pgrep -a adb` should show nothing left running.

## 5. VR notifications

With a VR game running alongside the app, post a notification. A toast should appear in front of
you, slightly low, for about 5 seconds, with a short two-note chime (Settings › Play a sound; one
chime per burst of notifications, in every display mode). If not, check the app's logs (Settings › View logs) for
`vr-toast` lines. In the Flatpak the helper always runs on the host via `flatpak-spawn --host`,
because OpenVR can't initialise inside the sandbox's PID namespace. To check it by hand:
`echo '{"cmd":"quit"}' | ~/.var/app/io.github.xhuytox.Scrcpy4Frame/data/scrcpy4frame/vr-toast`
should print `{"event":"ready"}` while SteamVR is running.

## 6. Dashboard button for a hidden overlay

Mirror in VR, press Hide under the overlay, then open the SteamVR dashboard. A round button with a
phone icon should sit centred under the dashboard's bottom bar, facing you. It highlights white
under the laser, and clicking it shows the overlay in front of you straight away, with no
countdown, after which the button goes away. In theater it shows the overlay where it was
instead (see Theater in section 7). Also check:

- It follows when you drag the dashboard, including up near the ceiling and down near the floor,
  and it doesn't swivel when you only move your head.
- It stays put with several dashboard tabs open (the bar grows sideways, the button doesn't move).
- Settings › Display › "Show quick-view button" turned off removes it
  straight away, during the session.

If it never shows, start the app with `--log-level debug` (or `SCRCPY4FRAME_LOG=debug`): the
helper's own lines, tagged `dashbar`, then appear in the app's log as `vr-mirror: ...`.

## 7. Overlay placement, More menu and scrolling

Mirror in VR and check:

- **Opening:** the overlay appears centred where you're looking (its vertical middle, not its
  bottom edge, in the middle of your view), upright and turned to face you, at Settings › Overlay size. Move and resize it, then Disconnect and Connect again: it's back in
  front of you at Settings' size.
- **Carrying:** grab the bar and move the overlay around you, up high and down low. It keeps
  turning to face you, with no roll, and the stick still pushes it away or pulls it closer.
  Around eye level it stands upright and doesn't tip as you raise or lower it, like the
  dashboard. Once it's well above or below your head (past 25°) it tips over to face you
  square, turning smoothly over about a third of a second rather than snapping, and straight
  overhead it faces straight down at you. Wave it back and forth across 25°: each turn
  finishes before the next starts, and it ends upright or square to match where you left it,
  even if you let go mid-turn.
- **Resizing:** drag the bracket at the bottom right. The overlay grows and shrinks around its
  centre, evenly on every side, like a Steam Frame window, and the bracket stays under the
  laser. Drag it to the centre: it shrinks to its smallest. When you let go it turns to face
  you again. Its centre hasn't moved, so its tilt changes only a little: the phone screen's
  centre rises as the controls under it grow.
- **Hidden** (Home): with the overlay hidden, press it. The overlay shows straight away, with no
  countdown, centred in front of you, not where it was (in theater it's the other way round: see
  Theater below).
- **Theater** (Home): its icon is the More menu's Theater icon (corners pointing out). Press
  "Theater off". The overlay goes into theater as with the More menu's button, and Home's button
  turns accented, reads "Theater on" and shows shrink (corners pointing in), like the menu's. Press it again: the overlay
  comes back in front of you at its earlier size. Turning theater on or off from the More menu
  updates Home's button too. With the overlay hidden, Home's Theater button shows it.
- **Recenter** (Home): resize the overlay first. Press Recenter, then turn your head: a small round
  badge in the middle of your view counts 3, 2, 1 and follows your head. Then the overlay lands
  where the badge was, at its size: upright when you look roughly ahead, tipped toward you when
  you look well up or down.
  Recenter is the only thing that counts down.
  Pressing Recenter again during the countdown doesn't restart it. Grabbing the bar or the
  bracket during the countdown, or pressing Hide, cancels it.
- **Buttons:** Back, Home, Recents, then Hide and ⋮ (Hide left of ⋮).
- **More menu:** the ⋮ button opens a dark tray below it with three buttons in a row going
  right: Theater (under ⋮), Recenter, then audio casting. A click on the phone, the bar or
  another button closes it and still does its usual thing. Closing the SteamVR dashboard closes
  it, and so does Hide. In portrait the row reaches past the phone image's right edge: all three
  buttons are fully drawn and clickable.
  - **Recenter** closes the menu and counts down like Home's.
  - **Audio casting:** headphones with a slash while off. Press it: the button goes faint while
    casting starts, then shows headphones (white, no blue) and the phone's audio plays on the
    Frame. Home's button shows the same at each step. Press it again to stop. On a device below
    Android 11 it stays faint and does nothing. While casting, pick another Settings › Audio ›
    Audio buffer: the button goes faint for a moment and the audio comes back (the log shows the
    new buffer in "casting audio from … with a N ms buffer").
- **Theater:** the Theater button shows expand. Pressing it moves the overlay 3.8 m straight ahead,
  grown to 3.5 m on its long side, upright with the phone screen's bottom edge on the floor, at
  the same height as the Frame's own theater screen. The buttons and drag bar are below the
  floor but still show and work. The button then shows shrink. Check:
  - The bracket resizes it bigger or smaller around its centre, evenly on every side, as outside
    theater: the centre stays where it was when you grabbed the bracket, so the screen's bottom
    edge sinks below the floor as it grows and lifts off it as it shrinks. When you let go it
    turns to face you again about that centre.
  - Carrying it works as outside theater (it can leave the floor), and the stick pushes it up to
    8 m away. Rotate the device: the screen's bottom edge stays where it was.
  - Recenter keeps theater on (the button still shows shrink) and puts the overlay on your line
    of sight 3.8 m away, at its theater size.
  - Carry it somewhere, press Hide, turn around, then press Home's "Hidden" button: the overlay
    comes back where it was, not in front of you. Hide it again and use the dashboard's
    quick-view button: the same.
  - Turn theater off and on again: it's back on the floor at 3.8 m, wherever you'd moved it.
  - Pressing Theater again (shrink) brings the overlay back in front of you straight away, at
    its size from before theater.
- **Theater doesn't carry over:** stop mirroring while theater is on and mirror again: the overlay
  opens in front of you at its size from before theater, with theater off. Quit and reopen the
  app: it opens at Settings' Overlay size.
- **Scrolling:** with the laser on the device's screen, hold the stick up or down. The device
  scrolls smoothly and the controller doesn't buzz. It should move the same share of its screen as
  Settings does for the same stick push, in portrait and landscape.

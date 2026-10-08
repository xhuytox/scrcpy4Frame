# scrcpy4Frame Companion

Optional helper app for the Android device. Serves as a middle man to facilitate automatic
wireless reconnects and notification forwarding.

It keeps Wireless debugging on only while scrcpy4Frame runs on the Frame. The Frame wakes it over
UDP port 38490 when it starts, and it turns Wireless debugging off when the Frame says `bye` or goes
quiet for a minute. See "Wireless debugging only while the Frame app is around" in
`docs/CONTRACTS.md`.

- Package: `io.github.xhuytox.scrcpy4frame.companion`. minSdk 26, targetSdk 36.
- Platform APIs only. No AndroidX and no third-party runtime dependencies. The one library is
  View Binding's small runtime. Because `android.useAndroidX=false`, the build uses the
  non-AndroidX `com.android.databinding:viewbinding`.

## How the Frame sets it up

```sh
adb install -r companion.apk
adb shell pm grant io.github.xhuytox.scrcpy4frame.companion android.permission.WRITE_SECURE_SETTINGS
adb shell cmd notification allow_listener io.github.xhuytox.scrcpy4frame.companion/.NotifListener
adb shell cmd deviceidle whitelist +io.github.xhuytox.scrcpy4frame.companion
```

The release APK is signed with the build machine's debug key. If `adb install -r` fails with
`INSTALL_FAILED_UPDATE_INCOMPATIBLE` because the APK was rebuilt elsewhere, the Frame uninstalls
the companion, installs it again and repeats the grants.

## Building

From the repository root:

```sh
scripts/build-companion.sh      # -> dist/companion.apk (prints path and sha256)
```

The script needs a JDK 17+ and the Android SDK. It looks for the SDK in `ANDROID_HOME`, then
`~/Library/Android/sdk`, then `~/Android/Sdk`.

For a release, `scripts/release-build.sh` always rebuilds the APK and asks for the companion
version. If you give one, it sets `versionName` in `app/build.gradle.kts` and adds 1 to
`versionCode`. Android refuses to install an APK with a lower `versionCode` over a newer one.
Leave it blank to keep the current version.

To build and test from this directory instead:

```sh
./gradlew :app:testDebugUnitTest
./gradlew :app:assembleRelease
```

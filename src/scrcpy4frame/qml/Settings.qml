import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "components"

Flickable {
    id: settingsPage
    signal showLogs()
    readonly property var s: app.settingsView
    contentHeight: body.implicitHeight + 2 * theme.pad
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { width: 18 }
    WheelScroll { flickable: settingsPage }

    ColumnLayout {
        id: body
        width: Math.min(settingsPage.width - 2 * theme.pad, 1400)
        x: (settingsPage.width - width) / 2
        y: theme.pad
        spacing: theme.gap * 1.5

        Card {
            Layout.fillWidth: true
            SectionTitle { text: "Display" }
            ChoiceRow {
                topDivider: false
                label: "Overlay size"
                description: "The size the overlay opens at, in front of you. To resize it while it's open, drag the corner bracket at its bottom right."
                current: settingsPage.s.panel_size
                options: [{ value: "small", label: "Small" }, { value: "medium", label: "Medium" },
                          { value: "large", label: "Large" }]
                onChosen: (v) => app.setSetting("panel_size", v)
            }
            ToggleRow {
                label: "Show quick-view button"
                description: "While the overlay is hidden, a button under the dashboard's bottom bar brings it back in front of you."
                checked: settingsPage.s.dashboard_button
                onToggled: (on) => app.setSetting("dashboard_button", on)
            }
        }

        Card {
            Layout.fillWidth: true
            SectionTitle { text: "Video" }
            ChoiceRow {
                topDivider: false
                label: "Resolution"
                description: "Longest side, in pixels."
                current: settingsPage.s.max_size
                options: [{ value: 1280, label: "1280" }, { value: 1600, label: "1600" },
                          { value: 1920, label: "1920" }, { value: 2560, label: "2560" }]
                onChosen: (v) => app.setSetting("max_size", v)
            }
            ChoiceRow {
                label: "Frame rate"
                current: settingsPage.s.max_fps
                options: [{ value: 30, label: "30 fps" }, { value: 60, label: "60 fps" },
                          { value: 90, label: "90 fps" }, { value: 120, label: "120 fps" }]
                onChosen: (v) => app.setSetting("max_fps", v)
            }
            ChoiceRow {
                label: "Video quality"
                description: "Bit rate. Higher is sharper and needs a stronger Wi-Fi signal."
                current: settingsPage.s.bitrate_mbps
                options: [{ value: 4, label: "4 Mbps" }, { value: 8, label: "8 Mbps" },
                          { value: 12, label: "12 Mbps" }, { value: 20, label: "20 Mbps" }]
                onChosen: (v) => app.setSetting("bitrate_mbps", v)
            }
            ChoiceRow {
                label: "Video codec"
                description: "H.264 is the most compatible."
                current: settingsPage.s.video_codec
                options: [{ value: "h264", label: "H.264" }, { value: "h265", label: "H.265" }]
                onChosen: (v) => app.setSetting("video_codec", v)
            }
        }

        Card {
            Layout.fillWidth: true
            SectionTitle { text: "Audio" }
            ChoiceRow {
                topDivider: false
                label: "Audio buffer"
                description: "For audio casting. The default, 100 ms, is balanced. The lower you go, the less delay, but the more chance of glitchy audio. 50 ms is only recommended over USB. 200 ms is best for listening to music without video."
                current: settingsPage.s.audio_buffer_ms
                options: [{ value: 50, label: "50 ms" }, { value: 100, label: "100 ms" },
                          { value: 150, label: "150 ms" }, { value: 200, label: "200 ms" }]
                onChosen: (v) => app.setSetting("audio_buffer_ms", v)
            }
        }

        Card {
            Layout.fillWidth: true
            SectionTitle { text: "Device while connected" }
            ToggleRow {
                topDivider: false
                label: "Turn the device's screen off when you connect"
                description: "Saves battery and keeps the screen private. The device keeps running, and you can switch it back on while connected."
                checked: settingsPage.s.turn_screen_off
                onToggled: (on) => app.setSetting("turn_screen_off", on)
            }
            ToggleRow {
                label: "Keep the device awake while connected"
                checked: settingsPage.s.keep_active
                onToggled: (on) => app.setSetting("keep_active", on)
            }
        }

        Card {
            Layout.fillWidth: true
            SectionTitle { text: "Device notifications" }
            Text {
                Layout.fillWidth: true
                Layout.maximumWidth: 1100
                text: "Needs the companion app on your Android device (set up from the device's \"More\" menu). Notifications appear only while scrcpy4Frame is running."
                color: theme.textDim; font.pixelSize: theme.fontSmall; wrapMode: Text.WordWrap
            }
            ToggleRow {
                topDivider: false
                label: "Show device notifications"
                checked: settingsPage.s.notifications
                onToggled: (on) => app.setSetting("notifications", on)
            }
            ChoiceRow {
                label: "Where to show them"
                description: "Desktop shows pop-ups in Desktop Mode."
                current: settingsPage.s.notify_display
                options: [{ value: "auto", label: "Automatic" }, { value: "vr", label: "In VR" },
                          { value: "desktop", label: "Desktop" }, { value: "app", label: "In this app" }]
                onChosen: (v) => app.setSetting("notify_display", v)
            }
            ToggleRow {
                label: "Play a sound"
                description: "A short chime when a notification pops up."
                checked: settingsPage.s.notify_sound
                onToggled: (on) => app.setSetting("notify_sound", on)
            }
            ToggleRow {
                label: "Hide message content"
                description: "Show only the app name, for privacy."
                checked: settingsPage.s.hide_content
                onToggled: (on) => app.setSetting("hide_content", on)
            }
            ToggleRow {
                label: "Don't pop up while the overlay is visible"
                description: "For the device you're connected to: its own pop-up already shows on the overlay (or in the mirror window). Hide the overlay and they pop up again."
                checked: settingsPage.s.suppress_while_visible
                onToggled: (on) => app.setSetting("suppress_while_visible", on)
            }
            SettingRow {
                id: mutedRow
                visible: (settingsPage.s.muted_packages || []).length > 0
                label: "Muted apps"
                description: "Select an app to unmute it."
                Flow {
                    Layout.fillWidth: true
                    spacing: 12
                    layoutDirection: mutedRow.stacked ? Qt.LeftToRight : Qt.RightToLeft
                    Repeater {
                        model: settingsPage.s.muted_packages || []
                        delegate: BigButton {
                            required property string modelData
                            compact: true
                            text: modelData
                            trailingGlyph: "x"
                            Accessible.name: "Unmute " + modelData
                            onClicked: app.unmuteApp(modelData)
                        }
                    }
                }
            }
        }

        Card {
            Layout.fillWidth: true
            SectionTitle { text: "Guide" }
            Repeater {
                model: [
                    ["Overlay", "Point the laser at the device to use it. Click to tap, drag to swipe, the stick scrolls, right-click is Back."],
                    ["Moving the overlay", "Grab the bar under it with the laser and carry it. While holding, the stick moves it closer or further away. Each time you connect, it opens in front of you at the Overlay size. \"Recenter\" on the Home page, or in the overlay's three-dot menu, brings it back in front of you after a 3-second countdown: look where you want it."],
                    ["Theater", "Press the three-dot button under the overlay, then the expand button: the overlay stands on the floor 3.8 m in front of you and grows to 3.5 m. You can still move, resize and recenter it. The shrink button that replaces it brings the overlay back in front of you at its earlier size. The same menu has Recenter and an audio casting button. The Theater button on the Home page does the same."],
                    ["Window", "Point and click to tap the device. Click and drag to swipe. The stick or scroll wheel scrolls."],
                    ["Back, Home and Recents", "Use the buttons under the overlay. Right-click is also Back and middle-click is Home."],
                    ["Disconnect", "Press Alt+Q. In Steam's controller settings, bind a button chord (for example View + Menu) to Left Alt + Q."]
                ]
                delegate: InfoRow {
                    required property var modelData
                    required property int index
                    topDivider: index > 0
                    term: true
                    label: modelData[0]
                    value: modelData[1]
                }
            }
        }

        Card {
            Layout.fillWidth: true
            RowLayout {
                Layout.fillWidth: true
                spacing: theme.gap
                SectionTitle { text: "About" }
                // Only when the startup check found a newer release.
                UpdateButton { visible: app.appUpdate.available === true; named: true }
                BigButton { compact: true; text: "View logs"; onClicked: settingsPage.showLogs() }
            }
            InfoRow {
                topDivider: false
                label: "Version"
                value: app.about.version
                mono: true
            }
        }
    }
}

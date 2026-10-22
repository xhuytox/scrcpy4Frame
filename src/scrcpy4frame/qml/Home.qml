import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "components"

// Saved devices with their status and controls, plus recent device notifications.
Flickable {
    id: home
    contentHeight: body.implicitHeight + 2 * theme.pad
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { width: 18 }
    WheelScroll { flickable: home }

    readonly property var statusText: ({
        "connected": "Ready",        // adb is up. "Connected" is shown only while mirroring.
        "connecting": "Connecting…",
        "ready": "Found on Wi-Fi",
        "off": "Wireless debugging is off",
        "unreachable": "Not reachable",
        "needs_pair": "Needs pairing again",
        "unplugged": "Plug your device into the Frame",
        "usb_waiting": "Plugged in"
    })

    ColumnLayout {
        id: body
        width: Math.min(home.width - 2 * theme.pad, 1400)
        x: (home.width - width) / 2
        y: theme.pad
        spacing: theme.gap * 1.5

        Card {
            Layout.fillWidth: true
            visible: (app.mirrorStatus.message || "").length > 0
            highlighted: true
            Text {
                Layout.fillWidth: true
                text: app.mirrorStatus.message || ""
                color: theme.text
                font.pixelSize: theme.font
                wrapMode: Text.WordWrap
            }
            Flow {
                Layout.fillWidth: true
                spacing: theme.gap
                BigButton {
                    visible: app.mirrorStatus.retrySmaller === true
                    kind: "primary"; compact: true
                    text: "Try a smaller resolution"
                    onClicked: app.retryMirrorSmaller()
                }
                BigButton { compact: true; text: "Dismiss"; onClicked: app.clearMirrorMessage() }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: app.devices.length === 0
            SectionTitle { text: "Connect to an Android device" }
            Text {
                Layout.fillWidth: true
                Layout.maximumWidth: 1100
                text: "See and control your Android device right here in the headset, over Wi-Fi or USB. Setup takes about two minutes, and we'll walk you through every step on the device."
                color: theme.textDim
                font.pixelSize: theme.font
                wrapMode: Text.WordWrap
            }
            ButtonGrid {
                maxColumns: 2
                BigButton {
                    kind: "primary"; text: "Set up a device"
                    enabled: app.ready
                    onClicked: app.startWizard("new", "")
                }
            }
        }

        Card {
            Layout.fillWidth: true
            visible: app.usbHint.length > 0
            highlighted: true
            UsbHint {
                Layout.fillWidth: true
                waitingFor: app.usbHint
                allowText: "A device is plugged in. Tap Allow on it to use the cable."
            }
        }

        Repeater {
            model: app.devices
            delegate: Card {
                id: deviceCard
                required property var modelData
                property bool showMore: false
                property bool showReenable: false
                readonly property var d: modelData
                readonly property bool inVr: app.mirrorStatus.mode === "vr"
                // Started, but the overlay only shows in the headset with its first frame.
                readonly property bool opening: d.mirroring && app.mirrorStatus.opening === true
                Layout.fillWidth: true
                highlighted: d.mirroring

                RowLayout {
                    Layout.fillWidth: true
                    spacing: theme.gap
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Text {
                            Layout.fillWidth: true
                            text: deviceCard.d.name
                            color: theme.text
                            font.pixelSize: theme.fontLarge
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: [deviceCard.d.manufacturer,
                                   deviceCard.d.release ? "Android " + deviceCard.d.release : "",
                                   deviceCard.d.via === "usb" ? "USB" : deviceCard.d.via === "wifi" ? "Wi-Fi" : "",
                                   deviceCard.d.host].filter(function (s) { return s }).join("  ·  ")
                            color: theme.textDim
                            font.pixelSize: theme.fontSmall
                            elide: Text.ElideRight
                        }
                    }
                    RowLayout {
                        spacing: 12
                        StatusChip {
                            status: deviceCard.opening ? "connecting" : deviceCard.d.status
                            text: deviceCard.opening ? "Connecting…"
                                  : deviceCard.d.mirroring
                                  ? "Connected"     // even if the VR panel is hidden
                                  : (home.statusText[deviceCard.d.status] || deviceCard.d.status)
                        }
                        BigButton {
                            visible: !deviceCard.d.mirroring
                            kind: "ghost"; compact: true
                            implicitWidth: 150
                            text: deviceCard.showMore ? "Less" : "More"
                            trailingGlyph: deviceCard.showMore ? "up" : "down"
                            onClicked: deviceCard.showMore = !deviceCard.showMore
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: (deviceCard.d.detail || "").length > 0
                    text: deviceCard.d.detail || ""
                    color: theme.textDim
                    font.pixelSize: theme.fontSmall
                    wrapMode: Text.WordWrap
                }
                UsbHint {
                    Layout.fillWidth: true
                    waitingFor: deviceCard.d.usbHint || ""
                    allowText: "Tap Allow on your device to use the cable."
                }
                RowLayout {
                    visible: deviceCard.d.companion
                    spacing: 12
                    Rectangle { width: 10; height: 10; radius: 5; color: companionText.color }
                    Text {
                        id: companionText
                        text: deviceCard.d.companionState === "connected" ? "Companion: notifications on"
                              : "Companion: waiting for the device's app…"
                        color: deviceCard.d.companionState === "connected" ? theme.ok : theme.textDim
                        font.pixelSize: theme.fontSmall
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: deviceCard.d.companion && (deviceCard.d.companionNote || "").length > 0
                    text: deviceCard.d.companionNote || ""
                    color: theme.textDim
                    font.pixelSize: theme.fontSmall
                    wrapMode: Text.WordWrap
                }

                Card {
                    id: reenableCard
                    Layout.fillWidth: true
                    visible: deviceCard.showReenable && deviceCard.d.status === "off" && !deviceCard.d.mirroring
                    color: theme.bg
                    readonly property var guide: deviceCard.showReenable ? app.guideFor("reenable", deviceCard.d.id) : ({})
                    SectionLabel { text: reenableCard.guide.title || "" }
                    GuideSteps {
                        Layout.fillWidth: true
                        lines: reenableCard.guide.lines || []
                    }
                    RowLayout {
                        spacing: 12
                        Rectangle {
                            width: 12; height: 12; radius: 6
                            color: theme.warn
                            SequentialAnimation on opacity {
                                running: deviceCard.showReenable && deviceCard.d.status === "off"
                                loops: Animation.Infinite
                                NumberAnimation { to: 0.25; duration: 600; easing.type: Easing.InOutQuad }
                                NumberAnimation { to: 1; duration: 600; easing.type: Easing.InOutQuad }
                            }
                        }
                        Text {
                            text: "We'll connect as soon as we see it."
                            color: theme.textDim
                            font.pixelSize: theme.fontSmall
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: deviceCard.d.mirroring
                    spacing: theme.gap
                    Divider {}
                    // The controls wait for the overlay, which takes a few seconds to appear.
                    RowLayout {
                        Layout.fillWidth: true
                        visible: deviceCard.opening
                        spacing: theme.gap
                        Spinner { size: 48; color: theme.accent }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4
                            Text {
                                Layout.fillWidth: true
                                text: "Opening the overlay…"
                                color: theme.text
                                font.pixelSize: theme.font
                                font.weight: Font.Medium
                                wrapMode: Text.WordWrap
                            }
                            Text {
                                Layout.fillWidth: true
                                text: "It appears in front of you in a few seconds."
                                color: theme.textDim
                                font.pixelSize: theme.fontSmall
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: !deviceCard.opening
                        spacing: 14
                        SectionLabel { text: "Controls" }
                        // On/off buttons name what's true now and are accented while it's on.
                        // Wide enough cells that "Audio casting off" never gets cut off.
                        ButtonGrid {
                            maxColumns: 4
                            minCellWidth: 300
                            BigButton {
                                // Hiding the panel keeps mirroring and audio casting running.
                                visible: deviceCard.inVr
                                kind: "toggle"
                                isOn: app.mirrorStatus.panelVisible
                                glyph: isOn ? "eye" : "eyeOff"
                                text: isOn ? "Shown" : "Hidden"
                                onClicked: app.setPanelVisible(!isOn)
                            }
                            BigButton {
                                // The panel's own Theater icon: what a press does, expand or shrink.
                                // Turning it on shows a hidden panel.
                                visible: deviceCard.inVr
                                kind: "toggle"
                                isOn: app.mirrorStatus.theater === true
                                glyph: isOn ? "shrink" : "expand"
                                text: isOn ? "Theater on" : "Theater off"
                                onClicked: app.setTheater(!isOn)
                            }
                            BigButton {
                                // Busy until scrcpy's audio client is up on the device.
                                kind: "toggle"
                                isOn: app.mirrorStatus.audioCasting === true
                                busy: app.mirrorStatus.audioBusy === true
                                enabled: app.mirrorStatus.audioSupported !== false && !busy
                                glyph: isOn ? "headphones" : "headphonesOff"
                                text: app.mirrorStatus.audioSupported === false ? "Needs Android 11"
                                      : (isOn ? "Audio casting on" : "Audio casting off")
                                onClicked: app.setAudioCasting(!isOn)
                            }
                            BigButton {
                                // Busy while the first change of a session sets up its connection.
                                kind: "toggle"
                                isOn: app.mirrorStatus.screenOn
                                busy: app.mirrorStatus.screenBusy === true
                                enabled: !busy
                                glyph: isOn ? "phone" : "phoneOff"
                                text: isOn ? "Screen on" : "Screen off"
                                onClicked: app.setPhoneScreen(!isOn)
                            }
                            BigButton {
                                visible: deviceCard.inVr
                                glyph: "recenter"
                                text: "Recenter"
                                onClicked: app.recenterPanel()
                            }
                        }
                    }
                    Divider {}
                    BigButton {
                        Layout.alignment: Qt.AlignRight
                        kind: "danger"
                        text: deviceCard.opening ? "Cancel" : "Disconnect"
                        onClicked: app.stopMirror()
                    }
                }

                ButtonGrid {
                    visible: !deviceCard.d.mirroring
                    BigButton {
                        kind: "primary"
                        text: "Connect"
                        enabled: deviceCard.d.status === "connected" && !app.mirroring
                        onClicked: app.mirror(deviceCard.d.id)
                    }
                    BigButton {
                        visible: deviceCard.d.status === "off" && deviceCard.d.method === "wireless"
                                 && deviceCard.d.setup === "wireless"
                        text: deviceCard.showReenable ? "Hide help" : "How do I turn it on?"
                        onClicked: deviceCard.showReenable = !deviceCard.showReenable
                    }
                    BigButton {
                        visible: deviceCard.d.status === "needs_pair"
                        text: "Pair again"
                        onClicked: app.startWizard("repair", deviceCard.d.id)
                    }
                    BigButton {
                        visible: deviceCard.d.status === "ready" || deviceCard.d.status === "unreachable"
                        text: "Retry now"
                        onClicked: app.reconnectDevice(deviceCard.d.id)
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: deviceCard.showMore && !deviceCard.d.mirroring
                    spacing: theme.gap
                    Divider {}
                    OptionRow {
                        id: companionOption
                        visible: deviceCard.d.companion || deviceCard.d.sdk >= 26
                        topDivider: false
                        glyph: "phone"
                        title: "Companion app"
                        status: deviceCard.d.companion ? "Installed" : ""
                        description: "Turns Wireless debugging on only while scrcpy4Frame is open, and shows the device's notifications in the headset."
                        BigButton {
                            Layout.fillWidth: true
                            compact: true
                            text: deviceCard.d.companion ? "Remove" : "Install"
                            enabled: deviceCard.d.companion || deviceCard.d.status === "connected"
                            onClicked: deviceCard.d.companion ? app.removeCompanion(deviceCard.d.id)
                                                              : app.installCompanion(deviceCard.d.id)
                        }
                    }
                    OptionRow {
                        id: tileOption
                        visible: deviceCard.d.method === "wireless"
                        topDivider: companionOption.visible
                        glyph: "tiles"
                        title: "Quick Settings shortcut"
                        description: "A Wireless debugging button in the device's Quick Settings, so turning it back on is one tap."
                        BigButton {
                            Layout.fillWidth: true
                            visible: !deviceCard.d.qsTile
                            compact: true
                            text: "Add"
                            enabled: deviceCard.d.status === "connected"
                            onClicked: app.addTile(deviceCard.d.id)
                        }
                        RowLayout {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredHeight: theme.target * 0.75
                            visible: deviceCard.d.qsTile
                            spacing: 10
                            LineIcon { name: "check"; size: 28; color: theme.ok }
                            Text { text: "Added"; color: theme.ok; font.pixelSize: theme.fontSmall; font.weight: Font.Medium }
                        }
                    }
                    OptionRow {
                        visible: deviceCard.d.method === "wireless" && deviceCard.d.setup === "wireless"
                        topDivider: companionOption.visible || tileOption.visible
                        glyph: "link"
                        title: "Pairing"
                        description: "Pair again if the device no longer trusts this Frame."
                        BigButton {
                            Layout.fillWidth: true
                            compact: true
                            text: "Pair again"
                            onClicked: app.startWizard("repair", deviceCard.d.id)
                        }
                    }
                    OptionRow {
                        topDivider: companionOption.visible || tileOption.visible
                        glyph: "trash"
                        danger: true
                        title: "Forget this device"
                        description: (deviceCard.d.companion ? "Uninstalls the companion app, disconnects the device and removes it from this list."
                                                             : "Disconnects it and removes it from this list.")
                                     + " You can set it up again any time."
                        BigButton {
                            Layout.fillWidth: true
                            compact: true
                            kind: "danger"
                            text: "Forget"
                            onClicked: app.forgetDevice(deviceCard.d.id)
                        }
                    }
                }
            }
        }

        BigButton {
            Layout.fillWidth: true
            visible: app.devices.length > 0
            kind: "dashed"
            glyph: "plus"
            text: "Add another device"
            enabled: app.ready
            onClicked: app.startWizard("new", "")
        }

        Card {
            Layout.fillWidth: true
            visible: app.notifications.length > 0
            RowLayout {
                Layout.fillWidth: true
                SectionTitle { text: "Device notifications" }
                BigButton { kind: "ghost"; compact: true; text: "Clear all"; onClicked: app.clearNotifications() }
            }
            Repeater {
                model: app.notifications.slice(0, 8)
                delegate: Item {
                    id: note
                    required property var modelData
                    required property int index
                    readonly property bool narrow: width < theme.stackBelow
                    Layout.fillWidth: true
                    implicitHeight: noteGrid.y + noteGrid.implicitHeight

                    Divider {
                        visible: note.index > 0
                        width: parent.width
                    }
                    GridLayout {
                        id: noteGrid
                        y: note.index > 0 ? theme.gap : 0
                        width: parent.width
                        columns: 3
                        columnSpacing: 24
                        rowSpacing: 16
                        Rectangle {
                            Layout.row: 0; Layout.column: 0
                            Layout.alignment: Qt.AlignTop
                            implicitWidth: 64; implicitHeight: 64
                            radius: 16
                            color: note.modelData.icon.length > 0 ? "transparent" : theme.surface3
                            Image {
                                anchors.fill: parent
                                source: note.modelData.icon ? "data:image/png;base64," + note.modelData.icon : ""
                                visible: note.modelData.icon.length > 0
                                fillMode: Image.PreserveAspectFit
                            }
                            Text {
                                anchors.centerIn: parent
                                visible: note.modelData.icon.length === 0
                                text: (note.modelData.appLabel || "?").charAt(0)
                                color: theme.text
                                font.pixelSize: theme.font
                                font.weight: Font.Bold
                            }
                        }
                        ColumnLayout {
                            Layout.row: 0; Layout.column: 1
                            Layout.fillWidth: true
                            spacing: 2
                            Text { text: note.modelData.appLabel; color: theme.textDim; font.pixelSize: theme.fontSmall }
                            Text {
                                Layout.fillWidth: true
                                text: note.modelData.title; color: theme.text; font.pixelSize: theme.font
                                font.weight: Font.DemiBold; elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: app.settingsView.hide_content ? "" : note.modelData.text
                                visible: text.length > 0
                                color: theme.text; font.pixelSize: theme.fontSmall
                                wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                            }
                        }
                        // Fixed-width buttons in their own column so they line up across rows.
                        RowLayout {
                            Layout.row: note.narrow ? 1 : 0
                            Layout.column: note.narrow ? 1 : 2
                            spacing: 12
                            uniformCellSizes: true
                            BigButton { Layout.preferredWidth: 168; compact: true; text: "Open"; onClicked: app.openNotification(note.modelData.uid) }
                            BigButton { Layout.preferredWidth: 168; compact: true; text: "Dismiss"; onClicked: app.dismissNotification(note.modelData.uid) }
                            BigButton { Layout.preferredWidth: 168; compact: true; kind: "ghost"; text: "Mute app"; onClicked: app.muteApp(note.modelData.pkg) }
                        }
                    }
                }
            }
        }
    }
}

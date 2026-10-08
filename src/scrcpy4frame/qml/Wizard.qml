import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "components"

// Device setup wizard. Steps advance on their own once the Python side can verify them.
Item {
    id: wp
    readonly property var w: app.wizardView
    readonly property var g: w.guide || ({})
    readonly property string step: w.step || ""

    readonly property var fallbackTitles: ({
        "brand": "Let's connect your device",
        "done": "All set"
    })

    function act(name, args) { app.wizardAction(name, args || []) }

    function defaultStatus() {
        if (w.message) return w.message
        switch (step) {
        case "wireless": return "Waiting for your device…"
        case "pair_open": return "Waiting for the pairing window…"
        case "usb_plug": return "Waiting for your device…"
        case "usb_auth": return "Waiting for you to tap Allow…"
        case "usb_perms": return "Waiting for permission…"
        case "usb_unplug": return "Waiting for you to unplug the cable…"
        case "connecting": return "Connecting…"
        case "usb_tcpip": return "Working…"
        case "oem_input": return w.status === "working" ? "Checking…" : "Waiting for the setting…"
        default: return ""
        }
    }

    // Kept slim so the pairing keypad fits without scrolling.
    RowLayout {
        id: header
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: theme.pad
        anchors.rightMargin: theme.pad
        anchors.topMargin: 24
        spacing: theme.gap

        BigButton {
            text: "‹ Back"
            kind: "ghost"; compact: true
            visible: wp.w.canBack === true
            onClicked: wp.act("back")
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: wp.g.title || wp.fallbackTitles[wp.step] || ""
                color: theme.text
                font.pixelSize: theme.fontLarge
                font.weight: Font.Bold
                wrapMode: Text.WordWrap
            }
            Row {
                spacing: 8
                Repeater {
                    model: wp.w.progressTotal || 0
                    delegate: Rectangle {
                        required property int index
                        width: 56; height: 8; radius: 4
                        color: index <= wp.w.progress ? theme.accent : theme.surface3
                    }
                }
            }
        }
        BigButton {
            text: "Cancel"
            kind: "ghost"; compact: true
            onClicked: app.cancelWizard()
        }
    }

    Flickable {
        id: flick
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: parent.bottom
        anchors.leftMargin: theme.pad
        anchors.rightMargin: theme.pad
        anchors.topMargin: 24
        anchors.bottomMargin: 24
        contentHeight: body.implicitHeight + theme.pad
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { width: 18 }
        WheelScroll { flickable: flick }

        ColumnLayout {
            id: body
            width: Math.min(flick.width - 30, 1400)
            x: (flick.width - width) / 2
            spacing: theme.gap

            Loader {
                id: stepLoader
                Layout.fillWidth: true
                sourceComponent: {
                    switch (wp.step) {
                    case "brand": return brandStep
                    case "pick_phone": return pickStep
                    case "pair_code": return codeStep
                    case "manual": return manualStep
                    case "companion": return companionStep
                    case "tile": return tileStep
                    case "test": return testStep
                    case "done": return null
                    default: return instructionStep
                    }
                }
                onLoaded: if (item) item.forceActiveFocus()
            }
        }
    }

    Component {
        id: brandStep
        ColumnLayout {
            spacing: theme.gap
            Text {
                Layout.fillWidth: true
                text: "Which device do you have? We'll show you exactly where to tap."
                      + (app.network.ssid ? " Your device needs to be on the same Wi-Fi as the Frame: \"" + app.network.ssid + "\"." : "")
                color: theme.textDim
                font.pixelSize: theme.font
                wrapMode: Text.WordWrap
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                rowSpacing: theme.gap
                columnSpacing: theme.gap
                Repeater {
                    model: app.brands
                    delegate: BigButton {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.label
                        onClicked: wp.act("choose_brand", [modelData.id])
                    }
                }
            }
            Card {
                Layout.fillWidth: true
                visible: wp.w.path !== "usb"
                Text {
                    Layout.fillWidth: true
                    text: "Device on Android 10 or older, or no Wi-Fi pairing? You can set it up once with a USB cable plugged into the Frame."
                    color: theme.textDim
                    font.pixelSize: theme.fontSmall
                    wrapMode: Text.WordWrap
                }
                BigButton {
                    compact: true
                    text: "Use a USB cable instead"
                    onClicked: app.startWizard("usb", "")
                }
            }
        }
    }

    Component {
        id: instructionStep
        ColumnLayout {
            spacing: theme.gap
            Card {
                Layout.fillWidth: true
                GuideSteps { Layout.fillWidth: true; lines: wp.g.lines || [] }
                Text {
                    Layout.fillWidth: true
                    visible: (wp.g.tip || "").length > 0
                    text: "Tip: " + (wp.g.tip || "")
                    color: theme.textDim
                    font.pixelSize: theme.fontSmall
                    wrapMode: Text.WordWrap
                }
            }
            Card {
                Layout.fillWidth: true
                visible: wp.step === "usb_perms"
                TextEdit {
                    Layout.fillWidth: true
                    readOnly: true
                    selectByMouse: true
                    text: wp.g.command || ""
                    color: theme.text
                    font.family: "monospace"
                    font.pixelSize: theme.fontSmall
                    wrapMode: TextEdit.WrapAnywhere
                }
            }
            Text {
                Layout.fillWidth: true
                visible: wp.step === "pair_open" && wp.w.targetFound === true
                text: "Wireless debugging is on. Found a device at " + wp.w.targetHost + "."
                color: theme.ok
                font.pixelSize: theme.font
                wrapMode: Text.WordWrap
            }
            StatusChip {
                Layout.fillWidth: false
                status: wp.w.status
                text: wp.defaultStatus()
            }
            Card {
                Layout.fillWidth: true
                visible: wp.w.help === true && (wp.g.help || []).length > 0
                highlighted: true
                Text { text: "Having trouble?"; color: theme.text; font.pixelSize: theme.fontLarge; font.weight: Font.DemiBold }
                GuideSteps { Layout.fillWidth: true; numbered: false; lines: wp.g.help || [] }
            }
            Flow {
                Layout.fillWidth: true
                spacing: theme.gap
                BigButton {
                    visible: wp.step === "devopts" || wp.step === "usb_setup"
                    kind: "primary"; text: "Done"
                    onClicked: wp.act("next")
                }
                BigButton {
                    visible: (wp.step === "connecting" || wp.step === "usb_tcpip" || wp.step === "usb_unplug") && wp.w.status === "error"
                    kind: "primary"; text: "Try again"
                    onClicked: wp.act("retry")
                }
                BigButton {
                    visible: wp.step === "oem_input" && wp.w.status === "waiting"
                    kind: "primary"; text: "Check again"
                    onClicked: wp.act("check_again")
                }
                BigButton {
                    visible: wp.step === "tile_manual"
                    kind: "primary"; text: "Check again"; enabled: !wp.w.busy
                    onClicked: wp.act("check_again")
                }
                BigButton {
                    visible: wp.step === "tile_manual"
                    text: "Continue"
                    onClicked: wp.act("next")
                }
                BigButton {
                    visible: (wp.step === "wireless" || wp.step === "pair_open" || wp.step === "usb_plug") && wp.w.help !== true
                    text: "I need help"
                    onClicked: wp.act("show_help")
                }
                BigButton {
                    visible: wp.step === "pair_open" && wp.w.targetFound === true
                    text: "That's not my device"
                    onClicked: wp.act("not_mine")
                }
                BigButton {
                    visible: wp.step === "wireless" || wp.step === "pair_open"
                             || (wp.step === "connecting" && wp.w.status === "error")
                    text: "Enter details manually"
                    onClicked: wp.act("use_manual")
                }
                BigButton {
                    visible: wp.step === "wireless" && wp.w.help === true
                    text: "Use a USB cable instead"
                    onClicked: wp.act("use_usb")
                }
                BigButton {
                    visible: wp.step === "oem_input"
                    text: "Skip (view only)"
                    onClicked: wp.act("skip")
                }
            }
        }
    }

    Component {
        id: pickStep
        ColumnLayout {
            spacing: theme.gap
            Card {
                Layout.fillWidth: true
                GuideSteps { Layout.fillWidth: true; lines: wp.g.lines || [] }
            }
            Repeater {
                model: wp.w.candidates || []
                delegate: BigButton {
                    required property var modelData
                    Layout.fillWidth: true
                    text: modelData.host + "   (port " + modelData.port + ")"
                    onClicked: wp.act("pick", [modelData.host])
                }
            }
        }
    }

    Component {
        id: codeStep
        RowLayout {
            spacing: theme.gap * 2
            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: theme.gap
                Card {
                    Layout.fillWidth: true
                    GuideSteps { Layout.fillWidth: true; lines: wp.g.lines || [] }
                    Text {
                        text: "Pairing with " + wp.w.pairHost + ":" + wp.w.pairPort
                        color: theme.textDim
                        font.pixelSize: theme.fontSmall
                    }
                }
                Text {
                    readonly property int remaining: 6 - pad.value.length
                    text: wp.w.busy ? "Pairing…"
                          : (remaining > 0 ? remaining + (remaining === 1 ? " digit" : " digits") + " to go" : "Ready: press Pair")
                    color: theme.textDim
                    font.pixelSize: theme.fontSmall
                }
                StatusChip {
                    status: wp.w.status
                    text: wp.w.message || ""
                }
                BigButton {
                    kind: "ghost"; compact: true
                    text: "Enter details manually"
                    onClicked: wp.act("use_manual")
                }
            }
            Numpad {
                id: pad
                focus: true
                Layout.alignment: Qt.AlignTop
                submitText: "Pair"
                submitEnabled: value.length === 6 && !wp.w.busy
                onSubmitted: (value) => wp.act("submit_code", [value])
            }
        }
    }

    Component {
        id: manualStep
        RowLayout {
            id: manual
            spacing: theme.gap * 2
            property string field: "ip"
            property var values: ({ "ip": "", "connect": "", "pair": "", "code": "" })
            readonly property var limits: ({ "ip": 15, "connect": 5, "pair": 5, "code": 6 })

            function key(k) {
                var copy = Object.assign({}, values)
                var current = copy[field]
                if (k === "back") current = current.slice(0, -1)
                else if (current.length < limits[field]) current += k
                copy[field] = current
                values = copy
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: theme.gap
                Card {
                    Layout.fillWidth: true
                    GuideSteps { Layout.fillWidth: true; lines: wp.g.lines || [] }
                }
                Repeater {
                    model: [
                        { "id": "ip", "label": "IP address" },
                        { "id": "connect", "label": "Port on the Wireless debugging screen" },
                        { "id": "pair", "label": "Port in the pairing window" },
                        { "id": "code", "label": "6-digit pairing code" }
                    ]
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: 104
                        radius: theme.radius
                        color: theme.surface2
                        border.width: manual.field === modelData.id ? 3 : 1
                        border.color: manual.field === modelData.id ? theme.accent : theme.border
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.leftMargin: 24
                            spacing: 4
                            Text { text: modelData.label; color: theme.textDim; font.pixelSize: theme.fontSmall }
                            Text { text: manual.values[modelData.id] || " "; color: theme.text; font.pixelSize: theme.fontLarge }
                        }
                        MouseArea { anchors.fill: parent; onClicked: manual.field = modelData.id }
                    }
                }
                StatusChip {
                    status: wp.w.status
                    text: wp.w.message || ""
                }
            }
            ColumnLayout {
                Layout.alignment: Qt.AlignTop
                spacing: theme.gap
                Numpad {
                    focus: true
                    external: true
                    showDisplay: false
                    showSubmit: false
                    allowDot: manual.field === "ip"
                    onKeyPressed: (k) => manual.key(k)
                }
                BigButton {
                    Layout.fillWidth: true
                    kind: "primary"
                    text: wp.w.busy ? "Pairing…" : "Pair"
                    enabled: !wp.w.busy
                    onClicked: wp.act("manual_submit", [manual.values.ip, parseInt(manual.values.connect || "0"),
                                                        parseInt(manual.values.pair || "0"), manual.values.code])
                }
            }
        }
    }

    Component {
        id: companionStep
        ColumnLayout {
            spacing: theme.gap
            readonly property var checklist: [
                { "key": "install", "label": "Install the companion" },
                { "key": "reconnect", "label": "Allow automatic reconnect" },
                { "key": "notifications", "label": "Allow notification access" },
                { "key": "battery", "label": "Keep it running in the background" }
            ]
            Card {
                Layout.fillWidth: true
                GuideSteps { Layout.fillWidth: true; numbered: false; lines: wp.g.lines || [] }
            }
            Card {
                Layout.fillWidth: true
                visible: wp.w.busy || Object.keys(wp.w.companionSteps || {}).length > 0
                Repeater {
                    model: parent.parent.checklist
                    delegate: RowLayout {
                        required property var modelData
                        readonly property string state: (wp.w.companionSteps || {})[modelData.key] || ""
                        spacing: 16
                        Text {
                            Layout.preferredWidth: 44
                            text: state === "ok" ? "✓" : state === "failed" ? "✕" : state === "skipped" ? "–" : "…"
                            color: state === "ok" ? theme.ok : state === "failed" ? theme.error : theme.textDim
                            font.pixelSize: theme.fontLarge
                            font.weight: Font.Bold
                        }
                        Text {
                            text: modelData.label + (state === "skipped" ? " (needs Android 11)" : "")
                            color: theme.text
                            font.pixelSize: theme.font
                        }
                    }
                }
            }
            StatusChip {
                status: wp.w.status
                text: wp.w.message || ""
            }
            Flow {
                Layout.fillWidth: true
                spacing: theme.gap
                BigButton {
                    visible: wp.w.status !== "ok"
                    kind: "primary"
                    enabled: !wp.w.busy
                    text: wp.w.status === "error" ? "Try again" : "Install the companion"
                    onClicked: wp.act(wp.w.status === "error" ? "retry" : "accept_companion")
                }
                BigButton {
                    visible: wp.w.status !== "ok"
                    enabled: !wp.w.busy
                    text: wp.w.status === "error" ? "Skip" : "No thanks"
                    onClicked: wp.act(wp.w.status === "error" ? "skip" : "decline_companion")
                }
                BigButton {
                    visible: wp.w.status === "ok"
                    kind: "primary"
                    text: "Continue"
                    onClicked: wp.act("next")
                }
            }
        }
    }

    Component {
        id: tileStep
        ColumnLayout {
            spacing: theme.gap
            Card {
                Layout.fillWidth: true
                GuideSteps { Layout.fillWidth: true; numbered: false; lines: wp.g.lines || [] }
            }
            StatusChip { status: wp.w.status; text: wp.w.message || "" }
            Flow {
                Layout.fillWidth: true
                spacing: theme.gap
                BigButton {
                    visible: wp.w.status !== "ok"
                    kind: "primary"; enabled: !wp.w.busy
                    text: "Add the shortcut"
                    onClicked: wp.act("add_tile")
                }
                BigButton {
                    visible: wp.w.status !== "ok"
                    enabled: !wp.w.busy
                    text: "Skip"
                    onClicked: wp.act("skip")
                }
                BigButton {
                    visible: wp.w.status === "ok"
                    kind: "primary"; text: "Continue"
                    onClicked: wp.act("next")
                }
            }
        }
    }

    Component {
        id: testStep
        ColumnLayout {
            spacing: theme.gap
            Card {
                Layout.fillWidth: true
                highlighted: true
                Text {
                    Layout.fillWidth: true
                    text: "Paired with " + (wp.w.device.model || "your device")
                          + (wp.w.device.release ? " (Android " + wp.w.device.release + ")" : "")
                    color: theme.ok
                    font.pixelSize: theme.fontLarge
                    font.weight: Font.DemiBold
                    wrapMode: Text.WordWrap
                }
                GuideSteps { Layout.fillWidth: true; numbered: false; lines: wp.g.lines || [] }
            }
            Flow {
                Layout.fillWidth: true
                spacing: theme.gap
                BigButton { kind: "primary"; text: "Connect now"; onClicked: wp.act("start_mirror") }
                BigButton { text: "Finish"; onClicked: wp.act("finish") }
            }
        }
    }
}

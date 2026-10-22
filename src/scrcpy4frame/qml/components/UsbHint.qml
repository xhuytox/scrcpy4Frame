import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// What a plugged-in device waits for before the cable works: Allow on the device, or a one-time
// USB permission on the Frame.
ColumnLayout {
    id: hint
    property string waitingFor: ""          // "unauthorized" | "no permissions"
    property string allowText: ""
    readonly property var perms: waitingFor === "no permissions" ? app.guide("usb_perms") : ({})

    visible: waitingFor.length > 0
    spacing: theme.gap / 2

    Text {
        Layout.fillWidth: true
        visible: hint.waitingFor === "unauthorized"
        text: hint.allowText
        color: theme.text
        font.pixelSize: theme.fontSmall
        wrapMode: Text.WordWrap
    }
    GuideSteps {
        Layout.fillWidth: true
        visible: hint.waitingFor === "no permissions"
        lines: hint.perms.lines || []
    }
    TextEdit {
        Layout.fillWidth: true
        visible: hint.waitingFor === "no permissions"
        readOnly: true
        selectByMouse: true
        text: hint.perms.command || ""
        color: theme.text
        font.family: "monospace"
        font.pixelSize: theme.fontSmall
        wrapMode: TextEdit.WrapAnywhere
    }
}

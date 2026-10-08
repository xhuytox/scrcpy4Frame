import QtQuick
import QtQuick.Layouts

// Status pill. `status` is waiting | working | ok | error | idle, or a device status.
Rectangle {
    id: chip
    property string status: "idle"
    property string text: ""
    property bool small: false          // tag-sized, for use next to a title

    readonly property color tone: {
        switch (status) {
        case "ok": case "connected": return theme.ok
        case "error": case "needs_pair": case "unreachable": return theme.error
        case "waiting": case "off": return theme.warn
        case "working": case "connecting": case "ready": return theme.accent
        default: return theme.textDim
        }
    }

    visible: text.length > 0
    radius: height / 2
    color: Qt.rgba(tone.r, tone.g, tone.b, 0.14)
    border.color: Qt.rgba(tone.r, tone.g, tone.b, 0.5)
    implicitHeight: row.implicitHeight + (small ? 8 : 20)
    implicitWidth: row.implicitWidth + (small ? 28 : 40)

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: chip.small ? 10 : 14
        Rectangle {
            id: dot
            readonly property bool busy: chip.status === "working" || chip.status === "connecting" || chip.status === "waiting"
            width: chip.small ? 10 : 16; height: width; radius: width / 2
            color: chip.tone
            // Pulse while busy so the screen doesn't look frozen.
            SequentialAnimation on opacity {
                running: dot.busy && chip.visible
                loops: Animation.Infinite
                onStopped: dot.opacity = 1
                NumberAnimation { to: 0.25; duration: 600; easing.type: Easing.InOutQuad }
                NumberAnimation { to: 1; duration: 600; easing.type: Easing.InOutQuad }
            }
        }
        Text {
            text: chip.text
            color: theme.text
            font.pixelSize: chip.small ? theme.fontLabel : theme.fontSmall
            font.weight: Font.Medium
            wrapMode: Text.WordWrap
            Layout.maximumWidth: 900
        }
    }
}

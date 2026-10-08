import QtQuick
import QtQuick.Layouts

// Strip under the header while a newer release is available. Closing it keeps any update going,
// and Settings › About still offers it.
Rectangle {
    id: banner
    readonly property var u: app.appUpdate
    readonly property color tone: theme.accent

    visible: u.available === true && u.banner === true
    implicitHeight: visible ? row.implicitHeight + 2 * 14 : 0
    color: Qt.tint(theme.surface, Qt.rgba(tone.r, tone.g, tone.b, 0.12))
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.border }

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: theme.pad
        anchors.rightMargin: theme.pad
        spacing: theme.gap
        LineIcon { name: "download"; color: theme.accent; size: 36 }
        Text {
            Layout.fillWidth: true
            text: {
                var latest = "v" + banner.u.latest
                if (banner.u.state === "installed")
                    return banner.u.error || ("scrcpy4Frame " + latest + " is installed. Restart it to finish updating.")
                if (banner.u.error)
                    return "Couldn't update to " + latest + ". " + banner.u.error
                return "scrcpy4Frame " + latest + " is available. You're on v" + banner.u.current + "."
            }
            color: theme.text
            font.pixelSize: theme.font
            wrapMode: Text.WordWrap
        }
        UpdateButton {}
        BigButton {
            kind: "ghost"
            compact: true
            implicitWidth: implicitHeight
            Accessible.name: "Close"
            contentItem: Item {
                LineIcon { anchors.centerIn: parent; name: "x"; size: 30 }
            }
            onClicked: app.closeUpdateBanner()
        }
    }
}

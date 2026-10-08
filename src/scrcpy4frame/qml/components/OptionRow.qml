import QtQuick
import QtQuick.Layouts

// Row in a device's More list: icon, title and description, then the action button.
Item {
    id: opt
    property string glyph: ""
    property string title: ""
    property string description: ""
    property string status: ""              // tag after the title, like "Installed"
    property bool danger: false
    property bool topDivider: true          // off for the first visible row
    default property alias action: slot.data
    readonly property color errorTone: theme.error

    Layout.fillWidth: true
    implicitHeight: row.y + row.implicitHeight

    // Indented past the icon column.
    Divider {
        visible: opt.topDivider
        x: 64 + row.spacing
        width: parent.width - x
    }

    RowLayout {
        id: row
        y: opt.topDivider ? theme.gap : 0
        width: parent.width
        spacing: 24
        Rectangle {
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 64
            implicitHeight: 64
            radius: 18
            color: opt.danger ? Qt.tint(theme.surface2, Qt.rgba(opt.errorTone.r, opt.errorTone.g, opt.errorTone.b, 0.12))
                              : theme.surface2
            LineIcon {
                anchors.centerIn: parent
                name: opt.glyph
                size: 30
                color: opt.danger ? theme.error : theme.textDim
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                spacing: 14
                Text {
                    text: opt.title
                    color: theme.text
                    font.pixelSize: theme.font
                }
                StatusChip { small: true; status: "ok"; text: opt.status }
                Item { Layout.fillWidth: true }
            }
            Text {
                Layout.fillWidth: true
                text: opt.description
                color: theme.textDim
                font.pixelSize: theme.fontSmall
                wrapMode: Text.WordWrap
            }
        }
        ColumnLayout {
            id: slot
            // Fixed width so buttons line up even when a description is long.
            Layout.preferredWidth: 220
            Layout.minimumWidth: 220
            Layout.maximumWidth: 220
            Layout.alignment: Qt.AlignVCenter
        }
    }
}

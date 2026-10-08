import QtQuick
import QtQuick.Layouts

// Numbered or bulleted instruction list.
ColumnLayout {
    id: guide
    property var lines: []
    property bool numbered: true
    spacing: 18

    Repeater {
        model: guide.lines
        delegate: RowLayout {
            required property string modelData
            required property int index
            Layout.fillWidth: true
            spacing: 20
            Rectangle {
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: 2
                width: 44; height: 44; radius: 22
                color: theme.surface3
                Text {
                    anchors.centerIn: parent
                    text: guide.numbered ? (index + 1) : "•"
                    color: theme.accent
                    font.pixelSize: theme.fontSmall
                    font.weight: Font.Bold
                }
            }
            Text {
                Layout.fillWidth: true
                text: modelData
                color: theme.text
                font.pixelSize: theme.font
                wrapMode: Text.WordWrap
                lineHeight: 1.15
            }
        }
    }
}

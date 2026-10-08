import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "components"

ColumnLayout {
    id: logs
    spacing: theme.gap
    property string content: app.logText()

    RowLayout {
        Layout.fillWidth: true
        Layout.margins: theme.pad
        SectionTitle { text: "Logs" }
        BigButton { compact: true; text: "Refresh"; onClicked: logs.content = app.logText() }
    }
    ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.leftMargin: theme.pad
        Layout.rightMargin: theme.pad
        Layout.bottomMargin: theme.pad
        TextArea {
            readOnly: true
            selectByMouse: true
            text: logs.content
            color: theme.text
            font.family: "monospace"
            font.pixelSize: 18
            wrapMode: TextArea.WrapAnywhere
            background: Rectangle { color: theme.surface; radius: theme.radius }
        }
    }
}

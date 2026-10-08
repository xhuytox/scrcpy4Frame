import QtQuick
import QtQuick.Layouts

// Read-only settings row with the value in the control column.
Item {
    id: row
    property string label: ""
    property string value: ""
    property bool term: false               // label styled as a heading instead of a dim key
    property bool mono: false
    property bool topDivider: true
    readonly property bool stacked: width < theme.stackBelow

    Layout.fillWidth: true
    implicitHeight: grid.y + grid.implicitHeight

    Divider {
        visible: row.topDivider
        width: parent.width
    }

    GridLayout {
        id: grid
        y: row.topDivider ? theme.gap : 0
        width: parent.width
        columns: row.stacked ? 1 : 2
        columnSpacing: 48
        rowSpacing: 6
        Text {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignTop
            text: row.label
            color: row.term ? theme.text : theme.textDim
            font.pixelSize: row.term ? theme.font : theme.fontSmall
            wrapMode: Text.WordWrap
        }
        Text {
            Layout.fillWidth: row.stacked
            Layout.preferredWidth: row.stacked ? -1 : theme.controlColumn
            Layout.minimumWidth: row.stacked ? 0 : theme.controlColumn
            Layout.alignment: Qt.AlignTop
            Layout.topMargin: row.term && !row.stacked ? 4 : 0   // first lines share a baseline
            text: row.value
            color: theme.text
            font.pixelSize: theme.fontSmall
            font.family: row.mono ? "monospace" : Qt.application.font.family
            wrapMode: row.mono ? Text.WrapAnywhere : Text.WordWrap
            lineHeight: 1.1
        }
    }
}

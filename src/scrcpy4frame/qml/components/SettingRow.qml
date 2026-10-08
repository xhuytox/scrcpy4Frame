import QtQuick
import QtQuick.Layouts

// Label and description on the left, control in a fixed-width column on the right.
// In narrow cards the control moves under the label unless `stackable` is false.
Item {
    id: row
    property string label: ""
    property string description: ""
    property bool topDivider: true          // off for the first row of a card
    property bool stackable: true
    property real controlWidth: theme.controlColumn
    property bool clickable: false          // the whole row is one pointer target
    signal clicked()
    default property alias control: slot.data
    readonly property bool stacked: stackable && width < theme.stackBelow

    Layout.fillWidth: true
    implicitHeight: grid.y + grid.implicitHeight

    Divider {
        visible: row.topDivider
        width: parent.width
    }

    // Hover/focus fill. Extends past the row edges so the text stays aligned with other rows.
    Rectangle {
        id: fill
        x: -16
        width: parent.width + 32
        y: grid.y - theme.gap + 1
        height: grid.height + 2 * theme.gap - 2
        radius: 16
        color: area.containsMouse ? theme.surface2 : "transparent"
        border.width: row.activeFocus ? 4 : 0
        border.color: theme.focus
    }

    GridLayout {
        id: grid
        y: row.topDivider ? theme.gap : 0
        width: parent.width
        columns: row.stacked ? 1 : 2
        columnSpacing: 48
        rowSpacing: 18
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            Text {
                Layout.fillWidth: true
                text: row.label
                color: theme.text
                font.pixelSize: theme.font
                wrapMode: Text.WordWrap
            }
            Text {
                Layout.fillWidth: true
                visible: row.description.length > 0
                text: row.description
                color: theme.textDim
                font.pixelSize: theme.fontSmall
                wrapMode: Text.WordWrap
            }
        }
        ColumnLayout {
            id: slot
            Layout.fillWidth: row.stacked
            Layout.preferredWidth: row.stacked ? -1 : row.controlWidth
            Layout.minimumWidth: row.stacked ? 0 : row.controlWidth
            Layout.maximumWidth: row.stacked ? Number.POSITIVE_INFINITY : row.controlWidth
            spacing: 12
        }
    }

    MouseArea {
        id: area
        anchors.fill: fill
        enabled: row.clickable
        hoverEnabled: row.clickable
        onClicked: row.clicked()
    }
}

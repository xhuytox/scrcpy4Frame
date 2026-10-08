import QtQuick
import QtQuick.Layouts

// Padded surface that lays out its children in a column.
Rectangle {
    id: card
    default property alias content: column.data
    property alias spacing: column.spacing
    property bool highlighted: false

    color: theme.surface
    radius: theme.radius + 4
    border.width: highlighted ? 2 : 1
    border.color: highlighted ? theme.accent : theme.border
    implicitHeight: column.implicitHeight + 2 * theme.pad
    implicitWidth: column.implicitWidth + 2 * theme.pad

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: theme.pad
        spacing: theme.gap
    }
}

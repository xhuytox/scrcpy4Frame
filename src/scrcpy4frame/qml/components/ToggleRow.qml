import QtQuick
import QtQuick.Layouts

// Toggle setting. The whole row is the pointer target.
SettingRow {
    id: row
    property bool checked: false
    signal toggled(bool checked)

    stackable: false
    controlWidth: 104
    clickable: true
    activeFocusOnTab: true
    opacity: enabled ? 1 : 0.5
    onClicked: row.toggled(!row.checked)
    Keys.onSpacePressed: row.toggled(!row.checked)
    Keys.onReturnPressed: row.toggled(!row.checked)
    Keys.onDownPressed: row.nextItemInFocusChain(true).forceActiveFocus()
    Keys.onUpPressed: row.nextItemInFocusChain(false).forceActiveFocus()

    Rectangle {
        Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
        implicitWidth: 104
        implicitHeight: 56
        radius: 28
        color: row.checked ? theme.accent : theme.surface3
        Rectangle {
            width: 44; height: 44; radius: 22
            y: 6
            x: row.checked ? parent.width - width - 6 : 6
            color: "white"
            Behavior on x { NumberAnimation { duration: 120 } }
        }
    }
}

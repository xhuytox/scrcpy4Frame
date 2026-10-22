import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Large choice on the wizard's first screen: icon, title and one line saying what it is.
Button {
    id: control
    property string glyph: ""
    property string title: ""
    property string description: ""

    implicitHeight: Math.max(theme.target * 1.6, row.implicitHeight + 2 * theme.gap)
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    Keys.onReturnPressed: control.clicked()
    Keys.onEnterPressed: control.clicked()
    Keys.onDownPressed: control.nextItemInFocusChain(true).forceActiveFocus()
    Keys.onRightPressed: control.nextItemInFocusChain(true).forceActiveFocus()
    Keys.onUpPressed: control.nextItemInFocusChain(false).forceActiveFocus()
    Keys.onLeftPressed: control.nextItemInFocusChain(false).forceActiveFocus()

    contentItem: Item {
        implicitHeight: row.implicitHeight
        RowLayout {
            id: row
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: theme.gap
            anchors.rightMargin: theme.gap
            anchors.verticalCenter: parent.verticalCenter
            spacing: 24
            Rectangle {
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: 80
                implicitHeight: 80
                radius: 22
                color: theme.surface3
                LineIcon { anchors.centerIn: parent; name: control.glyph; size: 48; color: theme.accent }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                Text {
                    Layout.fillWidth: true
                    text: control.title
                    color: theme.text
                    font.pixelSize: theme.fontLarge
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: control.description
                    color: theme.textDim
                    font.pixelSize: theme.font
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    background: Rectangle {
        radius: theme.radius
        color: control.down || control.hovered ? theme.surface3 : theme.surface2
        border.width: control.activeFocus ? 4 : 1
        border.color: control.activeFocus ? theme.focus : theme.border
    }
}

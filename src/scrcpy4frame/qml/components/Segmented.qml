import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Segmented picker. `options` is a list of {value, label}.
Rectangle {
    id: seg
    property var options: []
    property var current
    signal chosen(var value)

    implicitWidth: theme.controlColumn
    implicitHeight: theme.target * 0.75 + 12
    radius: theme.radius + 2
    color: theme.bg
    border.color: theme.border

    RowLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 4
        uniformCellSizes: true
        Repeater {
            model: seg.options
            delegate: Button {
                id: option
                required property var modelData
                readonly property bool selected: modelData.value === seg.current
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: modelData.label
                font.pixelSize: theme.fontSmall
                font.weight: selected ? Font.DemiBold : Font.Medium
                focusPolicy: Qt.StrongFocus
                hoverEnabled: true
                onClicked: seg.chosen(modelData.value)
                Keys.onReturnPressed: option.clicked()
                Keys.onEnterPressed: option.clicked()
                Keys.onDownPressed: option.nextItemInFocusChain(true).forceActiveFocus()
                Keys.onRightPressed: option.nextItemInFocusChain(true).forceActiveFocus()
                Keys.onUpPressed: option.nextItemInFocusChain(false).forceActiveFocus()
                Keys.onLeftPressed: option.nextItemInFocusChain(false).forceActiveFocus()

                contentItem: Text {
                    text: option.text
                    font: option.font
                    color: option.selected ? theme.accentText : theme.text
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    radius: theme.radius - 4
                    color: option.selected ? (option.hovered ? Qt.lighter(theme.accent, 1.12) : theme.accent)
                                           : (option.down || option.hovered ? theme.surface2 : "transparent")
                    border.width: option.activeFocus ? 4 : 0
                    border.color: theme.focus
                }
            }
        }
    }
}

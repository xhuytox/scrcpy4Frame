import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// On-screen keypad for entering codes and addresses with the VR pointer.
// Submit shares the bottom row with ⌫ and 0 to keep the pad short.
ColumnLayout {
    id: pad
    property string value: ""
    property int maxLength: 6
    property bool allowDot: false
    property bool showDisplay: true
    property bool showSubmit: true
    property bool external: false        // owner tracks the value via keyPressed()
    property string submitText: "Pair"
    property bool submitEnabled: value.length === maxLength
    signal submitted(string value)
    signal keyPressed(string key)
    spacing: 12

    readonly property int keyWidth: 138
    readonly property int keyHeight: 76
    readonly property int keyGap: 10
    readonly property int rowWidth: 3 * keyWidth + 2 * keyGap

    function press(key) {
        if (key === "submit") { if (submitEnabled) submitted(value); return }
        if (external) { keyPressed(key); return }
        if (key === "back") value = value.slice(0, -1)
        else if (value.length < maxLength) value += key
    }

    Keys.onPressed: (event) => {
        if (event.text >= "0" && event.text <= "9") { press(event.text); event.accepted = true }
        else if (event.text === "." && allowDot) { press("."); event.accepted = true }
        else if (event.key === Qt.Key_Backspace) { press("back"); event.accepted = true }
        else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && submitEnabled) {
            submitted(value); event.accepted = true
        }
    }

    Row {
        visible: pad.showDisplay
        Layout.alignment: Qt.AlignHCenter
        spacing: pad.keyGap
        Repeater {
            model: pad.maxLength
            delegate: Rectangle {
                required property int index
                width: (pad.rowWidth - (pad.maxLength - 1) * pad.keyGap) / pad.maxLength
                height: 76
                radius: 14
                color: theme.surface2
                border.width: index === pad.value.length ? 3 : 1
                border.color: index === pad.value.length ? theme.accent : theme.border
                Text {
                    anchors.centerIn: parent
                    text: pad.value.charAt(index)
                    color: theme.text
                    font.pixelSize: 42
                    font.weight: Font.DemiBold
                }
            }
        }
    }

    GridLayout {
        Layout.alignment: Qt.AlignHCenter
        columns: 3
        rowSpacing: pad.keyGap
        columnSpacing: pad.keyGap
        Repeater {
            model: pad.showSubmit
                   ? ["1", "2", "3", "4", "5", "6", "7", "8", "9", "back", "0", "submit"]
                   : ["1", "2", "3", "4", "5", "6", "7", "8", "9", pad.allowDot ? "." : "", "0", "back"]
            delegate: BigButton {
                required property string modelData
                readonly property bool isSubmit: modelData === "submit"
                Layout.preferredWidth: pad.keyWidth
                Layout.preferredHeight: pad.keyHeight
                // Keep the empty cell in the grid so 0 stays centred under 8.
                opacity: modelData === "" ? 0 : (enabled ? 1 : 0.4)
                enabled: modelData !== "" && (!isSubmit || pad.submitEnabled)
                kind: isSubmit ? "primary" : "secondary"
                text: modelData === "back" ? "⌫" : (isSubmit ? pad.submitText : modelData)
                font.pixelSize: isSubmit ? theme.font : 34
                Accessible.name: modelData === "back" ? "Delete" : text
                onClicked: pad.press(modelData)
            }
        }
    }
}

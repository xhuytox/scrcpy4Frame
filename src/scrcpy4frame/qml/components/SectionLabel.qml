import QtQuick
import QtQuick.Layouts

// Small uppercase heading inside a card.
Text {
    Layout.fillWidth: true
    color: theme.textDim
    font.pixelSize: theme.fontLabel
    font.weight: Font.DemiBold
    font.capitalization: Font.AllUppercase
    font.letterSpacing: 1.6
    elide: Text.ElideRight
}

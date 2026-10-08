import QtQuick
import QtQuick.Layouts

// Setting with a few fixed choices. `options` is a list of {value, label}.
SettingRow {
    id: choice
    property var options: []
    property var current
    signal chosen(var value)

    Segmented {
        Layout.fillWidth: true
        options: choice.options
        current: choice.current
        onChosen: (v) => choice.chosen(v)
    }
}

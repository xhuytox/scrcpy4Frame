import QtQuick
import QtQuick.Layouts

// Equal-width button cells, up to `maxColumns` per row and at least `minCellWidth` wide.
// Cell width doesn't depend on how many buttons are visible, so actions line up across cards.
Flow {
    id: grid
    property int maxColumns: 3
    property real minCellWidth: 380
    readonly property int columns: Math.max(1, Math.min(maxColumns,
                                                        Math.floor((width + spacing) / (minCellWidth + spacing))))
    readonly property real cellWidth: (width - (columns - 1) * spacing) / columns

    Layout.fillWidth: true
    spacing: theme.gap

    Component.onCompleted: {
        for (let i = 0; i < children.length; ++i)
            children[i].width = Qt.binding(function () { return grid.cellWidth })
    }
}

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Shapes

// Button sized for the VR pointer. Arrow keys move focus so Steam Input's gamepad-as-keyboard works.
Button {
    id: control
    property string kind: "secondary"   // primary | secondary | danger | ghost | dashed | toggle
    property bool isOn: false           // toggle: accent while on, outlined grey while off
    property bool compact: false
    property string glyph: ""           // LineIcon before the text
    property string trailingGlyph: ""   // LineIcon after the text
    property bool busy: false           // waiting on the device: a spinner takes the glyph's place
    property bool spinning: false       // busy for long enough to show it, so quick changes don't flicker
    readonly property color errorTone: theme.error
    readonly property color outlineTone: theme.textDim
    readonly property bool accented: kind === "primary" || (kind === "toggle" && isOn)

    implicitHeight: compact ? theme.target * 0.75 : theme.target
    implicitWidth: Math.max(compact ? 160 : 220, contentItem.implicitWidth + 2 * theme.pad)
    font.pixelSize: compact ? theme.fontSmall : theme.font
    font.weight: accented ? Font.DemiBold : Font.Medium
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true
    opacity: enabled || busy ? 1 : 0.4

    onBusyChanged: spinning = false
    Timer {
        interval: 250
        running: control.busy && !control.spinning
        onTriggered: control.spinning = true
    }

    Keys.onReturnPressed: control.clicked()
    Keys.onEnterPressed: control.clicked()
    Keys.onDownPressed: control.nextItemInFocusChain(true).forceActiveFocus()
    Keys.onRightPressed: control.nextItemInFocusChain(true).forceActiveFocus()
    Keys.onUpPressed: control.nextItemInFocusChain(false).forceActiveFocus()
    Keys.onLeftPressed: control.nextItemInFocusChain(false).forceActiveFocus()

    contentItem: Item {
        id: inner
        // The label gets whatever width the icons leave.
        readonly property real iconsWidth: (lead.visible ? lead.width + content.spacing : 0)
                                           + (spin.visible ? spin.width + content.spacing : 0)
                                           + (trail.visible ? trail.width + content.spacing : 0)
        implicitWidth: iconsWidth + label.implicitWidth
        implicitHeight: Math.max(label.implicitHeight, lead.visible ? lead.height : 0,
                                 spin.visible ? spin.height : 0)
        Row {
            id: content
            anchors.centerIn: parent
            spacing: 12
            LineIcon {
                id: lead
                anchors.verticalCenter: parent.verticalCenter
                name: control.spinning ? "" : control.glyph; color: label.color; size: control.compact ? 26 : 32
            }
            Spinner {
                id: spin
                anchors.verticalCenter: parent.verticalCenter
                visible: control.spinning
                color: label.color; size: control.compact ? 26 : 32
            }
            Text {
                id: label
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(0, Math.min(implicitWidth, inner.width - inner.iconsWidth))
                text: control.text
                font: control.font
                color: {
                    if (control.accented) return theme.accentText
                    if (control.kind === "danger") return theme.error
                    if (control.kind === "dashed" && !control.hovered) return theme.textDim
                    return theme.text
                }
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            LineIcon {
                id: trail
                anchors.verticalCenter: parent.verticalCenter
                name: control.trailingGlyph; color: label.color; size: control.compact ? 24 : 28
            }
        }
    }

    background: Rectangle {
        radius: theme.radius
        color: {
            if (control.accented)
                return control.down ? Qt.darker(theme.accent, 1.25) : (control.hovered ? Qt.lighter(theme.accent, 1.12) : theme.accent)
            if (control.kind === "ghost")
                return control.down || control.hovered ? theme.surface2 : "transparent"
            if (control.kind === "dashed")
                return control.down || control.hovered ? theme.surface : "transparent"
            if (control.kind === "danger")
                return Qt.tint(theme.surface2, Qt.rgba(control.errorTone.r, control.errorTone.g, control.errorTone.b,
                                                       control.down || control.hovered ? 0.22 : 0.12))
            return control.down ? theme.surface3 : (control.hovered ? theme.surface3 : theme.surface2)
        }
        border.width: {
            if (control.activeFocus) return 4
            if (control.kind === "toggle") return 2
            return control.kind === "ghost" || control.kind === "dashed" ? 0 : 1
        }
        border.color: {
            if (control.activeFocus) return theme.focus
            if (control.kind === "danger") return Qt.rgba(control.errorTone.r, control.errorTone.g, control.errorTone.b, 0.45)
            if (control.kind === "toggle")
                return control.isOn ? color : Qt.rgba(control.outlineTone.r, control.outlineTone.g, control.outlineTone.b, 0.5)
            return theme.border
        }

        Shape {
            id: dash
            anchors.fill: parent
            visible: control.kind === "dashed" && !control.activeFocus
            layer.enabled: visible
            layer.samples: 4
            ShapePath {
                strokeColor: control.hovered ? theme.textDim : theme.border
                strokeWidth: 2
                strokeStyle: ShapePath.DashLine
                dashPattern: [4, 3]
                fillColor: "transparent"
                PathRectangle { x: 1; y: 1; width: dash.width - 2; height: dash.height - 2; radius: theme.radius - 1 }
            }
        }
    }
}

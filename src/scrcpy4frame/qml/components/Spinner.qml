import QtQuick
import QtQuick.Shapes

// Turning arc for something that's waiting on the device. Drawn on LineIcon's 24-unit grid.
Item {
    id: spinner
    property color color: theme.text
    property real size: 32

    implicitWidth: size
    implicitHeight: size

    Shape {
        width: 24
        height: 24
        scale: spinner.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: spinner.color
            strokeWidth: 2.2
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathSvg { path: "M12 4 A8 8 0 1 1 4 12" }
        }
    }

    RotationAnimator on rotation {
        from: 0
        to: 360
        duration: 900
        loops: Animation.Infinite
        running: spinner.visible
    }
}

import QtQuick
import QtQuick.Shapes

// Stroke icon drawn on a 24-unit grid and scaled to `size`.
Item {
    id: icon
    property string name: ""
    property color color: theme.text
    property real size: 32

    readonly property var paths: ({
        "back": "M16 5 L7 12 L16 19 Z",
        "home": "M5 12 A7 7 0 1 0 19 12 A7 7 0 1 0 5 12 Z",
        "recents": "M8 5.5 H16 A2.5 2.5 0 0 1 18.5 8 V16 A2.5 2.5 0 0 1 16 18.5 H8 A2.5 2.5 0 0 1 5.5 16 V8 A2.5 2.5 0 0 1 8 5.5 Z",
        "eye": "M2 12 C3 9.5 7 5 12 5 C17 5 21 9.5 22 12 C21 14.5 17 19 12 19 C7 19 3 14.5 2 12 Z"
               + " M9 12 A3 3 0 1 0 15 12 A3 3 0 1 0 9 12 Z",
        "eyeOff": "M3 3 L21 21 M10.6 5.1 Q11.3 5 12 5 C17 5 21 9.5 22 12 C21.6 13 20.7 14.4 19.4 15.7"
                  + " M6.6 6.6 C4.4 8 2.8 10.1 2 12 C3 14.5 7 19 12 19 C13.9 19 15.6 18.4 17 17.5"
                  + " M9.9 9.9 A3 3 0 0 0 14.1 14.1",
        // As the overlay's Theater button (vr-mirror ui_draw.cpp corners_icon): four corners
        // pointing out while theater is off, in while it's on.
        "expand": "M6.8 4.8 H2 V9.6 M17.2 4.8 H22 V9.6 M6.8 19.2 H2 V14.4 M17.2 19.2 H22 V14.4",
        "shrink": "M2 9.6 H6.8 V4.8 M22 9.6 H17.2 V4.8 M2 14.4 H6.8 V19.2 M22 14.4 H17.2 V19.2",
        "recenter": "M8.5 12 A3.5 3.5 0 1 0 15.5 12 A3.5 3.5 0 1 0 8.5 12 Z M12 2.5 V6.5 M12 17.5 V21.5 M2.5 12 H6.5 M17.5 12 H21.5",
        "phone": "M9 2.5 H15 A2.5 2.5 0 0 1 17.5 5 V19 A2.5 2.5 0 0 1 15 21.5 H9 A2.5 2.5 0 0 1 6.5 19 V5 A2.5 2.5 0 0 1 9 2.5 Z M10.5 18.5 H13.5",
        "phoneOff": "M9 2.5 H15 A2.5 2.5 0 0 1 17.5 5 V19 A2.5 2.5 0 0 1 15 21.5 H9 A2.5 2.5 0 0 1 6.5 19 V5 A2.5 2.5 0 0 1 9 2.5 Z M3.5 3.5 L20.5 20.5",
        "headphones": "M4.5 17 V12.5 A7.5 7.5 0 0 1 19.5 12.5 V17"
                      + " M4.5 14 H6.5 A1.5 1.5 0 0 1 8 15.5 V18.5 A1.5 1.5 0 0 1 6.5 20 H6 A1.5 1.5 0 0 1 4.5 18.5 Z"
                      + " M19.5 14 H17.5 A1.5 1.5 0 0 0 16 15.5 V18.5 A1.5 1.5 0 0 0 17.5 20 H18 A1.5 1.5 0 0 0 19.5 18.5 Z",
        "headphonesOff": "M4.5 17 V12.5 A7.5 7.5 0 0 1 19.5 12.5 V17"
                         + " M4.5 14 H6.5 A1.5 1.5 0 0 1 8 15.5 V18.5 A1.5 1.5 0 0 1 6.5 20 H6 A1.5 1.5 0 0 1 4.5 18.5 Z"
                         + " M19.5 14 H17.5 A1.5 1.5 0 0 0 16 15.5 V18.5 A1.5 1.5 0 0 0 17.5 20 H18 A1.5 1.5 0 0 0 19.5 18.5 Z"
                         + " M3.5 3.5 L20.5 20.5",
        "tiles": "M5.5 4 H9.5 A1.5 1.5 0 0 1 11 5.5 V9.5 A1.5 1.5 0 0 1 9.5 11 H5.5 A1.5 1.5 0 0 1 4 9.5 V5.5 A1.5 1.5 0 0 1 5.5 4 Z"
                 + " M14.5 4 H18.5 A1.5 1.5 0 0 1 20 5.5 V9.5 A1.5 1.5 0 0 1 18.5 11 H14.5 A1.5 1.5 0 0 1 13 9.5 V5.5 A1.5 1.5 0 0 1 14.5 4 Z"
                 + " M5.5 13 H9.5 A1.5 1.5 0 0 1 11 14.5 V18.5 A1.5 1.5 0 0 1 9.5 20 H5.5 A1.5 1.5 0 0 1 4 18.5 V14.5 A1.5 1.5 0 0 1 5.5 13 Z"
                 + " M14.5 13 H18.5 A1.5 1.5 0 0 1 20 14.5 V18.5 A1.5 1.5 0 0 1 18.5 20 H14.5 A1.5 1.5 0 0 1 13 18.5 V14.5 A1.5 1.5 0 0 1 14.5 13 Z",
        "link": "M10 14 A4 4 0 0 0 15.7 14 L18.7 11 A4 4 0 0 0 13 5.3 L12 6.3"
                + " M14 10 A4 4 0 0 0 8.3 10 L5.3 13 A4 4 0 0 0 11 18.7 L12 17.7",
        "trash": "M4 7 H20 M9 7 V4.5 H15 V7 M6.5 7 L7.5 20 H16.5 L17.5 7",
        "check": "M5 12.5 L9.5 17 L19 7.5",
        "plus": "M12 5 V19 M5 12 H19",
        "download": "M12 3.5 V15 M7 10 L12 15 L17 10 M4.5 19.5 H19.5",
        "x": "M7 7 L17 17 M17 7 L7 17",
        "down": "M6 9 L12 15 L18 9",
        "up": "M6 15 L12 9 L18 15"
    })

    visible: name.length > 0
    implicitWidth: size
    implicitHeight: size

    Shape {
        width: 24
        height: 24
        scale: icon.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: icon.color
            strokeWidth: 2.2
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: icon.paths[icon.name] || "M12 12" }
        }
    }
}

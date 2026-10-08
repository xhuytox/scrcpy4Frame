import QtQuick

// Wheel and VR-stick scrolling that moves `flickable`'s content directly. Declare it inside the Flickable.
// Flickable's own wheel handling starts a flick, and while one runs Flickable swallows the next
// press to stop it, so a button pressed right after scrolling took two or more clicks.
WheelHandler {
    required property Flickable flickable
    target: null
    onWheel: (event) => {
        const f = flickable
        const dy = event.pixelDelta.y !== 0 ? event.pixelDelta.y
                                            : event.angleDelta.y / 120 * Qt.styleHints.wheelScrollLines * 24
        f.contentY = Math.max(f.originY, Math.min(f.contentY - dy, f.originY + f.contentHeight - f.height))
    }
}

import QtQuick

// Starts the update, shows how it's going, then restarts into it (Updater in updater.py). After the
// app, it updates older companions on connected devices.
BigButton {
    readonly property var u: app.appUpdate
    property bool named: false              // "Update to v1.2.3" rather than "Update"

    kind: "primary"
    compact: true
    busy: u.state === "downloading" || u.state === "installing" || u.state === "companion"
    enabled: !busy
    text: {
        switch (u.state) {
        case "downloading": return "Downloading " + Math.floor(u.progress * 100) + "%"
        case "installing": return "Installing…"
        case "companion": return "Updating companion…"
        case "installed": return u.canRestart ? "Restart" : "Quit"
        default: return named ? "Update to v" + u.latest : (u.error ? "Try again" : "Update")
        }
    }
    onClicked: u.state === "installed" ? app.restartForUpdate() : app.installUpdate()
}

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import "components"

ApplicationWindow {
    id: root
    width: 1600
    height: 1000
    minimumWidth: 960
    minimumHeight: 640
    visible: true
    visibility: app.gameMode ? Window.FullScreen : Window.Windowed
    title: "scrcpy4Frame"
    color: theme.bg

    property string page: "home"   // home | settings | logs

    function showWindow() {
        if (app.gameMode) root.showFullScreen()
        else root.show()
        root.raise()
        root.requestActivate()
    }

    Connections {
        target: app
        function onMirroringChanged() {
            // gamescope shows one window at a time, so hide while the scrcpy window is up.
            // The VR panel lives in SteamVR and doesn't compete.
            if (!app.gameMode) return
            if (app.mirroring && app.mirrorStatus.mode === "window") root.hide()
            else if (!root.visible) root.showWindow()
        }
        function onBanner(text, kind) { banner.show(text, kind) }
        function onToast(note) { toasts.add(note) }
        function onWizardActiveChanged() { if (!app.wizardActive) root.page = "home" }
    }

    header: Column {
        Rectangle {
            width: parent.width
            implicitHeight: 112
            color: theme.surface
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: theme.border }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: theme.pad
                anchors.rightMargin: theme.pad
                spacing: theme.gap
                Text {
                    text: "scrcpy4Frame"
                    color: theme.text
                    font.pixelSize: theme.fontLarge
                    font.weight: Font.Bold
                }
                StatusChip {
                    // Grey, not red: a device can be used over USB with no Wi-Fi at all.
                    status: app.network.online ? "ok" : "idle"
                    text: app.network.online ? ("Wi-Fi" + (app.network.ssid ? ": " + app.network.ssid : "") + "  ·  " + app.network.ip)
                                             : "Not connected to Wi-Fi"
                }
                StatusChip {
                    visible: app.discoveryError.length > 0
                    status: "error"
                    text: "Can't search for devices"
                }
                Item { Layout.fillWidth: true }
                BigButton {
                    visible: root.page !== "home" && !app.wizardActive && app.fatalError.length === 0
                    kind: "ghost"; compact: true; text: "‹ Home"
                    onClicked: root.page = "home"
                }
                BigButton {
                    visible: root.page === "home" && !app.wizardActive
                    kind: "ghost"; compact: true; text: "Settings"
                    onClicked: root.page = "settings"
                }
                BigButton {
                    visible: app.gameMode
                    kind: "ghost"; compact: true; text: "Quit"
                    onClicked: Qt.quit()
                }
            }
        }
        UpdateBanner { width: parent.width }
    }

    Loader {
        id: pageLoader
        anchors.fill: parent
        sourceComponent: {
            if (app.fatalError.length > 0 && root.page !== "logs") return fatalPage
            if (app.wizardActive) return wizardPage
            if (root.page === "settings") return settingsPage
            if (root.page === "logs") return logsPage
            return homePage
        }
    }

    Component { id: homePage; Home {} }
    Component { id: wizardPage; Wizard {} }
    Component { id: settingsPage; Settings { onShowLogs: root.page = "logs" } }
    Component { id: logsPage; Logs {} }
    Component {
        id: fatalPage
        Item {
            Card {
                anchors.centerIn: parent
                width: Math.min(parent.width - 2 * theme.pad, 1100)
                SectionTitle { text: "Something's missing" }
                Text {
                    Layout.fillWidth: true
                    text: app.fatalError
                    color: theme.text; font.pixelSize: theme.font; wrapMode: Text.WordWrap
                }
                BigButton { text: "View logs"; onClicked: { root.page = "logs" } }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        visible: !app.ready && app.fatalError.length === 0
        color: theme.bg
        StatusChip {
            anchors.centerIn: parent
            status: "working"
            text: "Starting…"
        }
    }

    Rectangle {
        id: banner
        property string kind: "info"
        function show(text, kind) {
            bannerText.text = text
            banner.kind = kind
            banner.visible = true
            bannerTimer.restart()
        }
        visible: false
        z: 10
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: theme.gap
        width: Math.min(parent.width - 2 * theme.pad, bannerText.implicitWidth + 2 * theme.pad)
        height: bannerText.implicitHeight + 2 * theme.gap
        radius: theme.radius
        color: kind === "error" ? "#4a2226" : theme.surface3
        border.color: kind === "error" ? theme.error : theme.accent
        Text {
            id: bannerText
            anchors.centerIn: parent
            width: Math.min(implicitWidth, banner.parent.width - 4 * theme.pad)
            color: theme.text
            font.pixelSize: theme.font
            wrapMode: Text.WordWrap
        }
        MouseArea { anchors.fill: parent; onClicked: banner.visible = false }
        Timer { id: bannerTimer; interval: 6000; onTriggered: banner.visible = false }
    }

    // In-app notification toasts, used when VR and desktop pop-ups aren't available.
    Column {
        id: toasts
        z: 11
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: theme.pad
        spacing: theme.gap
        width: 620
        property var items: []
        function add(note) {
            var list = items.slice(-2)
            list.push(note)
            items = list
            toastTimer.restart()
        }
        Timer { id: toastTimer; interval: 7000; onTriggered: toasts.items = [] }
        Repeater {
            model: toasts.items
            delegate: Rectangle {
                required property var modelData
                width: toasts.width
                height: toastRow.implicitHeight + 2 * theme.gap
                radius: theme.radius
                color: theme.surface3
                border.color: theme.border
                RowLayout {
                    id: toastRow
                    anchors.fill: parent
                    anchors.margins: theme.gap
                    spacing: theme.gap
                    Image {
                        Layout.preferredWidth: 72
                        Layout.preferredHeight: 72
                        visible: modelData.icon.length > 0
                        source: modelData.icon ? "data:image/png;base64," + modelData.icon : ""
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: modelData.appLabel; color: theme.textDim; font.pixelSize: theme.fontSmall }
                        Text { text: modelData.title; color: theme.text; font.pixelSize: theme.font; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                        Text { text: modelData.text; color: theme.text; font.pixelSize: theme.fontSmall; Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight }
                    }
                }
                MouseArea { anchors.fill: parent; onClicked: app.openNotification(modelData.uid) }
            }
        }
    }
}

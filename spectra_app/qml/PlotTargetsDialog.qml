import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    color: "#ffffff"
    width: 600
    height: 520
    focus: true
    Keys.onReturnPressed: if (bridge.canAccept) dialogHost.accept()
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        PlotTargetsList {
            bridge: root.bridge
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 16
        }
        PlotDialogFooter { bridge: root.bridge; dialogHost: root.dialogHost; Layout.fillWidth: true }
    }
}


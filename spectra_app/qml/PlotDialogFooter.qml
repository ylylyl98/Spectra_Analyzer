import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    required property var bridge
    required property var dialogHost
    property string actionText: "OK"
    implicitHeight: 58
    color: "#f5f7fa"
    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        Controls.Label {
            text: bridge.selectedCount + " selected"
            color: "#5f6e80"
            Layout.fillWidth: true
        }
        SurfaceButton { font: Qt.application.font;
            objectName: "dialogCancel"
            text: "Cancel"
            onClicked: dialogHost.reject()
            Accessible.name: "Cancel"
        }
        SurfaceButton { font: Qt.application.font;
            objectName: "dialogAccept"
            text: actionText
            primary: true
            enabled: bridge.canAccept
            onClicked: dialogHost.accept()
            Accessible.name: actionText
        }
    }
}


import QtQuick
import QtQuick.Layouts

SurfaceButton {
    id: control
    required property var bridge
    property string command: ""
    property string actionOwner: ""
    objectName: "ordinaryCommand_" + command
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    font: Qt.application.font
    implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
    Accessible.name: text
    onActiveFocusChanged: if (activeFocus) actionOwner = bridge.ownerToken
    onPressed: actionOwner = bridge.ownerToken
    onClicked: if (actionOwner === bridge.ownerToken) bridge.trigger(command, actionOwner)
}

import QtQuick
import QtQuick.Layouts

SurfaceButton {
    id: control
    required property var bridge
    property string actionKey
    property string actionOwner: ""
    signal triggered(string owner)
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    font: Qt.application.font
    implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
    Accessible.name: text
    onActiveFocusChanged: if (activeFocus) actionOwner = bridge.ownerToken
    onPressed: actionOwner = bridge.ownerToken
    onClicked: {
        if (actionOwner !== bridge.ownerToken) return
        if (actionKey) bridge.command(actionKey, actionOwner)
        triggered(actionOwner)
    }
}

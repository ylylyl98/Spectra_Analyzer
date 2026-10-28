import QtQuick
import QtQuick.Layouts

SurfaceButton {
    id: root
    required property var bridge
    property string commandName
    property string capturedOwner: ""
    signal starting()
    AppColors { id: colors }
    objectName: "xy-analysis-" + commandName
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    font: Qt.application.font
    implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
    Accessible.name: text
    onActiveFocusChanged: if (activeFocus) capturedOwner = bridge.ownerToken
    PointHandler { onActiveChanged: if (active) root.capturedOwner = root.bridge.ownerToken }
    onPressed: starting()
    onClicked: bridge.command(commandName, capturedOwner)
    contentItem: Text {
        text: root.text; font: root.font; textFormat: Text.PlainText
        color: !root.enabled ? colors.disabledText : root.primary ? colors.surface : colors.text
        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }
}

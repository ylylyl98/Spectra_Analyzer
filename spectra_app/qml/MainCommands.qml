import QtQuick
import QtQuick.Controls.Basic as Controls

Rectangle {
    id: root
    required property var bridge
    objectName: "commandStrip"
    implicitWidth: 760
    implicitHeight: commandFlow.implicitHeight + 12
    color: colors.surface
    Accessible.name: "Application commands"

    AppColors { id: colors }

    Flow {
        id: commandFlow
        x: 10
        y: 6
        width: Math.max(1, root.width - 20)
        spacing: 4

        Repeater {
            model: bridge.commands.actions

            SurfaceButton {
                required property var modelData
                objectName: "command_" + modelData.key
                height: 30
                quiet: true
                text: modelData.text.replace(/&(?!&)/g, "").replace(/&&/g, "&")
                enabled: modelData.enabled
                checkable: modelData.checkable
                Accessible.name: text
                Accessible.description: modelData.tooltip
                onClicked: modelData.trigger()

                Binding on checked { value: modelData.checked }
                Controls.ToolTip.visible: hovered && modelData.tooltip.length > 0
                Controls.ToolTip.text: modelData.tooltip
                Controls.ToolTip.delay: 650
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: colors.divider
    }
}

import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    objectName: "taskNavigation"
    implicitWidth: 300
    implicitHeight: 42
    color: colors.surface
    Accessible.name: "Properties tasks"

    AppColors { id: colors }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 6
        anchors.rightMargin: 6
        spacing: 2

        Repeater {
            model: root.bridge.labels

            Controls.Button {
                id: taskButton
                required property int index
                required property string modelData
                readonly property bool selected: root.bridge.currentIndex === index
                objectName: "task_" + index
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.preferredHeight: 36
                text: modelData
                enabled: root.bridge.enabled
                checkable: true
                hoverEnabled: true
                focusPolicy: Qt.StrongFocus
                padding: 5
                font: Qt.font({family: Qt.application.font.family, pointSize: Qt.application.font.pointSize,
                               weight: selected ? Font.DemiBold : Font.Normal})
                Accessible.name: modelData + " task"
                onClicked: root.bridge.setCurrentIndex(index)

                Binding on checked { value: taskButton.selected }

                contentItem: Text {
                    text: taskButton.text
                    font: taskButton.font
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                    color: !taskButton.enabled ? colors.disabledText
                           : taskButton.selected ? colors.accent : colors.secondary
                }

                background: Rectangle {
                    radius: 4
                    color: taskButton.down ? colors.pressed
                           : taskButton.hovered ? colors.hover : "transparent"
                    border.width: taskButton.visualFocus ? 2 : 0
                    border.color: colors.accent

                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        width: Math.max(16, parent.width - 22)
                        height: 2
                        radius: 1
                        visible: taskButton.selected
                        color: taskButton.enabled ? colors.accent : colors.disabledText
                    }
                }
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

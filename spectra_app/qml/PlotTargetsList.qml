import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    required property var bridge
    spacing: 6
    Controls.Label { text: "Active graph targets"; font.bold: true; color: "#263447" }
    Controls.ScrollView {
        objectName: "plotTargetsScroll"
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        contentWidth: availableWidth
        ListView {
            id: targetList
            objectName: "plotTargetsList"
            model: bridge.targetItems
            spacing: 2
            boundsBehavior: Flickable.StopAtBounds
            delegate: Controls.CheckDelegate {
                required property var modelData
                width: targetList.width
                implicitHeight: Math.max(30, contentItem.implicitHeight + 8)
                objectName: "plotTarget_" + modelData.datasetId
                checked: modelData.checked
                onToggled: modelData.setChecked(checked)
                Accessible.name: modelData.label
                hoverEnabled: true
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: modelData.tooltip
                contentItem: Text {
                    text: modelData.label
                    color: "#263447"
                    font: Qt.application.font
                    wrapMode: Text.Wrap
                    rightPadding: 32
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }
    }
    RowLayout {
        SurfaceButton { font: Qt.application.font; objectName: "plotTargetsAll"; text: "Select all"; onClicked: bridge.selectAll(true) }
        SurfaceButton { font: Qt.application.font; objectName: "plotTargetsClear"; text: "Clear"; onClicked: bridge.selectAll(false) }
        Item { Layout.fillWidth: true }
    }
}


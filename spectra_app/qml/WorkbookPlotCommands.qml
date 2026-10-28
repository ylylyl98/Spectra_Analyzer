import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property QtObject bridge
    property font controlFont: Qt.application.font
    implicitHeight: commands.implicitHeight + 8
    color: colors.surface
    AppColors { id: colors }
    Flow {
        id: commands
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 4
        y: 4
        spacing: 4
        Text {
            objectName: "workbookSourceContext"
            width: Math.min(170, Math.max(60, root.width - 12))
            height: 32
            text: root.bridge.sourceText
            font: root.controlFont
            color: colors.secondary
            textFormat: Text.PlainText
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            MouseArea { anchors.fill: parent; hoverEnabled: true; Controls.ToolTip.visible: containsMouse; Controls.ToolTip.text: root.bridge.sourceTooltip }
        }
        Repeater {
            model: [{label: "Line", value: "line"}, {label: "Scatter", value: "scatter"}, {label: "Line+Symbol", value: "line_symbol"}]
            delegate: SurfaceButton {
                required property var modelData
                objectName: "workbookPlot_" + modelData.value
                text: modelData.label
                font: root.controlFont
                enabled: root.bridge.ready
                onClicked: root.bridge.plot(modelData.value)
            }
        }
        SurfaceButton { objectName: "workbookHeatmap"; text: "Heatmap"; font: root.controlFont; enabled: root.bridge.heatmapReady; onClicked: root.bridge.plot("heatmap"); Controls.ToolTip.visible: hovered; Controls.ToolTip.text: "DAT Original or Processed physical Gate columns" }
        SurfaceButton { objectName: "workbookAppend"; text: "Add to…"; font: root.controlFont; enabled: root.bridge.ready && root.bridge.hasTarget; onClicked: root.bridge.append(); Controls.ToolTip.visible: hovered; Controls.ToolTip.text: "Append Line curves to " + root.bridge.targetLabel + "; existing curves are skipped" }
        SurfaceButton {
            objectName: "workbookChooseTarget"
            text: root.bridge.targetLabel + " ▾"
            font: root.controlFont
            width: Math.min(180, Math.max(80, root.width - 12))
            enabled: root.bridge.targets.length > 0
            contentItem: Text { text: parent.text; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
            onClicked: if (root.bridge.openTargets()) { targets.ownerSheet = root.bridge.boundSheet; targets.popup() }
            Accessible.name: "Choose comparison destination"
            Controls.Menu {
                id: targets
                objectName: "workbookTargetMenu"
                property var ownerSheet: null
                Repeater {
                    model: root.bridge.targets
                    delegate: Controls.MenuItem {
                        required property var modelData
                        objectName: "workbookTarget_" + modelData.value
                        text: modelData.label
                        contentItem: Text { text: modelData.label; textFormat: Text.PlainText; font: root.controlFont; color: colors.text }
                        checkable: true
                        checked: root.bridge.targetId === modelData.value
                        onTriggered: if (targets.ownerSheet) targets.ownerSheet.chooseTarget(modelData.value)
                    }
                }
            }
        }
        SurfaceButton { objectName: "workbookPinTarget"; text: "Pin target"; font: root.controlFont; checkable: true; checked: root.bridge.targetPinned; enabled: root.bridge.hasTarget || checked; onClicked: root.bridge.pinTarget(checked) }
        SurfaceButton { objectName: "workbookShowTarget"; text: "Show target"; font: root.controlFont; enabled: root.bridge.hasTarget; onClicked: root.bridge.showTarget() }
        SurfaceButton { objectName: "workbookAddAgain"; text: "Add again"; font: root.controlFont; enabled: root.bridge.ready && root.bridge.hasTarget; onClicked: root.bridge.repeat() }
    }
}

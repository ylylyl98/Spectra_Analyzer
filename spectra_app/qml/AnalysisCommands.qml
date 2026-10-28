import QtQuick
import QtQuick.Controls.Basic as Controls

Rectangle {
    id: root
    objectName: "analysisCommands"
    required property var bridge
    property font controlFont: Qt.application.font
    implicitWidth: 440
    implicitHeight: commands.implicitHeight
    color: colors.surface
    Accessible.name: "Analysis commands"
    AppColors { id: colors }
    component Command: AnalysisAction {
        bridge: root.bridge
        font: root.controlFont
        implicitHeight: Math.max(28, contentItem.implicitHeight + 8)
        leftPadding: 8; rightPadding: 8; topPadding: 4; bottomPadding: 4
        objectName: "command_" + actionKey
        enabled: root.bridge.enabled && !root.bridge.busy
        width: Math.min(implicitWidth, commands.width)
    }
    Flow {
        id: commands
        x: 2; y: 0; width: Math.max(1, root.width - 4)
        spacing: 4
        onWidthChanged: forceLayout()
        Command {
            actionKey: "source"; text: "Source"
            enabled: root.bridge.enabled && !root.bridge.busy && root.bridge.sourceAvailable
            Accessible.name: "Source graph"
            Accessible.description: "Return to the graph used as this analysis source."
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: "Source graph"
        }
        Command {
            actionKey: "original"; text: "Original"
            Accessible.name: "Original data"
            Accessible.description: "Return to the original data view."
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: "Original data"
        }
        Command { actionKey: "results"; text: "Results" }
        Command { actionKey: "duplicate"; text: "Duplicate" }
        Command {
            id: exports
            objectName: "command_exportMenu"
            text: "Export ▾"
            onTriggered: function(owner) { exportMenu.actionOwner = owner; exportMenu.open() }
            Controls.Menu {
                id: exportMenu
                objectName: "analysisExportMenu"
                property string actionOwner: ""
                popupType: Controls.Popup.Window
                y: exports.height + 3
                width: Math.max(200, implicitWidth)
                font: root.controlFont
                background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                component Export: Controls.MenuItem {
                    property string actionKey
                    objectName: "command_" + actionKey
                    font: root.controlFont
                    Accessible.name: text
                    onTriggered: if (exportMenu.actionOwner === root.bridge.ownerToken && root.bridge.enabled && !root.bridge.busy)
                                     root.bridge.command(actionKey, exportMenu.actionOwner)
                    contentItem: Text { text: parent.text; font: root.controlFont; color: colors.text; verticalAlignment: Text.AlignVCenter }
                    background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
                }
                Export { actionKey: "exportSvg"; text: "Export SVG…" }
                Export { actionKey: "exportPng"; text: "Export PNG…" }
                Export { actionKey: "copySvg"; text: "Copy SVG" }
            }
        }
        SurfaceCheckBox {
            id: auxiliary
            property string actionOwner: ""
            objectName: "command_auxLinecut"
            text: "Linecut"
            font: root.controlFont
            width: Math.min(implicitWidth, commands.width)
            height: Math.max(28, contentItem.implicitHeight + 8)
            enabled: root.bridge.enabled && !root.bridge.busy
            Accessible.name: "Linecut preview"
            Accessible.description: "Show or hide the auxiliary current-Gate linecut preview."
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: "Show linecut preview"
            onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
            onPressed: actionOwner = root.bridge.ownerToken
            onClicked: if (actionOwner === root.bridge.ownerToken) root.bridge.setAuxLinecut(checked, actionOwner)
            Binding on checked { value: root.bridge.auxLinecutVisible }
        }
    }
    Connections {
        target: root.bridge
        function onStateChanged() {
            if (!root.bridge.enabled || root.bridge.busy || exportMenu.actionOwner !== root.bridge.ownerToken)
                exportMenu.close()
        }
    }
}

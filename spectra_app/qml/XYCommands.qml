import QtQuick
import QtQuick.Controls.Basic as Controls

Rectangle {
    id: root
    objectName: "XYCommandsRoot"
    required property var bridge
    property font controlFont: Qt.application.font
    implicitWidth: 680
    implicitHeight: 40
    color: colors.surface
    Accessible.name: "XY comparison commands"
    AppColors { id: colors }
    FontMetrics { id: metrics; font: root.controlFont }
    function action(key) {
        const rows = bridge ? bridge.actions : []
        for (let i = 0; i < rows.length; ++i) if (rows[i].key === key) return rows[i]
        return {key: key, text: "", tooltip: "", enabled: false, checked: false, checkable: false}
    }
    function commandWidth(key) { return Math.ceil(metrics.advanceWidth(action(key).text)) + 20 }
    readonly property var styleKeys: (bridge ? bridge.actions : []).filter(function(row) { return row.key.indexOf("style:") === 0 }).map(function(row) { return row.key })
    readonly property bool showResume: action("resume").text.length > 0 && width >= 450
    readonly property int styleCount: {
        let reserve = commandWidth("analyze") + 92 + (showResume ? commandWidth("resume") + 4 : 0)
        let all = 0
        for (let i = 0; i < styleKeys.length; ++i) all += commandWidth(styleKeys[i]) + 4
        if (all + reserve + 8 <= width && (showResume || !action("resume").text.length)) return styleKeys.length
        let count = 0, used = reserve + 62
        for (let i = 0; i < styleKeys.length; ++i) {
            if (used + commandWidth(styleKeys[i]) + 4 > width) break
            used += commandWidth(styleKeys[i]) + 4
            ++count
        }
        return count
    }
    readonly property var overflowKeys: styleKeys.slice(styleCount).concat(!showResume && action("resume").text.length ? ["resume"] : [])
    component Command: SurfaceButton {
        id: button
        property string command
        property var actionRow: root.action(command)
        property string actionOwner: ""
        objectName: "XYCommand_" + command.replace(":", "_")
        text: actionRow.text
        width: root.commandWidth(command); height: 30
        font: root.controlFont
        leftPadding: 8; rightPadding: 8; topPadding: 3; bottomPadding: 3
        enabled: root.bridge && root.bridge.enabled && actionRow.enabled
        checkable: actionRow.checkable
        Binding on checked { value: button.actionRow.checked }
        Accessible.name: text
        Accessible.description: actionRow.tooltip
        onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge ? root.bridge.ownerToken : ""
        PointHandler { onActiveChanged: if (active) button.actionOwner = root.bridge ? root.bridge.ownerToken : "" }
        onClicked: if (root.bridge) root.bridge.invoke(command, actionOwner)
        Controls.ToolTip.delay: 650
        Controls.ToolTip.visible: hovered && actionRow.tooltip.length > 0
        Controls.ToolTip.text: actionRow.tooltip
        Connections {
            target: root.bridge
            function onStateChanged() {
                if (!root.bridge || !root.bridge.enabled || button.actionOwner !== root.bridge.ownerToken) button.actionOwner = ""
            }
        }
    }
    Row {
        id: leading
        x: 4; y: 5; spacing: 4
        Command { command: "style:line"; visible: root.styleCount > 0 }
        Command { command: "style:scatter"; visible: root.styleCount > 1 }
        Command { command: "style:line_symbol"; visible: root.styleCount > 2 }
        Command { command: "analyze" }
    }
    Text {
        objectName: "XYSelectionCount"
        x: leading.x + leading.width + 6; y: 5
        width: Math.max(0, trailing.x - x - 6); height: 30
        text: root.bridge ? root.bridge.selectionText : ""; textFormat: Text.PlainText
        font: root.controlFont; color: colors.secondary
        elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
        Accessible.name: text
        HoverHandler { id: selectionHover }
        Controls.ToolTip.delay: 650
        Controls.ToolTip.visible: selectionHover.hovered
        Controls.ToolTip.text: text
    }
    Row {
        id: trailing
        x: root.width - width - 4; y: 5; spacing: 4
        Command { command: "resume"; visible: root.showResume }
        SurfaceButton {
            id: more
            objectName: "XYCommandsOverflow"
            property string actionOwner: ""
            visible: root.overflowKeys.length > 0
            enabled: root.bridge && root.bridge.enabled
            text: "More"; width: 54; height: 30
            font: root.controlFont
            leftPadding: 5; rightPadding: 5
            Accessible.name: "More comparison commands"
            onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge ? root.bridge.ownerToken : ""
            PointHandler { onActiveChanged: if (active) more.actionOwner = root.bridge ? root.bridge.ownerToken : "" }
            onClicked: if (root.bridge && actionOwner === root.bridge.ownerToken && root.bridge.enabled) {
                overflow.actionOwner = actionOwner
                overflow.open()
            }
            Controls.Menu {
                id: overflow
                objectName: "XYCommandsOverflowMenu"
                property string actionOwner: ""
                popupType: Controls.Popup.Window
                x: more.width - width; y: more.height + 3
                width: 240; padding: 4
                background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                Instantiator {
                    model: root.overflowKeys
                    onObjectAdded: function(index, object) { overflow.insertItem(index, object) }
                    onObjectRemoved: function(index, object) { overflow.removeItem(object) }
                    Controls.MenuItem {
                        id: entry
                        required property string modelData
                        property var actionRow: root.action(modelData)
                        objectName: "XYOverflow_" + modelData.replace(":", "_")
                        text: actionRow.text
                        enabled: root.bridge && root.bridge.enabled && actionRow.enabled
                        checkable: actionRow.checkable
                        Binding on checked { value: entry.actionRow.checked }
                        font: root.controlFont
                        implicitHeight: 32
                        Accessible.name: text
                        Accessible.description: actionRow.tooltip
                        onTriggered: if (root.bridge) root.bridge.invoke(modelData, overflow.actionOwner)
                        contentItem: Text { text: entry.text; textFormat: Text.PlainText; font: root.controlFont; color: entry.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { color: entry.highlighted ? colors.selected : colors.surface; border.color: entry.visualFocus ? colors.accent : "transparent" }
                    }
                }
            }
        }
    }
    Connections {
        target: root.bridge
        function onStateChanged() {
            if (!root.bridge || !root.bridge.enabled || overflow.actionOwner !== root.bridge.ownerToken) overflow.close()
            if (!root.bridge || !root.bridge.enabled || more.actionOwner !== root.bridge.ownerToken) more.actionOwner = ""
        }
    }
}



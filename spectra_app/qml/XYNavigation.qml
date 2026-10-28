import QtQuick
import QtQuick.Controls.Basic as Controls

Rectangle {
    id: root
    objectName: "XYNavigationRoot"
    required property var bridge
    property font controlFont: Qt.application.font
    implicitWidth: 560
    implicitHeight: Math.max(40, commands.implicitHeight + 8)
    color: colors.surface
    Accessible.name: "XY plot navigation"
    AppColors { id: colors }
    function action(key) {
        const rows = bridge ? bridge.actions : []
        for (let i = 0; i < rows.length; ++i) if (rows[i].key === key) return rows[i]
        return {text: "", tooltip: "", enabled: false, checked: false}
    }
    component NavButton: SurfaceButton {
        id: button
        property string command: ""
        property string actionOwner: ""
        property string description: text
        height: 30
        font: root.controlFont
        leftPadding: 8; rightPadding: 8; topPadding: 3; bottomPadding: 3
        enabled: root.bridge && root.bridge.enabled
        Accessible.name: description
        onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge ? root.bridge.ownerToken : ""
        PointHandler { onActiveChanged: if (active) button.actionOwner = root.bridge ? root.bridge.ownerToken : "" }
        onClicked: if (command.length && root.bridge) root.bridge.invoke(command, actionOwner)
        Controls.ToolTip.delay: 650
        Controls.ToolTip.visible: hovered
        Controls.ToolTip.text: description
        Connections {
            target: root.bridge
            function onStateChanged() {
                if (!root.bridge.enabled || button.actionOwner !== root.bridge.ownerToken)
                    button.actionOwner = ""
            }
        }
    }
    Flow {
        id: commands
        x: 4; y: 4; width: Math.max(1, root.width - 8)
        spacing: 3
        NavButton { objectName: "XYNavigation_home"; text: "Home"; command: "home"; description: "Reset original view" }
        NavButton { objectName: "XYNavigation_back"; text: "Back"; command: "back"; enabled: root.bridge && root.bridge.enabled && root.bridge.backEnabled; description: "Back to previous view" }
        NavButton { objectName: "XYNavigation_forward"; text: "Forward"; command: "forward"; enabled: root.bridge && root.bridge.enabled && root.bridge.forwardEnabled; description: "Forward to next view" }
        NavButton { objectName: "XYNavigation_pan"; text: "Pan"; command: "pan"; checkable: true; Binding on checked { value: root.bridge && root.bridge.mode === "pan/zoom" } description: "Pan axes with left mouse; scale with right mouse" }
        NavButton { objectName: "XYNavigation_zoom"; text: "Zoom"; command: "zoom"; checkable: true; Binding on checked { value: root.bridge && root.bridge.mode === "zoom rect" } description: "Zoom to rectangle" }
        Row {
            spacing: 0
            NavButton {
                objectName: "XYAddDataButton"; text: root.action("add").text || "Add data…"; command: "add"
                enabled: root.bridge && root.bridge.enabled && root.action("add").enabled
                description: "Add data from existing Sheets"
            }
            NavButton {
                id: addOptions
                objectName: "XYAddDataOptions"
                text: "▾"; width: 26; description: "Choose how to add data"
                enabled: root.bridge && root.bridge.enabled && (root.action("addSheets").enabled || root.action("addFiles").enabled)
                onClicked: if (root.bridge && actionOwner === root.bridge.ownerToken && root.bridge.enabled) {
                    addMenu.actionOwner = actionOwner
                    addMenu.open()
                }
                Controls.Menu {
                    id: addMenu
                    objectName: "XYAddDataMenu"
                    property string actionOwner: ""
                    popupType: Controls.Popup.Window
                    x: Math.min(0, addOptions.width - width); y: addOptions.height + 3
                    width: 210; padding: 4
                    background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                    component AddItem: Controls.MenuItem {
                        id: entry
                        property string command
                        property var actionRow: root.action(command)
                        text: actionRow.text
                        enabled: root.bridge && root.bridge.enabled && actionRow.enabled
                        font: root.controlFont
                        implicitHeight: 32
                        Accessible.name: text
                        onTriggered: if (root.bridge) root.bridge.invoke(command, addMenu.actionOwner)
                        contentItem: Text { text: entry.text; textFormat: Text.PlainText; font: root.controlFont; color: entry.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { color: entry.highlighted ? colors.selected : colors.surface; border.color: entry.visualFocus ? colors.accent : "transparent" }
                    }
                    AddItem { objectName: "XYAddSheets"; command: "addSheets" }
                    AddItem { objectName: "XYAddFiles"; command: "addFiles" }
                }
            }
        }
        NavButton {
            objectName: "XYInspectorToggle"; command: "inspector"; text: root.action("inspector").text || "Inspector"
            enabled: root.bridge && root.bridge.enabled && root.action("inspector").enabled
            checkable: true
            Binding on checked { value: root.bridge && root.bridge.inspectorChecked }
            description: "Show comparison inspector"
        }
        Text {
            objectName: "XYNavigationMessage"
            visible: root.width > 700
            width: Math.max(0, root.width - 600); height: 30
            text: root.bridge ? root.bridge.message : ""; textFormat: Text.PlainText
            font: root.controlFont; color: colors.secondary
            elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
            Accessible.name: text
        }
    }
    Connections {
        target: root.bridge
        function onStateChanged() {
            if (!root.bridge.enabled || addMenu.actionOwner !== root.bridge.ownerToken) addMenu.close()
        }
    }
}





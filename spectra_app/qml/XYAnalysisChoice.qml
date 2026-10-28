import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    required property string field
    property string label
    property var options: []
    property string selected: bridge ? String(bridge.values[field] || "") : ""
    property string token: ""
    property string owner: ""
    property bool popupSession: false
    property alias editorItem: choice
    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 3
    AppColors { id: colors }
    Text { Layout.fillWidth: true; text: root.label; font: Qt.application.font; color: colors.text; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    Controls.ComboBox {
        id: choice
        objectName: "xy-analysis-" + root.field
        Layout.fillWidth: true; Layout.minimumWidth: 0
        implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
        font: Qt.application.font; model: root.options
        Accessible.name: root.label
        function capture() {
            if (root.token) root.bridge.endEdit(root.token)
            root.owner = root.bridge.ownerToken
            root.token = root.bridge.beginEdit(root.field)
        }
        onActiveFocusChanged: {
            if (activeFocus && !root.token) capture()
            else if (!activeFocus) Qt.callLater(function() { if (choice && root && !choice.activeFocus && !root.popupSession) root.retire() })
        }
        PointHandler { onActiveChanged: if (active) choice.capture() }
        Keys.onPressed: function(event) {
            if (!root.token && root.owner === root.bridge.ownerToken && choice.activeFocus && (event.key === Qt.Key_Up || event.key === Qt.Key_Down || event.key === Qt.Key_Home || event.key === Qt.Key_End || event.key === Qt.Key_Space || event.key === Qt.Key_Return)) capture()
        }
        onActivated: if (root.token) root.bridge.choose(root.field, currentText, root.token)
        Connections {
            target: choice.popup
            function onAboutToShow() {
                root.popupSession = true
                if (!root.token && root.owner === root.bridge.ownerToken) choice.capture()
                if (!root.token) popupRetirement.start()
            }
            function onClosed() { root.popupSession = false; if (root.token) root.bridge.endEdit(root.token); root.token = "" }
        }
        Connections {
            target: root.bridge
            function onChanged() {
                if (root.owner && root.owner !== root.bridge.ownerToken) {
                    choice.popup.close()
                    if (root.token) root.bridge.endEdit(root.token)
                    root.token = ""
                    choice.focus = false
                }
            }
        }
        Binding on currentIndex { value: root.options.indexOf(root.selected); when: !choice.popup.visible }
        Component.onCompleted: {
            popup.popupType = Controls.Popup.Window
            popup.y = Qt.binding(function() { return choice.height + 3 })
            popup.height = Qt.binding(function() { return Math.min(root.options.length * Math.max(34, choice.implicitHeight) + popup.topPadding + popup.bottomPadding, Math.max(120, choice.Screen.desktopAvailableHeight - 30)) })
        }
        contentItem: Text { text: choice.displayText; font: choice.font; color: choice.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText; elide: Text.ElideRight }
        background: Rectangle { radius: 4; color: choice.enabled ? colors.surface : colors.disabled; border.color: choice.visualFocus ? colors.accent : colors.border; border.width: choice.visualFocus ? 2 : 1 }
        delegate: Controls.ItemDelegate {
            required property string modelData
            required property int index
            width: choice.width; implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
            highlighted: choice.highlightedIndex === index
            contentItem: Text { text: modelData; font: Qt.application.font; color: colors.text; textFormat: Text.PlainText }
            background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
        }
    }
    Timer { id: popupRetirement; interval: 0; onTriggered: choice.popup.close() }
    function retire() { choice.popup.close(); if (token) bridge.endEdit(token); token = "" }
    Component.onDestruction: if (bridge && token) bridge.endEdit(token)
}

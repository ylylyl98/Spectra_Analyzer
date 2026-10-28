import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    property string field
    property string label
    property bool labelVisible: true
    property string selected: ""
    property var choices: []
    property font controlFont: Qt.application.font
    property alias editorItem: choice
    property string selectionOwner: ""
    signal chosen(string value, string owner)
    signal interactionStarted(string owner)
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    spacing: 3
    AppColors { id: colors }
    Text { visible: root.labelVisible; Layout.fillWidth: true; Layout.minimumWidth: 0; text: root.label; font: root.controlFont; color: root.enabled ? colors.text : colors.disabledText; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    Controls.ComboBox {
        id: choice
        objectName: root.field
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
        model: root.choices
        textRole: "label"
        valueRole: "value"
        font: root.controlFont
        leftPadding: 8; rightPadding: 25
        hoverEnabled: true
        Accessible.name: root.label
        function capture() { root.selectionOwner = root.bridge.ownerToken; root.interactionStarted(root.selectionOwner) }
        onActiveFocusChanged: if (activeFocus) capture()
        onPressedChanged: if (pressed) capture()
        onActivated: if (root.selectionOwner === root.bridge.ownerToken) root.chosen(currentValue, root.selectionOwner)
        Connections { target: choice.popup; function onAboutToShow() { choice.capture() } }
        Connections { target: root.bridge; function onStateChanged() { if (root.selectionOwner !== root.bridge.ownerToken) choice.popup.close() } }
        Binding on currentIndex {
            value: {
                for (let i = 0; i < root.choices.length; ++i)
                    if (String(root.choices[i].value) === root.selected) return i
                return -1
            }
        }
        Component.onCompleted: {
            popup.popupType = Controls.Popup.Window
            popup.y = Qt.binding(function() { return choice.height + 3 })
            popup.height = Qt.binding(function() { return Math.min(popup.contentItem.implicitHeight, Math.max(100, choice.Screen.desktopAvailableHeight - popup.topMargin - popup.bottomMargin)) })
        }
        contentItem: Text { text: choice.displayText; font: root.controlFont; color: choice.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; textFormat: Text.PlainText }
        indicator: Text { x: choice.width - width - 8; y: (choice.height - height) / 2; text: "⌄"; font: root.controlFont; color: colors.secondary }
        background: Rectangle { radius: 4; color: !choice.enabled ? colors.disabled : choice.down ? colors.pressed : choice.hovered ? colors.hover : colors.surface; border.width: choice.visualFocus ? 2 : 1; border.color: choice.visualFocus ? colors.accent : colors.border }
        delegate: Controls.ItemDelegate {
            required property var modelData
            required property int index
            width: choice.width
            implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
            highlighted: choice.highlightedIndex === index
            contentItem: Text { text: modelData.label; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap; textFormat: Text.PlainText }
            background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
        }
    }
}

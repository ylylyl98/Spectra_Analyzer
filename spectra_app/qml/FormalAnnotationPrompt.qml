import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Controls.Pane {
    id: root
    objectName: "formalAnnotationPrompt"
    required property var bridge
    required property var dialogHost
    property font controlFont: Qt.application.font
    font: controlFont
    implicitWidth: 520
    implicitHeight: 360
    padding: 14
    onActiveFocusChanged: if (activeFocus) Qt.callLater(function() { promptText.forceActiveFocus() })
    AppColors { id: colors }
    background: Rectangle { color: colors.surface }
    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        Text {
            Layout.fillWidth: true
            text: root.bridge.kind === "arrow" ? "Optional number / label:" : "Text (Unicode supported):"
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            font: root.controlFont
            color: colors.text
        }
        Controls.ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 0
            Layout.minimumHeight: 90
            contentWidth: availableWidth
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
            Controls.TextArea {
                id: promptText
                objectName: "annotation_prompt_text"
                font: root.controlFont
                enabled: root.bridge.enabled
                color: enabled ? colors.text : colors.disabledText
                textFormat: TextEdit.PlainText
                wrapMode: TextEdit.Wrap
                selectByMouse: true
                padding: 8
                Accessible.name: root.bridge.kind === "arrow" ? "Optional arrow number or label" : "Annotation text"
                Binding on text { value: root.bridge.text }
                onTextChanged: if (root.bridge.enabled && text !== root.bridge.text) root.bridge.setText(text)
                Component.onCompleted: { forceActiveFocus(); selectAll() }
                background: Rectangle { color: promptText.enabled ? colors.surface : colors.disabled; radius: 4; border.width: promptText.activeFocus ? 2 : 1; border.color: promptText.activeFocus ? colors.accent : colors.border }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            SurfaceButton {
                objectName: "annotation_prompt_cancel"
                text: "Cancel"
                font: root.controlFont
                implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
                onClicked: root.dialogHost.reject()
            }
            SurfaceButton {
                objectName: "annotation_prompt_accept"
                text: root.bridge.kind === "arrow" ? "Add arrow" : "Add text"
                font: root.controlFont
                implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
                primary: true
                enabled: root.bridge.enabled && root.bridge.canAccept
                onClicked: root.dialogHost.accept()
            }
        }
    }
}

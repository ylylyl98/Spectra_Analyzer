import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    required property string field
    property string label
    property bool labelVisible: true
    property string value: ""
    property string hint: ""
    property string placeholder: ""
    property string draftKind: "number"
    property string seedId: ""
    property int gate: -1
    property bool suppressFinish: false
    property var mayFinish: null
    property font controlFont: Qt.application.font
    property alias editorItem: editor
    readonly property string errorKey: seedId ? seedId + ":" + field : field
    readonly property bool invalid: !!bridge && bridge.errorField === errorKey && !!bridge.errorText
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    spacing: 3
    AppColors { id: colors }
    Text { visible: root.labelVisible; Layout.fillWidth: true; Layout.minimumWidth: 0; text: root.label; font: root.controlFont; color: root.enabled ? colors.text : colors.disabledText; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    Controls.TextField {
        id: editor
        property string editOwner: ""
        property int editGate: -1
        property string editSeed: ""
        objectName: root.errorKey
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        implicitHeight: Math.max(34, contentHeight + topPadding + bottomPadding)
        font: root.controlFont
        color: enabled ? colors.text : colors.disabledText
        selectionColor: colors.accent
        selectedTextColor: colors.surface
        leftPadding: 8; rightPadding: 8; topPadding: 6; bottomPadding: 6
        selectByMouse: true
        hoverEnabled: true
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        placeholderText: root.placeholder
        Accessible.name: root.label
        Accessible.description: root.hint
        onActiveFocusChanged: if (activeFocus) {
            editOwner = root.bridge.ownerToken
            editGate = root.gate
            editSeed = root.seedId
        }
        onTextEdited: {
            if (root.draftKind === "seed") root.bridge.draftSeed(editSeed, editGate, root.field, text, editOwner)
            else if (root.draftKind === "filter") root.bridge.draftFilter(root.field, text, editOwner)
            else root.bridge.draftNumber(root.field, text, editOwner)
        }
        function finish() {
            if (!root.suppressFinish && (!root.mayFinish || root.mayFinish()) && editOwner)
                root.bridge.commitEditors(editOwner)
        }
        onAccepted: finish()
        onEditingFinished: finish()
        Binding on text { value: root.value }
        background: Rectangle {
            radius: 4
            color: !editor.enabled ? colors.disabled : root.invalid ? colors.errorSurface : colors.surface
            border.width: editor.activeFocus ? 2 : 1
            border.color: root.invalid ? colors.error : editor.activeFocus ? colors.accent : colors.border
        }
        Controls.ToolTip.visible: hovered && root.hint.length > 0
        Controls.ToolTip.text: root.hint
        Controls.ToolTip.delay: 650
    }
    Text { Layout.fillWidth: true; Layout.minimumWidth: 0; objectName: root.errorKey + "_error"; visible: root.invalid; text: visible ? root.bridge.errorText : ""; color: colors.error; font: root.controlFont; wrapMode: Text.Wrap; textFormat: Text.PlainText; Accessible.name: root.label + " validation error" }
}

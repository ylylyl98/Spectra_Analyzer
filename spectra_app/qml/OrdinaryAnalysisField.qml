import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: fieldRoot
    required property var panel
    required property string field
    readonly property var metadata: panel.metadata(field)
    readonly property string kind: metadata.kind || "number"
    readonly property string label: field === "linewidth_min" ? panel.ui.minWidthLabel || metadata.label || field
                                   : field === "linewidth_max" ? panel.ui.maxWidthLabel || metadata.label || field
                                   : metadata.label || field
    readonly property string conversion: field === "linewidth_min" ? panel.ui.minWidthText || ""
                                         : field === "linewidth_max" ? panel.ui.maxWidthText || ""
                                         : field === "peak_move_limit" ? panel.ui.moveText || "" : ""
    readonly property string helpText: (metadata.help || "") + ((field.indexOf("linewidth_") === 0 || field === "peak_move_limit") ? " " + (panel.ui.widthHelp || "") : "")
    readonly property var editorItem: kind === "bool" ? check : kind === "choice" ? choice.editorItem : number
    readonly property bool invalid: panel.bridge.errorField === field && !!panel.bridge.errorText
    property string editOwner: ""
    property bool pendingEdit: false
    objectName: "ordinaryRow_" + field
    Layout.fillWidth: true; Layout.minimumWidth: 0
    spacing: 3
    AppColors { id: colors }
    Component.onCompleted: { panel.editors[field] = fieldRoot; project() }
    function project() {
        const value = panel.state.texts && panel.state.texts[field] !== undefined ? String(panel.state.texts[field]) : String(panel.values[field] === undefined ? "" : panel.values[field])
        if (number.text !== value) number.text = value
    }
    function finish() {
        if (!panel.retiring && pendingEdit && editOwner && editOwner === panel.bridge.ownerToken) {
            // Return emits accepted and editingFinished; validate each edit once.
            pendingEdit = false
            panel.bridge.commit(editOwner)
        }
    }
    function retire() { pendingEdit = false; editOwner = ""; choice.editorItem.popup.close(); project() }
    Connections {
        target: fieldRoot.panel.bridge
        function onStateChanged() { fieldRoot.project() }
        function onDraftChanged() { fieldRoot.project() }
    }
    Text {
        objectName: "ordinaryLabel_" + fieldRoot.field
        visible: fieldRoot.kind !== "bool"
        Layout.fillWidth: true; Layout.minimumWidth: 0
        text: fieldRoot.label; textFormat: Text.PlainText; wrapMode: Text.Wrap
        color: enabled ? colors.text : colors.disabledText; font: fieldRoot.panel.controlFont
    }
    Controls.TextField {
        id: number
        objectName: fieldRoot.kind === "number" ? "ordinaryField_" + fieldRoot.field : ""
        property string helpText: fieldRoot.helpText
        visible: fieldRoot.kind === "number"
        Layout.fillWidth: true; Layout.minimumWidth: 0
        implicitHeight: Math.max(32, contentHeight + topPadding + bottomPadding)
        font: fieldRoot.panel.controlFont
        color: enabled ? colors.text : colors.disabledText
        leftPadding: 8; rightPadding: 8; topPadding: 5; bottomPadding: 5
        selectByMouse: true; hoverEnabled: true
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        Accessible.name: fieldRoot.label; Accessible.description: helpText
        onActiveFocusChanged: if (activeFocus) fieldRoot.editOwner = fieldRoot.panel.bridge.ownerToken
        onTextEdited: {
            fieldRoot.pendingEdit = true
            fieldRoot.panel.bridge.setDraft(fieldRoot.field, text, fieldRoot.editOwner)
        }
        onAccepted: fieldRoot.finish()
        onEditingFinished: fieldRoot.finish()
        Keys.onUpPressed: {
            if (!fieldRoot.panel.retiring && fieldRoot.panel.bridge.step(fieldRoot.field, 1, fieldRoot.editOwner)) fieldRoot.pendingEdit = false
        }
        Keys.onDownPressed: {
            if (!fieldRoot.panel.retiring && fieldRoot.panel.bridge.step(fieldRoot.field, -1, fieldRoot.editOwner)) fieldRoot.pendingEdit = false
        }
        background: Rectangle {
            radius: 4; color: !number.enabled ? colors.disabled : fieldRoot.invalid ? colors.errorSurface : colors.surface
            border.width: number.activeFocus ? 2 : 1
            border.color: fieldRoot.invalid ? colors.error : number.activeFocus ? colors.accent : colors.border
        }
        Controls.ToolTip {
            id: numberTip
            popupType: Controls.Popup.Window
            visible: number.hovered && number.helpText.trim().length > 0
            text: number.helpText; delay: 650; font: fieldRoot.panel.controlFont
            width: Math.min(400, implicitWidth)
            contentItem: Text { text: numberTip.text; textFormat: Text.PlainText; wrapMode: Text.Wrap; font: fieldRoot.panel.controlFont; color: colors.text }
            background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
        }
    }
    SurfaceCheckBox {
        id: check
        objectName: fieldRoot.kind === "bool" ? "ordinaryField_" + fieldRoot.field : ""
        property string actionOwner: ""
        visible: fieldRoot.kind === "bool"
        Layout.fillWidth: true; Layout.minimumWidth: 0
        text: fieldRoot.label; font: fieldRoot.panel.controlFont
        implicitHeight: Math.max(30, contentItem.implicitHeight + 8)
        Accessible.name: fieldRoot.label; Accessible.description: fieldRoot.helpText
        onActiveFocusChanged: if (activeFocus) actionOwner = fieldRoot.panel.bridge.ownerToken
        onPressed: actionOwner = fieldRoot.panel.bridge.ownerToken
        onClicked: if (actionOwner === fieldRoot.panel.bridge.ownerToken) fieldRoot.panel.bridge.setValue(fieldRoot.field, checked, actionOwner)
        Binding on checked { value: !!fieldRoot.panel.values[fieldRoot.field] }
        contentItem: Text {
            text: check.text; font: check.font; color: check.enabled ? colors.text : colors.disabledText
            leftPadding: check.indicator.width + check.spacing; wrapMode: Text.Wrap; textFormat: Text.PlainText
        }
    }
    AnalysisChoice {
        id: choice
        visible: fieldRoot.kind === "choice"
        bridge: fieldRoot.panel.bridge; field: fieldRoot.field
        label: fieldRoot.label; labelVisible: false; controlFont: fieldRoot.panel.controlFont
        selected: String(fieldRoot.panel.values[fieldRoot.field] || "")
        choices: (fieldRoot.metadata.choices || []).map(function(value) { return {label: value, value: value} })
        Component.onCompleted: editorItem.objectName = fieldRoot.kind === "choice" ? "ordinaryField_" + fieldRoot.field : ""
        onChosen: function(value, owner) { bridge.setValue(fieldRoot.field, value, owner) }
    }
    Text {
        objectName: "ordinaryConversion_" + fieldRoot.field
        visible: !!text
        Layout.fillWidth: true; Layout.minimumWidth: 0
        text: fieldRoot.conversion; textFormat: Text.PlainText; wrapMode: Text.Wrap
        color: colors.secondary; font: fieldRoot.panel.controlFont
        Accessible.name: fieldRoot.label + " axis conversion"
    }
    Text {
        objectName: "ordinaryError_" + fieldRoot.field
        visible: fieldRoot.invalid
        Layout.fillWidth: true; Layout.minimumWidth: 0
        text: visible ? fieldRoot.panel.bridge.errorText : ""
        textFormat: Text.PlainText; wrapMode: Text.Wrap; color: colors.error; font: fieldRoot.panel.controlFont
    }
}

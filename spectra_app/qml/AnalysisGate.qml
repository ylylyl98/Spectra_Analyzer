import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "analysisGate"
    required property var bridge
    property font controlFont: Qt.application.font
    readonly property var state: bridge.state
    property string projectedOwner: ""
    property bool retiring: false
    implicitWidth: 442
    implicitHeight: content.implicitHeight + 2
    color: colors.surface
    focus: true
    Accessible.name: "Gate navigation"
    AppColors { id: colors }

    function project() {
        const changedOwner = projectedOwner !== bridge.ownerToken
        if (changedOwner) {
            retiring = true
            root.forceActiveFocus(Qt.OtherFocusReason)
            projectedOwner = bridge.ownerToken
        }
        // Avoid assigning an unchanged value: assignment moves a live cursor.
        if (gate.text !== String(state.gateText)) gate.text = String(state.gateText)
        if (index.text !== String(state.indexText)) index.text = String(state.indexText)
        if (changedOwner) Qt.callLater(function() { root.retiring = false })
    }
    Component.onCompleted: project()
    component Caption: Text {
        textFormat: Text.PlainText
        font: root.controlFont; color: colors.secondary
        height: 30; verticalAlignment: Text.AlignVCenter
    }
    component Field: Controls.TextField {
        id: field
        property string fieldKey
        property string editOwner: ""
        readonly property bool invalid: root.bridge.errorField === fieldKey && !!root.bridge.errorText
        height: Math.max(30, contentHeight + 8)
        font: root.controlFont
        color: enabled ? colors.text : colors.disabledText
        selectionColor: colors.accent; selectedTextColor: colors.surface
        leftPadding: 6; rightPadding: 6; topPadding: 4; bottomPadding: 4
        selectByMouse: true
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        enabled: root.bridge.enabled
        onActiveFocusChanged: {
            if (activeFocus) editOwner = root.bridge.ownerToken
            else finish()
        }
        onTextEdited: root.bridge.setDraft(fieldKey, text, editOwner)
        function finish() {
            if (!root.retiring && editOwner) root.bridge.commit(editOwner)
        }
        onAccepted: finish()
        Keys.priority: Keys.BeforeItem
        Keys.onPressed: function(event) {
            if (event.key === Qt.Key_Up || event.key === Qt.Key_Down) {
                root.bridge.step(event.key === Qt.Key_Up ? 1 : -1, editOwner)
                event.accepted = true
            }
        }
        background: Rectangle {
            radius: 4
            color: !field.enabled ? colors.disabled : field.invalid ? colors.errorSurface : colors.surface
            border.width: field.activeFocus ? 2 : 1
            border.color: field.invalid ? colors.error : field.activeFocus ? colors.accent : colors.border
        }
        Accessible.description: invalid ? root.bridge.errorText : "Type a value and press Enter to apply. Up and Down move through available Gates."
    }
    component Step: SurfaceButton {
        property int direction
        property string actionOwner: ""
        width: 30; height: 30
        font: root.controlFont
        leftPadding: 4; rightPadding: 4; topPadding: 4; bottomPadding: 4
        onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
        onPressed: actionOwner = root.bridge.ownerToken
        onClicked: root.bridge.step(direction, actionOwner)
    }
    ColumnLayout {
        id: content
        x: 2; y: 1; width: Math.max(1, root.width - 4)
        spacing: 3
        Flow {
            id: row
            Layout.fillWidth: true; Layout.minimumWidth: 0
            spacing: 6
            onWidthChanged: forceLayout()
            Caption { text: "Gate" }
            Field { id: gate; objectName: "gateValue"; fieldKey: "gate"; width: 104; Accessible.name: "Gate value" }
            Caption { text: "Index" }
            Field { id: index; objectName: "gateIndex"; fieldKey: "index"; width: 58; Accessible.name: "Gate index" }
            Caption { objectName: "gateCount"; text: "/ " + root.state.count }
            Step { objectName: "gatePrevious"; direction: -1; text: "‹"; enabled: root.bridge.enabled && !!root.state.canPrevious; Accessible.name: "Previous Gate" }
            Step { objectName: "gateNext"; direction: 1; text: "›"; enabled: root.bridge.enabled && !!root.state.canNext; Accessible.name: "Next Gate" }
        }
        Text {
            objectName: "gateError"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            visible: !!root.bridge.errorText
            text: root.bridge.errorText
            textFormat: Text.PlainText; wrapMode: Text.Wrap
            font: root.controlFont; color: colors.error
            Accessible.name: "Gate validation error"
        }
    }
    Timer {
        id: focusError
        property string field
        property string owner
        interval: 0
        onTriggered: if (owner === root.bridge.ownerToken) {
            const editor = field === "index" ? index : gate
            editor.forceActiveFocus(Qt.OtherFocusReason)
        }
    }
    Connections {
        target: root.bridge
        function onStateChanged() { root.project() }
        function onDraftChanged() { root.project() }
        function onFocusRequested(field, owner) {
            focusError.field = field
            focusError.owner = owner
            focusError.restart()
        }
    }
}

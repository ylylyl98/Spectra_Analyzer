import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    property string field
    property string label
    property font controlFont: Qt.font({ family: "Segoe UI", pointSize: 10 })
    property bool numeric: false
    property var revealViewport: null
    property bool revealPending: false
    property string fieldOwner: ""
    property string revealOwner: ""
    function cancelReveal() {
        revealPending = false
        revealViewport = null
    }
    function ensureEditorVisible() {
        if (!revealPending) return
        if (revealOwner !== root.bridge.inputOwner || !root.visible || !root.enabled || !editor.activeFocus || !revealViewport) {
            cancelReveal()
            return
        }
        let point = editor.mapToItem(revealViewport.contentItem, 0, 0)
        let maximum = Math.max(0, revealViewport.contentHeight-revealViewport.height)
        revealViewport.contentY = Math.max(0, Math.min(point.y-12, maximum))
    }
    onVisibleChanged: if (!visible) cancelReveal()
    onEnabledChanged: if (!enabled) cancelReveal()
    onBridgeChanged: cancelReveal()
    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
    spacing: 4
    Label { text: root.label; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width }
    TextField {
        id: editor
        objectName: "xyField_" + root.field
        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
        font: root.controlFont
        text: root.bridge.values[root.field] === undefined ? "" : root.bridge.values[root.field]
        selectByMouse: true
        Accessible.name: root.label
        inputMethodHints: root.numeric ? Qt.ImhFormattedNumbersOnly : Qt.ImhNone
        PointHandler { onActiveChanged: if (active) root.fieldOwner = root.bridge.inputOwner }
        onTextEdited: root.bridge.draftFor(root.field, text, root.fieldOwner)
        onAccepted: root.bridge.commitFor(root.field, root.fieldOwner)
        onEditingFinished: root.bridge.blurFor(root.field, root.fieldOwner)
        onActiveFocusChanged: { if (activeFocus) root.fieldOwner = root.bridge.inputOwner; else root.cancelReveal() }
        Keys.onEscapePressed: root.bridge.revertFor(root.fieldOwner)
        background: Rectangle { radius: 4; color: colors.surface; border.color: editor.activeFocus ? colors.accent : colors.border; border.width: editor.activeFocus ? 2 : 1 }
        color: enabled ? colors.text : colors.disabledText
        padding: 8
    }
    Label {
        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
        visible: root.bridge.error.length > 0 && root.bridge.errorField === root.field
        text: visible ? root.bridge.error : ""
        color: colors.error
        font: root.controlFont
        wrapMode: Text.Wrap
        Accessible.name: root.label + " validation"
    }
    Connections {
        target: root.revealViewport
        function onContentHeightChanged() {
            // Expanding a section can finish after the focus request. Follow
            // its actual layout boundary rather than guessing a frame delay.
            root.ensureEditorVisible()
            Qt.callLater(root.ensureEditorVisible)
        }
    }
    Connections {
        target: root.bridge
        function onFocusFieldRequested(field) {
            root.cancelReveal()
            if (field !== root.field || !root.enabled || !root.bridge.enabled) return
            root.revealOwner = root.bridge.inputOwner
            root.fieldOwner = root.bridge.inputOwner
            for (let container = editor.parent; container; container = container.parent) {
                if (container.contentY !== undefined && container.contentHeight !== undefined) {
                    root.revealViewport = container
                    root.revealPending = true
                    editor.forceActiveFocus()
                    Qt.callLater(root.ensureEditorVisible)
                    break
                }
            }
        }
    }
    AppColors { id: colors }
}

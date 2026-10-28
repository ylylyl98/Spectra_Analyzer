import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    required property string field
    property string label
    property string hint
    property bool suppressCommit: false
    property var mayCommit: null
    property string token: ""
    property string owner: ""
    property alias editorItem: editor
    readonly property string error: bridge ? String(bridge.errors[field] || "") : ""
    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 3
    AppColors { id: colors }
    function refresh() {
        if (owner && owner !== bridge.ownerToken) {
            retire(); editor.focus = false
        }
        if (!editor.activeFocus) editor.text = String(bridge.drafts[field] || "")
    }
    function retire() { if (token) bridge.endEdit(token); token = "" }
    function capture() {
        if (!token) { owner = bridge.ownerToken; token = bridge.beginEdit(field) }
    }
    function stepNumber(direction) {
        if (token && bridge.step(field, direction, token)) editor.text = String(bridge.drafts[field])
    }
    Text { Layout.fillWidth: true; text: root.label; font: Qt.application.font; color: root.enabled ? colors.text : colors.disabledText; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    RowLayout {
        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 0
        Controls.TextField {
            id: editor
            objectName: "xy-analysis-" + root.field
            Layout.fillWidth: true; Layout.minimumWidth: 0
            implicitHeight: Math.max(34, contentHeight + 12)
            font: Qt.application.font; color: enabled ? colors.text : colors.disabledText
            selectByMouse: true; inputMethodHints: Qt.ImhFormattedNumbersOnly
            leftPadding: 8; rightPadding: 8; topPadding: 6; bottomPadding: 6
            Accessible.name: root.label; Accessible.description: root.error || root.hint
            onActiveFocusChanged: {
                if (activeFocus) root.capture()
                else if (root.token) {
                    if (!root.suppressCommit && (!root.mayCommit || root.mayCommit())) root.bridge.commit(root.token)
                    root.retire()
                }
            }
            onTextEdited: if (root.token) root.bridge.setDraft(root.field, text, root.token)
            onAccepted: if (root.token) root.bridge.commit(root.token)
            Keys.onUpPressed: root.stepNumber(1)
            Keys.onDownPressed: root.stepNumber(-1)
            background: Rectangle { radius: 4; color: root.error ? colors.errorSurface : editor.enabled ? colors.surface : colors.disabled; border.color: root.error ? colors.error : editor.activeFocus ? colors.accent : colors.border; border.width: editor.activeFocus ? 2 : 1 }
        }
        Column {
            Layout.preferredWidth: 25
            Repeater {
                model: [1, -1]
                delegate: SurfaceButton {
                    required property int modelData
                    property string stepToken: ""
                    width: 25; height: editor.height / 2; implicitHeight: height
                    leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                    focusPolicy: Qt.NoFocus; font: Qt.application.font; text: modelData > 0 ? "▴" : "▾"
                    Accessible.name: (modelData > 0 ? "Increase " : "Decrease ") + root.label
                    onPressed: { root.capture(); stepToken = root.token }
                    onClicked: {
                        if (stepToken && root.bridge.step(root.field, modelData, stepToken)) editor.text = String(root.bridge.drafts[root.field])
                        if (!editor.activeFocus) root.retire()
                    }
                }
            }
        }
    }
    Text { Layout.fillWidth: true; visible: !!root.hint; text: root.hint; color: colors.secondary; font: Qt.application.font; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    Text { Layout.fillWidth: true; visible: !!root.error; text: root.error; color: colors.error; font: Qt.application.font; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    Component.onCompleted: refresh()
    Connections { target: root.bridge; function onChanged() { root.refresh() } }
    Component.onDestruction: if (bridge && token) bridge.endEdit(token)
}

import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    readonly property var state: bridge.state
    width: 470; height: 430
    color: colors.surface
    focus: true
    Accessible.name: "Replace analysis input"
    AppColors { id: colors }
    function choose(choice, owner) { if (bridge.chooseRebind(choice, owner)) dialogHost.accept() }
    Component.onCompleted: Qt.callLater(function() { cancel.forceActiveFocus(Qt.OtherFocusReason) })
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16; spacing: 14
        Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: "Use " + (root.state.inputMode === "raw" ? "Original" : "Processed") + " input?"; font: Qt.application.font; color: colors.text; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: "This captures a new frozen input version and resets candidates, filters, seeds and fits. Duplicate then replace keeps the current analysis as a separate graph."; font: Qt.application.font; color: colors.text; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: String(root.state.rebindCount || 0) + " Gates in the frozen input"; font: Qt.application.font; color: colors.secondary; wrapMode: Text.Wrap; textFormat: Text.PlainText }
        Text { Layout.fillWidth: true; Layout.minimumWidth: 0; visible: !!root.bridge.errorText; text: root.bridge.errorText; color: colors.error; wrapMode: Text.Wrap; font: Qt.application.font; textFormat: Text.PlainText }
        Item { Layout.fillHeight: true }
        AnalysisAction { bridge: root.bridge; objectName: "analysis_rebind_replace"; text: "Replace and reset"; enabled: root.bridge.enabled && !root.state.busy; onTriggered: function(owner) { root.choose("replace", owner) } }
        AnalysisAction { bridge: root.bridge; objectName: "analysis_rebind_duplicate"; text: "Duplicate then replace"; enabled: root.bridge.enabled && !root.state.busy; onTriggered: function(owner) { root.choose("duplicate", owner) } }
        AnalysisAction { id: cancel; bridge: root.bridge; objectName: "analysis_rebind_cancel"; text: "Cancel"; onTriggered: root.dialogHost.reject() }
    }
}

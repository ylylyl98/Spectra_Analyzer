import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    readonly property var state: bridge.state
    width: 430; height: 380
    color: colors.surface
    focus: true
    Accessible.name: "Add seed"
    AppColors { id: colors }
    function add(owner) { if (bridge.acceptAdd(owner)) dialogHost.accept() }
    Connections { target: root.bridge; function onFocusRequested(section, field) { if (field === "addPosition") { position.editorItem.forceActiveFocus(Qt.OtherFocusReason); position.editorItem.selectAll() } } }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16; spacing: 12
        Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: "Add a manual seed · " + (root.state.gateLabel || "Current Gate"); wrapMode: Text.Wrap; color: colors.text; font: Qt.application.font; textFormat: Text.PlainText }
        AnalysisField { id: position; bridge: root.bridge; field: "addPosition"; label: "Position"; value: String(root.state.addPosition || ""); enabled: root.bridge.enabled && !root.state.busy; hint: "Position must lie inside the analysis X interval." }
        AnalysisChoice { bridge: root.bridge; field: "addKind"; label: "Kind"; selected: root.state.addKind || "peak"; choices: [{label: "Peak", value: "peak"}, {label: "Dip", value: "dip"}]; enabled: root.bridge.enabled && !root.state.busy; onChosen: function(value, owner) { bridge.setChoice(field, value, owner) } }
        Text { Layout.fillWidth: true; Layout.minimumWidth: 0; visible: !!root.bridge.errorText; text: root.bridge.errorText; color: colors.error; wrapMode: Text.Wrap; font: Qt.application.font; textFormat: Text.PlainText }
        Item { Layout.fillHeight: true }
        RowLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
            AnalysisAction { bridge: root.bridge; objectName: "analysis_add_cancel"; text: "Cancel"; onTriggered: root.dialogHost.reject() }
            AnalysisAction { bridge: root.bridge; objectName: "analysis_add_accept"; text: "Add seed"; primary: true; enabled: root.bridge.enabled && !root.state.busy; onTriggered: function(owner) { root.add(owner) } }
        }
    }
}


import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    readonly property var state: bridge.state
    property var editors: ({})
    color: colors.surface
    width: 460; height: 630
    focus: true
    Accessible.name: "Candidate filter settings"
    AppColors { id: colors }
    component Note: Text { Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.Wrap; font: Qt.application.font; color: colors.secondary; textFormat: Text.PlainText }
    component Field: AnalysisField {
        bridge: root.bridge; draftKind: "filter"
        value: String((root.state.filter || ({}))[field] === undefined ? "" : root.state.filter[field])
        Component.onCompleted: root.editors[field] = editorItem
    }
    Connections {
        target: root.bridge
        function onFocusRequested(section, field) {
            const editor = root.editors[field]
            if (editor) { editor.forceActiveFocus(Qt.OtherFocusReason); editor.selectAll(); scroll.contentItem.contentY = Math.max(0, editor.mapToItem(body, 0, 0).y - 30) }
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 12
        Note { text: "Pending criteria · " + (root.state.scope === "all" ? "All Gates" : root.state.gateLabel || "Current Gate"); color: colors.text }
        Note { text: root.state.filterState || "Changes apply only when you choose Apply filter." }
        Controls.ScrollView {
            id: scroll
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                id: body
                width: scroll.availableWidth; spacing: 10
                enabled: root.bridge.enabled && !root.state.busy
                Field { field: "min_prominence_fraction"; label: "Prominence fraction" }
                Field { field: "min_noise_strength"; label: "Noise strength" }
                Field { field: "min_width"; label: "Minimum width (X)" }
                Field { field: "max_width"; label: "Maximum width (X)"; hint: "Zero allows any width." }
                Field { field: "min_distance"; label: "Minimum distance (X)" }
                Field { field: "max_count"; label: "Maximum candidate count"; hint: "Zero keeps all candidates." }
            }
        }
        Note { objectName: "analysis_filter_error"; visible: !!root.bridge.errorText; text: root.bridge.errorText; color: colors.error; Accessible.name: "Filter validation error" }
        RowLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
            AnalysisAction { bridge: root.bridge; objectName: "analysis_filter_close"; text: "Close"; onTriggered: function(owner) { if (bridge.commitEditors(owner)) root.dialogHost.reject() } }
            AnalysisAction { bridge: root.bridge; objectName: "analysis_filter_apply"; actionKey: "filter"; text: "Apply filter"; primary: true; enabled: root.bridge.enabled && !root.state.busy }
        }
    }
}

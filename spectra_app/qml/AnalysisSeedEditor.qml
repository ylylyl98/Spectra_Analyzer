import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    readonly property var state: bridge.state
    readonly property var selected: {
        const rows = state.seeds || []
        for (let i = 0; i < rows.length; ++i) if (rows[i].seedId === state.selectedSeedId) return rows[i]
        return ({})
    }
    property var editors: ({})
    property string detailIdentity: ""
    property bool retiring: false
    color: colors.surface
    width: 740; height: 710
    focus: true
    Accessible.name: "Edit accepted seeds"
    AppColors { id: colors }
    function captureIdentity() { return bridge.ownerToken + ":" + state.gate + ":" + (state.selectedSeedId || "") }
    Component.onCompleted: detailIdentity = captureIdentity()
    component Note: Text { Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.Wrap; font: Qt.application.font; color: colors.secondary; textFormat: Text.PlainText }
    component Field: AnalysisField {
        bridge: root.bridge; draftKind: "seed"; seedId: root.state.selectedSeedId || ""; gate: root.state.gate === undefined ? -1 : root.state.gate
        value: String(root.selected[field] === undefined ? "" : root.selected[field]); suppressFinish: root.retiring
        Component.onCompleted: root.editors[field] = editorItem
    }
    component Flag: SurfaceCheckBox {
        property string field
        property string capturedOwner: ""
        property string capturedSeed: ""
        property int capturedGate: -1
        objectName: "seed_" + field + "_" + (root.state.selectedSeedId || "")
        Layout.fillWidth: true; Layout.minimumWidth: 0
        font: Qt.application.font
        checked: !!root.selected[field]
        Accessible.name: text
        function capture() { capturedOwner = root.bridge.ownerToken; capturedGate = root.state.gate; capturedSeed = root.state.selectedSeedId || "" }
        onPressed: capture()
        onActiveFocusChanged: if (activeFocus) capture()
        onClicked: { root.bridge.draftSeed(capturedSeed, capturedGate, field, String(checked), capturedOwner); root.bridge.commitEditors(capturedOwner) }
    }
    Connections {
        target: root.bridge
        function onStateChanged() {
            const identity = root.captureIdentity()
            if (identity !== root.detailIdentity) {
                root.retiring = true
                kindChoice.editorItem.popup.close()
                root.forceActiveFocus(Qt.OtherFocusReason)
                root.detailIdentity = identity
                Qt.callLater(function() { root.retiring = false })
            }
        }
        function onFocusRequested(section, field) {
            const split = field.lastIndexOf(":")
            const key = split >= 0 ? field.slice(split + 1) : field
            const editor = root.editors[key]
            if (editor) { editor.forceActiveFocus(Qt.OtherFocusReason); if (editor.selectAll) editor.selectAll(); scroll.contentItem.contentY = Math.max(0, editor.mapToItem(body, 0, 0).y - 30) }
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 10
        Note { text: root.state.gateLabel ? "Gate " + root.state.gateLabel : "Current Gate"; color: colors.text }
        Note { text: "Select a seed to edit. Uncheck Use to exclude it. Blank fit parameters are estimated; edits commit to this analysis." }
        Controls.ScrollView {
            Layout.fillWidth: true; Layout.minimumWidth: 0
            Layout.preferredHeight: Math.min(150, seedList.implicitHeight)
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                id: seedList
                width: parent.width; spacing: 3
                Repeater {
                    model: root.state.seeds || []
                    delegate: AnalysisAction {
                        required property var modelData
                        property int capturedGate: -1
                        bridge: root.bridge
                        objectName: "seed_" + modelData.seedId
                        text: (modelData.enabled ? "Use · " : "Excluded · ") + modelData.position + " · " + modelData.kind
                        checked: root.state.selectedSeedId === modelData.seedId
                        onPressed: capturedGate = root.state.gate
                        onActiveFocusChanged: if (activeFocus) { capturedGate = root.state.gate; actionOwner = root.bridge.ownerToken }
                        onTriggered: function(owner) { root.bridge.selectSeed(modelData.seedId, capturedGate, owner) }
                    }
                }
            }
        }
        Note { visible: !root.state.selectedSeedId; text: "Select a seed above, or add a seed from the Properties pane." }
        Controls.ScrollView {
            id: scroll
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                id: body
                width: scroll.availableWidth; spacing: 10
                enabled: !!root.state.selectedSeedId && root.bridge.enabled && !root.state.busy
                Flag { field: "enabled"; text: "Use this seed" }
                GridLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    columns: root.width >= 600 ? 2 : 1; columnSpacing: 12; rowSpacing: 10
                    Field { field: "position"; label: "Position"; Layout.preferredWidth: 1 }
                    AnalysisChoice {
                        id: kindChoice
                        bridge: root.bridge; field: (root.state.selectedSeedId || "") + ":kind"; label: "Kind"; Layout.preferredWidth: 1
                        property string capturedSeed: ""
                        property int capturedGate: -1
                        selected: root.selected.kind || "peak"
                        choices: [{label: "Peak", value: "peak"}, {label: "Dip", value: "dip"}]
                        onInteractionStarted: { capturedSeed = root.state.selectedSeedId || ""; capturedGate = root.state.gate === undefined ? -1 : root.state.gate }
                        onChosen: function(value, owner) { bridge.draftSeed(capturedSeed, capturedGate, "kind", value, owner); bridge.commitEditors(owner) }
                        Component.onCompleted: root.editors["kind"] = editorItem
                    }
                    Field { field: "amplitude"; label: "Amplitude"; placeholder: "Estimate"; Layout.preferredWidth: 1 }
                    Field { field: "width"; label: root.state.widthLabel || "Width / sigma"; placeholder: "Estimate"; Layout.preferredWidth: 1 }
                    Field { field: "gamma"; label: "Gamma (Voigt)"; placeholder: "Estimate"; enabled: !!root.selected.gammaEnabled; hint: "Retained when another fit model is selected."; Layout.preferredWidth: 1 }
                    Field { field: "bound"; label: "Symmetric center bound (±)"; placeholder: "Unbounded"; Layout.preferredWidth: 1 }
                }
                Flag { field: "fixedCenter"; text: "Fixed center" }
            }
        }
        Note { objectName: "analysis_seed_error"; visible: !!root.bridge.errorText; text: root.bridge.errorText; color: colors.error; Accessible.name: "Seed validation error" }
        AnalysisAction { bridge: root.bridge; objectName: "analysis_seed_close"; text: "Close"; onTriggered: function(owner) { if (bridge.commitEditors(owner)) root.dialogHost.reject() } }
    }
}


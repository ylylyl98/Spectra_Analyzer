import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "analysisWorkbench"
    required property var bridge
    property font controlFont: Qt.application.font
    readonly property var state: bridge.state
    property string projectedOwner: ""
    property bool retiring: false
    property bool suppressFieldFinish: false
    property var editors: ({})
    property var disclosureHeaders: []
    implicitWidth: 320
    implicitHeight: content.implicitHeight + 20
    color: colors.surface
    focus: true
    signal revealY(real y)
    Accessible.name: "Analysis Graph properties"
    AppColors { id: colors }
    Component.onCompleted: projectedOwner = bridge.ownerToken
    function choices(values) { return values.map(function(value) { return {label: value, value: value} }) }
    function expanded(key) { return !state.disclosures || state.disclosures[key] === undefined || state.disclosures[key] }
    function disclosureHasFocus() {
        for (let i = 0; i < disclosureHeaders.length; ++i) if (disclosureHeaders[i].activeFocus) return true
        return false
    }
    function reveal(section, field, focusEditor) {
        bridge.setDisclosure(section, true, bridge.ownerToken)
        pending.field = field; pending.owner = bridge.ownerToken; pending.takeFocus = focusEditor; pending.restart()
    }
    Timer {
        id: pending
        property string field
        property string owner
        property bool takeFocus
        interval: 20
        onTriggered: {
            if (owner !== root.bridge.ownerToken) return
            const editor = root.editors[field]
            if (!editor || !editor.visible) return
            if (takeFocus) { editor.forceActiveFocus(Qt.OtherFocusReason); if (editor.selectAll) editor.selectAll() }
            root.revealY(editor.mapToItem(root, 0, editor.height / 2).y)
        }
    }
    Connections {
        target: root.bridge
        function onFocusRequested(section, field) { root.reveal(section, field, true) }
        function onRevealRequested(section, field) { root.reveal(section, field, false) }
        function onStateChanged() {
            if (root.projectedOwner !== root.bridge.ownerToken) {
                root.retiring = true
                root.forceActiveFocus(Qt.OtherFocusReason)
                root.projectedOwner = root.bridge.ownerToken
                Qt.callLater(function() { root.retiring = false })
            }
        }
    }
    component Note: Text {
        Layout.fillWidth: true; Layout.minimumWidth: 0
        wrapMode: Text.Wrap; textFormat: Text.PlainText
        font: root.controlFont; color: colors.secondary
    }
    component Action: AnalysisAction {
        bridge: root.bridge; font: root.controlFont
        objectName: "analysis_" + actionKey
    }
    component Choice: AnalysisChoice {
        bridge: root.bridge; controlFont: root.controlFont
        selected: String(root.state[field] === undefined ? "" : root.state[field])
        onChosen: function(value, owner) { bridge.setChoice(field, value, owner) }
        Component.onCompleted: root.editors[field] = editorItem
    }
    component Field: AnalysisField {
        bridge: root.bridge; controlFont: root.controlFont
        value: String(root.state[field] === undefined ? "" : root.state[field])
        suppressFinish: root.retiring || root.suppressFieldFinish || root.disclosureHasFocus()
        mayFinish: function() { return !root.retiring && !root.suppressFieldFinish && !root.disclosureHasFocus() }
        Component.onCompleted: root.editors[field] = editorItem
    }
    component Section: ColumnLayout {
        id: section
        property string sectionKey
        property string title
        default property alias contents: body.data
        Layout.fillWidth: true; Layout.minimumWidth: 0
        spacing: 4
        AnalysisAction {
            id: header
            bridge: root.bridge
            objectName: "analysis_" + section.sectionKey + "_toggle"
            text: section.title
            font: root.controlFont
            Accessible.description: root.expanded(section.sectionKey) ? "Expanded. Press to collapse." : "Collapsed. Press to expand."
            Component.onCompleted: root.disclosureHeaders.push(header)
            onPressed: root.suppressFieldFinish = true
            onReleased: Qt.callLater(function() { root.suppressFieldFinish = false })
            onTriggered: function(owner) {
                root.suppressFieldFinish = true
                forceActiveFocus(Qt.MouseFocusReason)
                bridge.setDisclosure(section.sectionKey, !root.expanded(section.sectionKey), owner)
                Qt.callLater(function() { root.suppressFieldFinish = false })
            }
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) {
                    bridge.setDisclosure(section.sectionKey, event.key === Qt.Key_Right, actionOwner)
                    event.accepted = true
                }
            }
            contentItem: RowLayout {
                Text { text: root.expanded(section.sectionKey) ? "▾" : "▸"; font: root.controlFont; color: colors.secondary }
                Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: section.title; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap }
            }
        }
        ColumnLayout { id: body; visible: root.expanded(section.sectionKey); Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.leftMargin: 3; Layout.rightMargin: 3; spacing: 6 }
    }
    component Overlay: SurfaceCheckBox {
        property string overlayLayer
        property string actionOwner: ""
        Layout.fillWidth: true; Layout.minimumWidth: 0
        font: root.controlFont
        onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
        onPressed: actionOwner = root.bridge.ownerToken
        onClicked: root.bridge.setOverlay(overlayLayer, checked, actionOwner)
        Binding on checked { value: !!root.state[overlayLayer + "Visible"] }
        Accessible.name: text
    }
    ColumnLayout {
        id: content
        x: 10; y: 10; width: Math.max(1, root.width - 20)
        spacing: 5
        enabled: root.bridge.enabled
        Note { objectName: "analysis_error"; visible: !!root.bridge.errorText; text: visible ? root.bridge.errorText : ""; color: colors.error; Accessible.name: "Analysis validation error" }
        Section {
            sectionKey: "detection"; title: "Input and detection"
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; enabled: !root.state.busy
                spacing: 4
                RowLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                    Choice { field: "inputMode"; label: "Proposed input"; labelVisible: false; Layout.preferredWidth: 1; choices: [{label: "Original", value: "raw"}, {label: "Processed", value: "processed"}] }
                    Action { actionKey: "rebind"; text: root.state.bindText || "Use input"; Layout.preferredWidth: 1; enabled: !!root.state.canBind; Accessible.description: root.state.bindTooltip || "Capture a new frozen input version." }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                    Choice { field: "scope"; label: "Target scope"; labelVisible: false; Layout.preferredWidth: 1; choices: [{label: "Current Gate", value: "current"}, {label: "All Gates", value: "all"}] }
                    Choice { field: "mode"; label: "Candidate kind"; labelVisible: false; Layout.preferredWidth: 1; choices: root.choices(["Both", "Peaks", "Dips"]) }
                    Choice { field: "detector"; label: "Detection"; labelVisible: false; Layout.preferredWidth: 1; choices: [{label: "Direct", value: "Original"}, {label: "D2-assisted", value: "D2"}] }
                }
                GridLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; columns: 3; columnSpacing: 8
                    Field { field: "xmin"; label: "X minimum"; Layout.preferredWidth: 1 }
                    Field { field: "xmax"; label: "X maximum"; Layout.preferredWidth: 1 }
                    Field { field: "d2Window"; label: "D2 window"; Layout.preferredWidth: 1; enabled: root.state.detector === "D2"; hint: "Requested D2 points; the effective odd window depends on each finite segment." }
                }
                Note { objectName: "analysis_d2Note"; visible: !!root.state.d2Note; text: root.state.d2Note || ""; Accessible.name: "Effective D2 window" }
                Action { actionKey: "find"; text: "Find candidates"; primary: true }
            }
        }
        Section {
            sectionKey: "filter"; title: "Candidate filter"
            GridLayout {
                id: filterControls
                Layout.fillWidth: true; Layout.minimumWidth: 0
                columns: width < 342 ? 2 : 3
                columnSpacing: 6; rowSpacing: 6
                Choice { field: "filterPreset"; label: "Pending criteria"; labelVisible: false; Layout.preferredWidth: 1; Layout.columnSpan: filterControls.columns === 2 ? 2 : 1; choices: root.choices(["Loose", "Balanced", "Strict", "Custom"]); enabled: !root.state.busy }
                Action { actionKey: "filter"; text: "Apply filter"; Layout.preferredWidth: 1; enabled: !root.state.busy && !!root.state.filterCanApply }
                Action { actionKey: "filterSettings"; text: "Settings…"; Layout.preferredWidth: 1; enabled: !root.state.busy }
            }
            Note { objectName: "analysis_filterState"; text: root.state.filterState || "No filter applied"; Accessible.name: "Applied and pending candidate filter" }
        }
        Section {
            sectionKey: "fit"; title: "Fit seeds"
            RowLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                Choice { field: "model"; label: "Fit model"; labelVisible: false; Layout.preferredWidth: 1; choices: root.choices(["Lorentzian", "Gaussian", "Voigt"]); enabled: !root.state.busy }
                Action { actionKey: "fit"; text: "Fit seeds"; Layout.preferredWidth: 1; primary: true; enabled: !root.state.busy }
                Action { actionKey: "stop"; text: "Stop"; Layout.preferredWidth: 1; visible: !!root.state.busy }
            }
            Controls.ProgressBar { objectName: "analysis_progress"; Layout.fillWidth: true; visible: !!root.state.busy; indeterminate: true; Accessible.name: "Analysis running" }
            Note { objectName: "analysis_status"; text: root.state.status || "Ready"; Accessible.name: "Analysis task status" }
            Note { objectName: "analysis_counts"; text: root.state.counts || "" }
        }
        Section {
            sectionKey: "seeds"; title: "Seeds · current Gate"
            Note { text: root.state.gateLabel || "" }
            Note { visible: !(root.state.seeds || []).length; text: "No seeds at this Gate. Find candidates or add a seed." }
            Controls.ScrollView {
                Layout.fillWidth: true; Layout.minimumWidth: 0
                Layout.preferredHeight: Math.min(164, seedList.implicitHeight)
                visible: (root.state.seeds || []).length > 0
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    id: seedList
                    width: parent.width
                    spacing: 2
                    Repeater {
                        model: root.state.seeds || []
                        delegate: RowLayout {
                            id: seedRow
                            required property var modelData
                            property int rowGate: root.state.gate === undefined ? -1 : root.state.gate
                            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 5
                            SurfaceCheckBox {
                                property string capturedOwner: ""
                                property int capturedGate: -1
                                objectName: "seed_use_" + seedRow.modelData.seedId
                                enabled: !root.state.busy
                                checked: !!seedRow.modelData.enabled
                                Accessible.name: "Use seed at " + seedRow.modelData.position
                                onPressed: { capturedOwner = root.bridge.ownerToken; capturedGate = seedRow.rowGate }
                                onActiveFocusChanged: if (activeFocus) { capturedOwner = root.bridge.ownerToken; capturedGate = seedRow.rowGate }
                                onClicked: {
                                    root.bridge.draftSeed(seedRow.modelData.seedId, capturedGate, "enabled", String(checked), capturedOwner)
                                    root.bridge.commitEditors(capturedOwner)
                                }
                            }
                            AnalysisAction {
                                bridge: root.bridge
                                property int capturedGate: -1
                                objectName: "seed_" + seedRow.modelData.seedId
                                text: String(seedRow.modelData.position) + " · " + seedRow.modelData.kind
                                checked: root.state.selectedSeedId === seedRow.modelData.seedId
                                font: root.controlFont
                                onPressed: capturedGate = seedRow.rowGate
                                onActiveFocusChanged: if (activeFocus) { capturedGate = seedRow.rowGate; actionOwner = root.bridge.ownerToken }
                                onTriggered: function(owner) { root.bridge.selectSeed(seedRow.modelData.seedId, capturedGate, owner) }
                            }
                        }
                    }
                }
            }
            Action { actionKey: "seeds"; text: "Edit seeds…"; enabled: !root.state.busy }
            RowLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                Action { actionKey: "add"; text: "Add seed"; enabled: !root.state.busy }
                Action { actionKey: "remove"; text: "Remove seed"; enabled: !root.state.busy && !!root.state.selectedSeedId }
            }
        }
        Section {
            sectionKey: "overlays"; title: "Display and publish"
            RowLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0
                Overlay { objectName: "analysis_detected"; overlayLayer: "detected"; text: "Detected"; enabled: !root.state.busy }
                Overlay { objectName: "analysis_fitted"; overlayLayer: "fitted"; text: "Fitted"; enabled: !root.state.busy }
            }
            Action { actionKey: "style"; text: "Style…"; enabled: !root.state.busy }
            Action { actionKey: "linecut"; text: "Create current-Gate linecut graph"; enabled: !root.state.busy && !!root.state.canLinecut }
        }
    }
}

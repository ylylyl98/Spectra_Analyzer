import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "ordinaryAnalysisRoot"
    required property var bridge
    property font controlFont: Qt.application.font
    readonly property var state: bridge.state
    readonly property var values: state.values || ({})
    readonly property var ui: state.ui || ({})
    property var editors: ({})
    property var collapsed: ({})
    property string revealedField: ""
    property string projectedOwner: ""
    property bool retiring: false
    signal revealY(real y)
    implicitWidth: 320
    implicitHeight: content.implicitHeight + 20
    color: colors.surface
    focus: true
    Accessible.name: "Spectrum analysis"
    AppColors { id: colors }
    Component.onCompleted: projectedOwner = bridge.ownerToken
    function metadata(field) {
        const entries = bridge.fields
        for (let i = 0; i < entries.length; ++i) if (entries[i].name === field) return entries[i]
        return ({})
    }
    function fieldTab(field) { return Number(metadata(field).tab || 0) }
    function groupFor(field) {
        if (field.indexOf("fit_x") === 0) return "range"
        if (["linewidth_min", "linewidth_max", "peak_move_limit", "maxfev"].indexOf(field) >= 0) return "constraints"
        if (field === "csv_source") return "seeds"
        if (field.indexOf("fit_") === 0) return "model"
        return "detection"
    }
    function shown(field, condition) { return !!condition || revealedField === field }
    function expand(key) { const next = Object.assign({}, collapsed); next[key] = false; collapsed = next }
    function reveal(field, owner) {
        if (owner !== bridge.ownerToken) return
        revealedField = field
        expand(groupFor(field))
        bridge.selectTab(fieldTab(field))
        pending.field = field; pending.owner = owner; pending.restart()
    }
    Timer {
        id: pending
        property string field: ""
        property string owner: ""
        interval: 25
        onTriggered: {
            if (owner !== root.bridge.ownerToken) return
            const row = root.editors[field]
            if (!row || !row.visible) return
            row.editorItem.forceActiveFocus(Qt.OtherFocusReason)
            root.revealY(row.editorItem.mapToItem(root, 0, row.editorItem.height / 2).y)
        }
    }
    Connections {
        target: root.bridge
        function onFocusRequested(field, owner) { root.reveal(field, owner) }
        function onStateChanged() {
            if (root.projectedOwner !== root.bridge.ownerToken) {
                root.retiring = true
                root.forceActiveFocus(Qt.OtherFocusReason)
                root.projectedOwner = root.bridge.ownerToken
                root.revealedField = ""
                pending.stop()
                for (const field in root.editors) root.editors[field].retire()
                Qt.callLater(function() { root.retiring = false })
            }
        }
    }
    component Note: Text {
        Layout.fillWidth: true; Layout.minimumWidth: 0
        visible: text.length > 0
        font: root.controlFont; color: colors.secondary
        textFormat: Text.PlainText; wrapMode: Text.Wrap
    }
    component Field: OrdinaryAnalysisField { panel: root }
    component Action: OrdinaryAnalysisAction {
        bridge: root.bridge; font: root.controlFont
        enabled: root.bridge.enabled && !!root.ui[command + "Enabled"]
    }
    component Tab: Controls.TabButton {
        font: root.controlFont
        implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
        Accessible.name: text
        contentItem: Text {
            text: parent.text; textFormat: Text.PlainText; font: root.controlFont
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            color: parent.enabled ? parent.checked ? colors.accent : colors.text : colors.disabledText
        }
        background: Rectangle {
            color: parent.checked ? colors.selected : parent.hovered ? colors.hover : colors.surface
            border.color: parent.visualFocus ? colors.accent : colors.border
            radius: 4
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 2; color: colors.accent; visible: parent.parent.checked }
        }
    }
    component Section: ColumnLayout {
        id: section
        property string sectionKey
        property string title
        default property alias contents: body.data
        objectName: "ordinaryGroup_" + sectionKey
        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
        SurfaceButton {
            objectName: "ordinaryGroupToggle_" + section.sectionKey
            Layout.fillWidth: true; Layout.minimumWidth: 0
            text: (root.collapsed[section.sectionKey] ? "▸  " : "▾  ") + section.title
            font: root.controlFont
            Accessible.name: section.title
            Accessible.description: root.collapsed[section.sectionKey] ? "Collapsed" : "Expanded"
            onClicked: { const next = Object.assign({}, root.collapsed); next[section.sectionKey] = !next[section.sectionKey]; root.collapsed = next }
        }
        ColumnLayout { id: body; Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8; visible: !root.collapsed[section.sectionKey] }
    }
    ColumnLayout {
        id: content
        x: 10; y: 10; width: Math.max(1, root.width - 20); spacing: 10
        enabled: root.bridge.enabled
        Controls.TabBar {
            id: tabs
            objectName: "ordinaryTabs"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            currentIndex: root.bridge.selectedTab
            Tab { objectName: "ordinaryTab_find"; text: "Find Peaks"; onClicked: root.bridge.selectTab(0) }
            Tab { objectName: "ordinaryTab_fit"; text: "Peak Fit"; onClicked: root.bridge.selectTab(1) }
        }
        Note { objectName: "ordinaryAxis"; visible: !!text; text: root.ui.axisText || "" }
        Note { objectName: "ordinaryError"; visible: !!root.bridge.errorText; text: root.bridge.errorText; color: colors.error; Accessible.name: "Analysis validation error" }
        ColumnLayout {
            visible: root.bridge.selectedTab === 0
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 10
            Note { objectName: "ordinaryDetection"; text: root.ui.detectionText || "" }
            Action { command: "find"; text: root.ui.findText || "Find Peaks"; primary: true }
            Action { command: "batchFind"; text: root.ui.batchFindText || "Find All Gates" }
            Section {
                sectionKey: "detection"; title: "Detection settings"
                Field { field: "derivative_detection" }
                Field { field: "peak_mode"; visible: root.shown(field, !root.values.derivative_detection) }
                GridLayout {
                    visible: !root.values.derivative_detection || ["prominence", "distance", "max_peaks", "max_dips"].indexOf(root.revealedField) >= 0
                    Layout.fillWidth: true; Layout.minimumWidth: 0; columns: 2; columnSpacing: 8; rowSpacing: 8
                    Field { field: "prominence"; visible: root.shown(field, !root.values.derivative_detection) }
                    Field { field: "distance"; visible: root.shown(field, !root.values.derivative_detection) }
                    Field { field: "max_peaks"; visible: root.shown(field, !root.values.derivative_detection) }
                    Field { field: "max_dips"; visible: root.shown(field, !root.values.derivative_detection && root.values.peak_mode === "Both") }
                }
                Field { field: "peak_smooth"; visible: root.shown(field, !root.values.derivative_detection) }
                Field { field: "peak_smooth_window"; visible: root.shown(field, !root.values.derivative_detection && root.values.peak_smooth) }
                Field { field: "d2_mode"; visible: root.shown(field, root.values.derivative_detection) }
                GridLayout {
                    visible: !!root.values.derivative_detection || root.revealedField.indexOf("d2_") === 0
                    Layout.fillWidth: true; Layout.minimumWidth: 0; columns: 2; columnSpacing: 8; rowSpacing: 8
                    Field { field: "d2_window"; visible: root.shown(field, root.values.derivative_detection) }
                    Field { field: "d2_polyorder"; visible: root.shown(field, root.values.derivative_detection) }
                    Field { field: "d2_prominence"; visible: root.shown(field, root.values.derivative_detection) }
                    Field { field: "d2_distance"; visible: root.shown(field, root.values.derivative_detection) }
                    Field { field: "d2_separation"; visible: root.shown(field, root.values.derivative_detection) }
                    Field { field: "d2_target"; visible: root.shown(field, root.values.derivative_detection) }
                    Field { field: "d2_max_peaks"; visible: root.shown(field, root.values.derivative_detection) }
                    Field { field: "d2_max_dips"; visible: root.shown(field, root.values.derivative_detection && root.values.d2_mode === "Both") }
                }
            }
        }
        ColumnLayout {
            visible: root.bridge.selectedTab === 1
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 10
            Note { objectName: "ordinaryReadiness"; text: root.ui.readinessText || ""; Accessible.name: "Fit readiness" }
            Action { command: "fit"; text: root.ui.fitText || "Fit Current Gate"; primary: true }
            Action { command: "batchFit"; text: root.ui.batchFitText || "Fit All Gates" }
            Section {
                sectionKey: "range"; title: "Fit range"
                Note { objectName: "ordinaryFitRange"; text: root.ui.rangeText || "" }
                GridLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; columns: 2; columnSpacing: 8
                    Field { field: "fit_xmin" }
                    Field { field: "fit_xmax" }
                    Field { field: "fit_xmin_fixed" }
                    Field { field: "fit_xmax_fixed" }
                }
                Action { command: "currentRange"; text: "Use Current X Range" }
                Action { command: "fullRange"; text: "Full Range" }
            }
            Section {
                sectionKey: "model"; title: "Model and smoothing"
                Field { field: "fit_model" }
                Field { field: "fit_smooth" }
                Field { field: "fit_smooth_window"; visible: root.shown(field, root.values.fit_smooth) }
            }
            Section {
                sectionKey: "constraints"; title: "Fit constraints"
                Field { field: "linewidth_min" }
                Field { field: "linewidth_max" }
                Field { field: "peak_move_limit" }
                Note { objectName: "ordinaryWidthHelp"; text: root.ui.widthHelp || "" }
                Field { field: "maxfev" }
            }
            Section {
                sectionKey: "seeds"; title: "CSV seeds"
                Field { field: "csv_source" }
                ColumnLayout {
                    visible: !!root.values.csv_source
                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
                    Note { objectName: "ordinaryCsvPath"; text: root.state.csvPath || "No CSV loaded"; wrapMode: Text.WrapAnywhere }
                    Action { command: "loadCsv"; text: "Load CSV Seeds…" }
                }
            }
            Section {
                sectionKey: "results"; title: "Fit results and export"
                Action { command: "clearFit"; text: "Clear Fit" }
                Action { command: "exportCurrentFit"; text: "Export Current Fit…" }
                Action { command: "exportAllFit"; text: "Export All Fits…" }
            }
        }
        Section {
            sectionKey: "editing"; title: "Edit peaks"
            RowLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
                Action { command: "lasso"; text: "Lasso"; checkable: true; checked: !!root.state.lassoActive }
                Action { command: "circle"; text: "Circle"; checkable: true; checked: !!root.state.circleActive }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
                Action { command: "deleteLasso"; text: "Delete Selected" }
                Action { command: "clearLasso"; text: "Clear Selection" }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
                Text { text: "Peak number"; textFormat: Text.PlainText; font: root.controlFont; color: colors.text }
                Controls.SpinBox {
                    id: peakNumber
                    objectName: "ordinaryPeakNumber"
                    property string actionOwner: ""
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    from: 1; to: Math.max(1, root.state.peakCount || 0); value: root.state.peakNumber || 1
                    enabled: root.state.peakCount > 0
                    font: root.controlFont; Accessible.name: "Peak number to highlight or delete"
                    onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
                    onValueModified: if (actionOwner === root.bridge.ownerToken) root.bridge.setPeakNumber(value, actionOwner)
                    TapHandler { onPressedChanged: if (pressed) peakNumber.actionOwner = root.bridge.ownerToken }
                }
            }
            Action { command: "deletePeak"; text: "Delete Peak" }
            Action { command: "clearPeaks"; text: "Clear Current Peaks" }
            Action { command: "clearBatch"; text: "Clear Batch Peaks" }
            Action { command: "savePeaks"; text: "Save Peaks…" }
        }
        Action { command: "exportSpectrum"; text: "Export Spectrum…" }
        Action { command: "createAnalysis"; text: "Create Analysis Graph…" }
        Section {
            sectionKey: "summary"; title: "Analysis summary"
            Controls.ScrollView {
                id: summaryViewport
                objectName: "ordinarySummaryViewport"
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.minimumHeight: 0
                Layout.preferredHeight: 105; Layout.maximumHeight: 105
                clip: true; contentWidth: availableWidth
                Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                Controls.TextArea {
                    objectName: "ordinarySummary"
                    readOnly: true; selectByMouse: true; selectByKeyboard: true; wrapMode: TextEdit.Wrap
                    textFormat: TextEdit.PlainText; text: root.state.summary || ""
                    font: root.controlFont; color: colors.text; padding: 4
                    selectionColor: colors.accent; selectedTextColor: colors.surface
                    Accessible.name: "Analysis summary"
                    background: null
                }
            }
        }
    }
}

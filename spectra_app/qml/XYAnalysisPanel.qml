import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "xy-analysis-panel"
    required property var bridge
    readonly property var state: bridge.state
    property bool retiring: false
    property var editors: ({})
    implicitWidth: 300; implicitHeight: content.implicitHeight + 20
    color: colors.surface; focus: true
    signal revealY(real y)
    AppColors { id: colors }
    Accessible.name: "XY analysis"
    function retireEditors() {
        retiring = true
        for (const key in editors) editors[key].retire()
        root.forceActiveFocus()
        Qt.callLater(function() { retiring = false })
    }
    onVisibleChanged: if (!visible) retireEditors()
    component Note: Text { Layout.fillWidth: true; Layout.minimumWidth: 0; font: Qt.application.font; color: colors.secondary; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    component Choice: XYAnalysisChoice {
        bridge: root.bridge; enabled: !!root.state.editable
        Component.onCompleted: root.editors[field] = this
    }
    component Action: XYAnalysisAction { bridge: root.bridge }
    ColumnLayout {
        id: content
        x: 10; y: 10; width: Math.max(1, root.width - 20); spacing: 9
        Note { text: "XY analysis"; color: colors.text; font.bold: true }
        Note { objectName: "xy-analysis-context"; text: root.state.context || "Select an XY curve to analyze." }
        Choice { field: "mode"; label: "Find"; options: ["Peaks", "Dips", "Both"] }
        Choice { field: "detector"; label: "Detector"; options: ["Original", "D2"] }
        Action { commandName: "find"; text: "Find candidates"; primary: true; enabled: !!root.state.findEnabled }
        Choice { field: "preset"; label: "Candidate filter"; options: ["Loose", "Balanced", "Strict", "Custom"] }
        Action { commandName: "settings"; text: "Analysis settings…"; enabled: !!root.state.editable }
        Choice { field: "model"; label: "Fit model"; options: ["Gaussian", "Lorentzian", "Voigt"] }
        Action { commandName: "seeds"; text: "Seeds and fit results…"; enabled: !!root.state.seedsEnabled }
        Action { commandName: "fit"; text: "Fit seeds"; enabled: !!root.state.fitEnabled; primary: true }
        Action { commandName: "interval"; text: "Select X interval…"; onStarting: root.retireEditors() }
        Action { commandName: "resume"; text: root.state.resumeText || "Resume analysis"; visible: !!root.state.resumeVisible; enabled: !!root.state.resumeEnabled }
        Action { commandName: "start"; text: root.state.startText || "Start analysis"; visible: !!root.state.startVisible; enabled: !!root.state.startEnabled }
        Note { objectName: "xy-analysis-status"; text: root.state.status || ""; visible: !!text }
        Note { objectName: "xy-analysis-error"; text: root.state.error || ""; visible: !!text; color: colors.error }
        Action { commandName: "save"; text: "Save results to Workbook"; enabled: !!root.state.saveEnabled }
        Action { commandName: "graph"; text: "Create analysis graph"; enabled: !!root.state.graphEnabled }
    }
}

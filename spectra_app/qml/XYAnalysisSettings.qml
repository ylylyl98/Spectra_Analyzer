import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "xy-analysis-settings"
    required property var bridge
    required property var dialogHost
    property var editors: ({})
    property bool retiring: false
    width: 580; height: 700; color: colors.surface; focus: true
    Accessible.name: "XY analysis settings"
    AppColors { id: colors }
    function retireEditors() {
        retiring = true
        for (const key in editors) editors[key].retire()
        forceActiveFocus()
        Qt.callLater(function() { retiring = false })
    }
    function closeDrafts() { retireEditors(); dialogHost.reject() }
    onVisibleChanged: if (!visible) retireEditors()
    Connections {
        target: root.Window.window
        function onVisibleChanged() { if (!root.Window.window.visible) root.retireEditors() }
    }
    Connections {
        target: root.bridge
        function onRevealRequested(field) {
            const component = root.editors[field]
            if (!component) return
            Qt.callLater(function() {
                const editor = component.editorItem
                scroll.contentItem.contentY = Math.max(0, Math.min(body.height - scroll.availableHeight, editor.mapToItem(body, 0, 0).y - 35))
                editor.forceActiveFocus(Qt.OtherFocusReason)
                if (editor.selectAll) editor.selectAll()
            })
        }
    }
    component Note: Text { Layout.fillWidth: true; Layout.minimumWidth: 0; font: Qt.application.font; color: colors.secondary; wrapMode: Text.Wrap; textFormat: Text.PlainText }
    component Heading: Note { color: colors.text; font.bold: true; Layout.topMargin: 8 }
    component Field: XYAnalysisField {
        bridge: root.bridge; suppressCommit: root.retiring
        mayCommit: function() { return !root.retiring && root.visible && !!root.Window.window && root.Window.window.visible && root.Window.window.active }
        Component.onCompleted: root.editors[field] = this
    }
    component Choice: XYAnalysisChoice { bridge: root.bridge; Component.onCompleted: root.editors[field] = this }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 10
        Note { text: "Settings update the current analysis when a valid edit is committed. Return commits; unfinished text is kept when this window closes." }
        Controls.ScrollView {
            id: scroll
            objectName: "xy-analysis-settings-scroll"
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                id: body
                width: scroll.availableWidth; spacing: 10
                enabled: !!root.bridge.state.editable
                Heading { text: "Detection" }
                Choice { field: "mode"; label: "Find"; options: ["Peaks", "Dips", "Both"] }
                Choice { field: "detector"; label: "Detector"; options: ["Original", "D2"] }
                Field { field: "sgWindow"; label: "D2 smoothing window (points)"; hint: "Odd integer, 3–10001 points."; enabled: root.bridge.values.detector === "D2" }
                Field { field: "sgOrder"; label: "D2 polynomial order"; hint: "Integer, 2–10; smaller than the window."; enabled: root.bridge.values.detector === "D2" }
                Heading { text: "Candidate filter" }
                Choice { field: "preset"; label: "Preset"; options: ["Loose", "Balanced", "Strict", "Custom"] }
                Field { field: "prominence"; label: "Relative prominence"; hint: "Fraction of signal range, 0–1." }
                Field { field: "noise"; label: "Noise multiplier"; hint: "Non-negative multiple of estimated noise." }
                Field { field: "distance"; label: "Minimum candidate spacing (X units)"; hint: "0 allows any spacing." }
                Field { field: "candidateWidthMin"; label: "Minimum candidate width (X units)"; hint: "0 allows any width." }
                Field { field: "candidateWidthMax"; label: "Maximum candidate width (X units)"; hint: "0 = Unlimited." }
                Field { field: "maxCount"; label: "Maximum candidates"; hint: "Integer, 0–10000; 0 = Unlimited." }
                Heading { text: "Fit" }
                Note { objectName: "xy-analysis-width-label"; text: root.bridge.state.widthLabel || "Width in X units" }
                Choice { field: "model"; label: "Fit model"; options: ["Gaussian", "Lorentzian", "Voigt"] }
                Field { field: "widthMin"; label: "Minimum fitted width (X units)"; hint: "At least 0.000000001." }
                Field { field: "widthMax"; label: "Maximum fitted width (X units)"; hint: "0 = Auto; otherwise greater than the minimum." }
            }
        }
        SurfaceButton { objectName: "xy-analysis-settings-close"; Layout.alignment: Qt.AlignRight; text: "Close"; font: Qt.application.font; focusPolicy: Qt.NoFocus; onPressed: root.retireEditors(); onClicked: root.closeDrafts() }
    }
    Keys.onEscapePressed: root.closeDrafts()
}

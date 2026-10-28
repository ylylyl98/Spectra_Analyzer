import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property QtObject application
    objectName: "applicationProperties"
    color: colors.surface
    AppColors { id: colors }
    readonly property bool xyContext: application.contextKind === "xy"
    readonly property bool datContext: application.contextKind === "dat"
    property string pendingInspectorField: ""
    onXyContextChanged: if (!xyContext) {
        xyCurves.visited = false
        xyAnalysis.visited = false
        xyDisplay.visited = false
    }
    onDatContextChanged: if (!datContext) {
        datData.visited = false
        datAnalysis.visited = false
        datViewLoader.visited = false
    }
    function retainLoadedEditor(loader) {
        const editor = loader.item
        // Loading can occur while active is still being evaluated.
        Qt.callLater(function() {
            if (loader.active && loader.item === editor) loader.visited = true
        })
    }
    function revealInspectorField() {
        const field = pendingInspectorField
        if (!field.length) return
        if (field === "interval") {
            if (!xyDisplay.item) return
            xyDisplay.item.showInterval = true
        } else root.application.revealInspectorField(field)
        pendingInspectorField = ""
    }
    function reveal(y) {
        const view = root.xyContext && root.application.taskIndex === 1 && xyAnalysis.item
                   ? xyAnalysis.item.contentItem : scroll.contentItem
        view.contentY = Math.max(0, Math.min(y - 16, view.contentHeight - view.height))
    }
    Connections {
        target: root.application
        function onRevealProperties(kind, y) { Qt.callLater(function() { root.reveal(y) }) }
        function onInspectorRevealRequested(section, field) {
            root.pendingInspectorField = field
            Qt.callLater(root.revealInspectorField)
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 0
        TaskNavigation { bridge: root.application.applicationBridge.navigation; Layout.fillWidth: true }
        Text {
            objectName: "propertiesTarget"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            Layout.margins: 10
            text: root.application.contextSummary.split("\n")[0]
            textFormat: Text.PlainText
            font: Qt.font({family: Qt.application.font.family, pointSize: Qt.application.font.pointSize, bold: true})
            color: colors.text; wrapMode: Text.Wrap
            Accessible.name: "Properties target: " + text
        }
        Item {
            visible: root.xyContext
            Layout.fillWidth: true; Layout.fillHeight: visible
            // Visit lazily, then retain the editor and its local undo history.
            Loader {
                id: xyCurves
                property bool visited: false
                anchors.fill: parent
                active: root.xyContext && (visited || root.application.taskIndex === 0)
                visible: root.xyContext && root.application.taskIndex === 0
                sourceComponent: curves
                onLoaded: { root.retainLoadedEditor(xyCurves); Qt.callLater(root.revealInspectorField) }
            }
            Loader {
                id: xyAnalysis
                property bool visited: false
                anchors.fill: parent
                active: root.xyContext && (visited || root.application.taskIndex === 1)
                visible: root.xyContext && root.application.taskIndex === 1
                sourceComponent: scrollableXYAnalysis
                onLoaded: root.retainLoadedEditor(xyAnalysis)
            }
            Loader {
                id: xyDisplay
                property bool visited: false
                anchors.fill: parent
                active: root.xyContext && (visited || root.application.taskIndex === 2)
                visible: root.xyContext && root.application.taskIndex === 2
                sourceComponent: display
                onLoaded: { root.retainLoadedEditor(xyDisplay); Qt.callLater(root.revealInspectorField) }
            }
        }
        Controls.ScrollView {
            id: scroll
            objectName: "propertiesScroll"
            visible: !root.xyContext
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
            clip: true
            ColumnLayout {
                width: scroll.availableWidth; spacing: 8
                Text {
                    objectName: "workbenchPropertiesHint"
                    visible: root.application.workbenchActive && root.application.taskIndex !== 1
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: "Data and appearance edits belong to the source graph. Return to Workspace to edit them."
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                    font: Qt.application.font; color: colors.secondary; padding: 12
                }
                SurfaceButton {
                    objectName: "propertiesBackToWorkspace"
                    readonly property var command: root.application.applicationBridge.commands.actions.find(function(action) { return action.key === "back" })
                    visible: root.application.workbenchActive && root.application.taskIndex !== 1
                    Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10
                    text: "Back to Workspace"
                    enabled: command ? command.enabled : false
                    onClicked: if (command) command.trigger()
                }
                Loader {
                    id: datData
                    property bool visited: false
                    Layout.fillWidth: true
                    active: (root.datContext || root.application.contextKind === "empty") && (visited || root.application.taskIndex === 0)
                    visible: active && root.application.taskIndex === 0 && !root.application.workbenchActive
                    sourceComponent: dataPanel
                    onLoaded: root.retainLoadedEditor(datData)
                }
                Loader {
                    id: datAnalysis
                    property bool visited: false
                    Layout.fillWidth: true
                    active: root.datContext && (visited || root.application.taskIndex === 1)
                    visible: active && root.application.taskIndex === 1
                    sourceComponent: root.application.workbenchActive ? workbench : ordinary
                    onLoaded: root.retainLoadedEditor(datAnalysis)
                }
                Loader {
                    id: datViewLoader
                    property bool visited: false
                    Layout.fillWidth: true
                    active: root.datContext && (visited || root.application.taskIndex === 2)
                    visible: active && root.application.taskIndex === 2 && !root.application.workbenchActive
                    sourceComponent: datView
                    onLoaded: root.retainLoadedEditor(datViewLoader)
                }
                Text {
                    objectName: "propertiesContextHint"
                    visible: root.application.contextKind === "workbook" || root.application.contextKind === "empty"
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    text: root.application.contextKind === "workbook"
                        ? root.application.taskIndex === 0
                            ? root.application.contextSummary + "\n\nEdit cells and X/Y columns in the Workbook. Use Plot to create an XY graph."
                            : root.application.taskIndex === 1
                                ? "Select a graph or an Original / Processed DAT sheet, then choose Analyze."
                                : "Workbook cells are edited in the document. Open or create a graph to change its appearance."
                        : "Open a Workbook or graph from Project, or use Import data to begin."
                    wrapMode: Text.Wrap; textFormat: Text.PlainText
                    font: Qt.application.font; color: colors.secondary; padding: 12
                    HoverHandler { id: contextHover }
                    Controls.ToolTip {
                        id: contextTooltip
                        objectName: "propertiesContextTooltip"
                        popupType: Controls.Popup.Window
                        visible: contextHover.hovered && root.application.contextKind === "workbook"
                                 && root.application.taskIndex === 0
                        text: root.application.contextFullSummary
                        delay: 650; font: Qt.application.font
                        width: Math.min(640, implicitWidth)
                        contentItem: Text {
                            text: contextTooltip.text
                            textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere
                            font: Qt.application.font; color: colors.text
                        }
                        background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                    }
                }
            }
        }
    }
    Component {
        id: dataPanel
        ColumnLayout {
            SourceNavigation { bridge: root.application.sourceNavigation; Layout.fillWidth: true }
            ProcessingPanel { visible: root.datContext; processing: root.application.applicationBridge.processing; enabled: !root.application.workbenchActive; Layout.fillWidth: true }
        }
    }
    Component { id: ordinary; OrdinaryAnalysis { bridge: root.application.ordinaryAnalysis; onRevealY: function(y) { root.reveal(y) } } }
    Component { id: workbench; AnalysisWorkbench { bridge: root.application.analysisWorkbench; onRevealY: function(y) { root.reveal(y) } } }
    Component {
        id: datView
        ColumnLayout {
            Layout.fillWidth: true
            Text { objectName: "datAppearanceHeading"; text: "Data appearance"; font: Qt.font({family: Qt.application.font.family, pointSize: Qt.application.font.pointSize, bold: true}); color: colors.text; Layout.fillWidth: true; Layout.leftMargin: 10; Layout.topMargin: 8 }
            SharedDatProperties { bridge: root.application.sharedDat; enabled: !root.application.workbenchActive; Layout.fillWidth: true; onRevealY: function(y) { root.reveal(y + this.y) } }
            Text { objectName: "publicationFormatHeading"; text: "Publication format"; font: Qt.font({family: Qt.application.font.family, pointSize: Qt.application.font.pointSize, bold: true}); color: colors.text; Layout.fillWidth: true; Layout.leftMargin: 10; Layout.topMargin: 8 }
            FormalProperties { bridge: root.application.formalBridge; enabled: !root.application.workbenchActive; Layout.fillWidth: true; onRevealY: function(y) { root.reveal(y + this.y) } }
        }
    }
    Component {
        id: scrollableXYAnalysis
        Controls.ScrollView {
            id: inspectorAnalysisScroll
            objectName: "xyInspectorAnalysisScroll"
            contentWidth: availableWidth
            clip: true
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
            XYAnalysisPanel {
                width: inspectorAnalysisScroll.availableWidth
                bridge: root.application.xyAnalysisBridge
                onRevealY: function(y) { root.reveal(y) }
            }
        }
    }
    Component { id: curves; XYInspectorCurves { bridge: root.application.xyInspectorBridge } }
    Component { id: display; XYInspectorDisplay { bridge: root.application.xyInspectorBridge } }
}

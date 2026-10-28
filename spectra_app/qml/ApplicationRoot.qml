import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQml.Models

Controls.ApplicationWindow {
    id: root
    objectName: "applicationWindow"
    required property QtObject application
    required property QtObject host
    width: 1440; height: 900
    minimumWidth: 900; minimumHeight: 620
    title: application.title
    color: colors.surface
    AppColors { id: colors }
    menuBar: Controls.MenuBar {
        Instantiator {
            model: root.application.menus
            delegate: Controls.Menu {
                id: actionMenu
                required property var modelData
                title: modelData.title
                onAboutToShow: root.application.refreshMenus()
                Instantiator {
                    model: modelData.actions
                    delegate: Controls.MenuItem {
                        required property var modelData
                        text: modelData.text; enabled: modelData.enabled
                        checkable: modelData.checkable; checked: modelData.checked
                        onTriggered: modelData.trigger()
                    }
                    onObjectAdded: function(index, object) { actionMenu.insertItem(index, object) }
                    onObjectRemoved: function(index, object) { actionMenu.removeItem(object) }
                }
            }
            onObjectAdded: function(index, object) { root.menuBar.insertMenu(index, object) }
            onObjectRemoved: function(index, object) { root.menuBar.removeMenu(object) }
        }
        Controls.Menu {
            id: recentMenu
            objectName: "recentProjectsMenu"
            title: "Recent projects"
            Instantiator {
                model: root.application.recentProjects
                delegate: Controls.MenuItem { required property var modelData; text: modelData.title; onTriggered: root.application.openRecent(modelData.path) }
                onObjectAdded: function(index, object) { recentMenu.insertItem(index, object) }
                onObjectRemoved: function(index, object) { recentMenu.removeItem(object) }
            }
            Controls.MenuSeparator {}
            Controls.MenuItem { objectName: "clearRecentProjects"; text: "Clear recent projects"; onTriggered: root.application.clearRecent() }
        }
    }
    ColumnLayout {
        anchors.fill: parent; spacing: 0
        MainCommands { bridge: root.application.applicationBridge; Layout.fillWidth: true }
        RowLayout {
            Layout.fillWidth: true
            SurfaceButton { text: "Workspace"; checked: root.application.outerView === "workspace"; checkable: true; onClicked: root.application.request_outer_view("workspace") }
            SurfaceButton { text: "Analysis"; checked: root.application.outerView === "analysis"; checkable: true; onClicked: root.application.request_outer_view("analysis") }
            Item { Layout.fillWidth: true }
        }
        // Each context keeps its natural command height; long command sets retain
        // the existing scroll viewport instead of taking space from the plot.
        Item {
            objectName: "contextCommandBand"
            Layout.fillWidth: true
            Layout.preferredHeight: contextCommands.item
                ? contextCommands.item.objectName === "formalCommands"
                  && root.application.formalBridge.state.batch && root.application.formalBridge.state.batch.visible
                    ? contextCommands.item.implicitHeight : Math.min(82, contextCommands.item.implicitHeight)
                : 0
            clip: true
            Controls.ScrollView {
                id: commandScroll
                anchors.fill: parent; contentWidth: availableWidth
                contentHeight: contextCommands.implicitHeight
                Loader {
                    id: contextCommands
                    width: commandScroll.availableWidth
                    sourceComponent: root.application.workbenchActive ? analysisCommands : root.application.outerView === "analysis" ? navigationCommands : root.application.contextKind === "xy" ? xyCommands : root.application.contextKind === "workbook" ? workbookCommands : formalCommands
                }
            }
        }
        Controls.SplitView {
            id: shellSplit
            Layout.fillWidth: true; Layout.fillHeight: true
            onResizingChanged: if (!resizing) root.host.rememberPanelWidths(projectPanel.width, dockedProperties.width)
            ProjectNavigator {
                id: projectPanel
                project: root.application.applicationBridge.project
                visible: root.application.projectPanelVisible
                Controls.SplitView.preferredWidth: root.host.projectPanelWidth
                Controls.SplitView.minimumWidth: 170
                // Constrain the rendered width, leaving the preferred width intact
                // so a wider window restores the user's divider position.
                Controls.SplitView.maximumWidth: Math.max(170, shellSplit.width - 430 - (dockedProperties.visible ? 292 : 6))
            }
            Item {
                Controls.SplitView.fillWidth: true
                Controls.SplitView.minimumWidth: 430
                WorkspaceDocuments { anchors.fill: parent; documents: root.application.documents; visible: root.application.outerView === "workspace" }
                ColumnLayout {
                    anchors.fill: parent; spacing: 0; visible: root.application.outerView === "analysis"
                    AnalysisHeader { bridge: root.application.analysisHeader; Layout.fillWidth: true }
                    AnalysisGate { bridge: root.application.analysisHeader; Layout.fillWidth: true }
                    Controls.SplitView {
                        id: analysisSplit
                        orientation: Qt.Vertical
                        Layout.fillWidth: true; Layout.fillHeight: true
                        onResizingChanged: if (!resizing) root.application.rememberLinecutSizes(mainAnalysis.height, linecut.height)
                        Item {
                            id: mainAnalysis
                            Controls.SplitView.fillHeight: true
                            // Keep a readable page beside the 80px linecut and Basic's 6px handle.
                            Controls.SplitView.minimumHeight: root.application.workbenchActive
                                && analysisSplit.height >= 300 + (linecut.visible ? 86 : 0) ? 300 : 100
                            AnalysisCanvas { anchors.fill: parent; objectName: "applicationDatCanvas"; bridge: root.host.datBridge; attachmentGeneration: root.host.datGeneration; visible: !root.application.workbenchActive }
                            FormalPreview { anchors.fill: parent; objectName: "applicationWorkbenchPreview"; bridge: root.host.previewBridge; attachmentGeneration: root.host.previewGeneration; visible: root.application.workbenchActive }
                        }
                        AnalysisLinecut {
                            id: linecut
                            objectName: "applicationLinecut"
                            preview: root.host.linecutBridge; attachmentGeneration: root.host.linecutGeneration
                            visible: root.application.workbenchActive && root.application.linecutVisible
                            Controls.SplitView.preferredHeight: root.application.linecutHeight
                            Controls.SplitView.minimumHeight: 80
                        }
                    }
                }
            }
            ColumnLayout {
                id: dockedProperties
                objectName: "dockedProperties"
                visible: root.application.propertiesVisible && !root.application.propertiesFloating
                Controls.SplitView.preferredWidth: root.host.propertiesPanelWidth
                Controls.SplitView.minimumWidth: 280
                Controls.SplitView.maximumWidth: Math.max(280, shellSplit.width - 430 - (projectPanel.visible ? projectPanel.width + 12 : 6))
                spacing: 0
                SurfaceButton { objectName: "floatPropertiesButton"; text: "Float Properties"; focusPolicy: Qt.NoFocus; Layout.alignment: Qt.AlignRight; onClicked: root.host.moveProperties(true) }
                Item { id: dockedPropertiesContent; Layout.fillWidth: true; Layout.fillHeight: true }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 32; color: colors.surface
            RowLayout {
                anchors.fill: parent; anchors.margins: 4
                Text { text: root.application.statusText; elide: Text.ElideRight; Layout.fillWidth: true; color: colors.secondary }
                SurfaceButton { text: "Cancel"; visible: root.application.cancelVisible; onClicked: root.application.cancelOperation() }
            }
        }
    }
    DropArea {
        anchors.fill: parent
        onEntered: function(drag) { drag.accepted = drag.hasUrls && root.application.canAcceptDropUrls(drag.urls) }
        onPositionChanged: function(drag) { drag.accepted = drag.hasUrls && root.application.canAcceptDropUrls(drag.urls) }
        onDropped: function(drop) { drop.accepted = drop.hasUrls && root.application.dropUrls(drop.urls) }
    }
    Window {
        id: floatingProperties
        objectName: "floatingPropertiesWindow"
        title: "Properties — Spectra Analyzer"
        x: root.host.floatingPropertiesGeometry.x; y: root.host.floatingPropertiesGeometry.y
        width: root.host.floatingPropertiesGeometry.width; height: root.host.floatingPropertiesGeometry.height
        minimumWidth: 280; minimumHeight: 240
        transientParent: root
        visible: root.visible && root.application.propertiesVisible && root.application.propertiesFloating
        onClosing: function(close) { close.accepted = false; root.host.hideProperties() }
        ColumnLayout {
            anchors.fill: parent; spacing: 0
            SurfaceButton { objectName: "dockPropertiesButton"; text: "Dock Properties"; focusPolicy: Qt.NoFocus; onClicked: root.host.moveProperties(false) }
            Item { id: floatingPropertiesContent; Layout.fillWidth: true; Layout.fillHeight: true }
        }
    }
    ApplicationProperties {
        application: root.application
        parent: root.application.propertiesFloating ? floatingPropertiesContent : dockedPropertiesContent
        anchors.fill: parent
    }
    Component { id: formalCommands; FormalCommands { bridge: root.application.formalBridge } }
    Component { id: navigationCommands; AnalysisNavigation { bridge: root.application.analysisNavigation } }
    Component { id: analysisCommands; AnalysisCommands { bridge: root.application.analysisWorkbench } }
    Component { id: workbookCommands; WorkbookPlotCommands { bridge: root.application.workbookCommands } }
    Component {
        id: xyCommands
        ColumnLayout {
            implicitHeight: 82
            spacing: 0
            XYCommands { bridge: root.application.xyCommandBridge; Layout.fillWidth: true }
            XYNavigation { bridge: root.application.xyCommandBridge; Layout.fillWidth: true }
        }
    }
}

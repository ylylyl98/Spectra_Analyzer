import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    objectName: "sourceNavigation"
    required property var bridge
    property font controlFont: Qt.application.font
    property bool advancedExpanded: false
    property bool inventoryExpanded: false
    readonly property real datasetRowHeight: Math.max(28, Math.ceil(rowMetrics.height) + 10)
    implicitWidth: 300
    spacing: 6
    AppColors { id: colors }
    TextMetrics { id: rowMetrics; font: root.controlFont; text: "Ag 汉字" }
    TextMetrics { id: spinMetrics; font: root.controlFont; text: "12" }

    function openDatasetMenu(datasetId, item, x, y) {
        datasetMenu.target = bridge.datasetContext(datasetId);
        datasetMenu.popup(item, x, y);
    }
    function openGraphMenu() {
        graphMenu.target = bridge.graphContext();
        graphMenu.popup(graphActions, 0, graphActions.height);
    }

    Text {
        objectName: "sourceContext"
        Layout.fillWidth: true
        text: root.bridge.sourceContext
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: colors.secondary
        font: root.controlFont
    }
    SurfaceButton {
        objectName: "sourceAdvancedDisclosure"
        Layout.fillWidth: true
        text: (root.advancedExpanded ? "▾ " : "▸ ") + "Advanced source navigation"
        font: root.controlFont
        onClicked: root.advancedExpanded = !root.advancedExpanded
        Accessible.description: root.advancedExpanded ? "Expanded" : "Collapsed"
    }
    ColumnLayout {
        visible: root.advancedExpanded
        Layout.fillWidth: true
        spacing: 6
        SurfaceButton {
            objectName: "sourceImport"
            Layout.fillWidth: true
            text: "Import data…"
            font: root.controlFont
            enabled: root.bridge.importEnabled
            onClicked: root.bridge.importData()
            Accessible.name: "Import data files"
        }
        RowLayout {
            Layout.fillWidth: true
            Text {
                objectName: "sourceCount"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: root.bridge.countText
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: colors.text
                font: root.controlFont
            }
            Text { text: "Rows:"; color: colors.text; font: root.controlFont }
            Controls.SpinBox {
                objectName: "sourceVisibleRows"
                from: 4; to: 12
                editable: true
                value: root.bridge.visibleRows
                implicitWidth: Math.max(100, Math.ceil(spinMetrics.width) + 88)
                Layout.minimumWidth: implicitWidth
                font: root.controlFont
                onValueModified: root.bridge.setVisibleRows(value)
                Accessible.name: "Imported dataset visible rows"
            }
        }
        Controls.TextField {
            objectName: "sourceSearch"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: root.bridge.searchText
            enabled: root.bridge.navigationEnabled
            placeholderText: "Filename or folder path…"
            selectByMouse: true
            font: root.controlFont
            onTextEdited: root.bridge.setSearchText(text)
            Accessible.name: "Search imported datasets"
        }
        ListView {
            id: datasets
            objectName: "sourceDatasets"
            Layout.fillWidth: true
            Layout.preferredHeight: root.bridge.visibleRows * root.datasetRowHeight
            clip: true
            model: root.bridge.datasetRows
            enabled: root.bridge.navigationEnabled
            boundsBehavior: Flickable.StopAtBounds
            Controls.ScrollBar.vertical: Controls.ScrollBar {}
            keyNavigationEnabled: false
            currentIndex: {
                for (let i = 0; i < root.bridge.datasetRows.length; ++i)
                    if (root.bridge.datasetRows[i].datasetId === root.bridge.activeDatasetId) return i;
                return -1;
            }
            onCurrentIndexChanged: {
                if (currentIndex >= 0)
                    positionViewAtIndex(currentIndex, ListView.Contain);
            }
            onHeightChanged: Qt.callLater(function() {
                datasets.forceLayout();
                if (datasets.currentIndex >= 0)
                    datasets.positionViewAtIndex(datasets.currentIndex, ListView.Contain);
            })
            Keys.onDownPressed: root.bridge.moveDataset(1)
            Keys.onUpPressed: root.bridge.moveDataset(-1)
            Keys.onMenuPressed: {
                if (root.bridge.activeDatasetId.length)
                    root.openDatasetMenu(root.bridge.activeDatasetId, datasets, 16, 24);
            }
            delegate: Rectangle {
                required property var modelData
                required property int index
                objectName: "sourceDataset_" + index
                width: datasets.width
                height: root.datasetRowHeight
                color: modelData.datasetId === root.bridge.activeDatasetId ? colors.selected : colors.surface
                Text {
                    anchors.fill: parent
                    anchors.leftMargin: 6
                    anchors.rightMargin: 6
                    text: modelData.label
                    textFormat: Text.PlainText
                    elide: Text.ElideMiddle
                    verticalAlignment: Text.AlignVCenter
                    color: colors.text
                    font: root.controlFont
                }
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    onClicked: function(mouse) {
                        datasets.forceActiveFocus();
                        if (mouse.button === Qt.RightButton)
                            root.openDatasetMenu(modelData.datasetId, parent, mouse.x, mouse.y);
                        else
                            root.bridge.selectDataset(modelData.datasetId);
                    }
                }
                Accessible.name: modelData.label
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text { text: "Graph:"; color: colors.text; font: root.controlFont }
            Controls.ComboBox {
                objectName: "sourceGraphs"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                model: root.bridge.graphRows
                textRole: "name"
                valueRole: "graphId"
                currentIndex: {
                    for (let i = 0; i < root.bridge.graphRows.length; ++i)
                        if (root.bridge.graphRows[i].graphId === root.bridge.activeGraphId) return i;
                    return -1;
                }
                enabled: root.bridge.graphsEnabled && count > 0
                font: root.controlFont
                onActivated: root.bridge.selectGraph(currentValue)
                Accessible.name: "Named graph for the active DAT"
            }
            SurfaceButton {
                id: graphActions
                objectName: "sourceGraphActions"
                text: "Graphs"
                font: root.controlFont
                enabled: root.bridge.graphsEnabled
                onClicked: root.openGraphMenu()
            }
        }
        Text {
            objectName: "sourceNoMatches"
            visible: root.bridge.noMatches
            Layout.fillWidth: true
            text: "No matches. Clear search to show all datasets."
            wrapMode: Text.Wrap
            color: colors.secondary
            font: root.controlFont
        }
        Text {
            objectName: "sourceImportStatus"
            Layout.fillWidth: true
            text: root.bridge.importStatus
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: colors.secondary
            font: root.controlFont
            Accessible.name: "DAT import status"
        }
        SurfaceButton {
            objectName: "sourceInventoryDisclosure"
            Layout.fillWidth: true
            text: (root.inventoryExpanded ? "▾ " : "▸ ") + "Folder inventory"
            font: root.controlFont
            onClicked: root.inventoryExpanded = !root.inventoryExpanded
            Accessible.description: root.inventoryExpanded ? "Expanded" : "Collapsed"
        }
        ColumnLayout {
            visible: root.inventoryExpanded
            Layout.fillWidth: true
            RowLayout {
                Layout.fillWidth: true
                Controls.TextField {
                    objectName: "sourceRoot"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: root.bridge.rootPath
                    placeholderText: "Root folder path…"
                    readOnly: true
                    font: root.controlFont
                }
                SurfaceButton {
                    objectName: "sourceBrowse"
                    text: "Browse…"
                    font: root.controlFont
                    enabled: root.bridge.navigationEnabled
                    onClicked: root.bridge.browseRoot()
                }
            }
            Controls.ComboBox {
                objectName: "sourceFolders"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                model: root.bridge.folderRows
                textRole: "label"
                valueRole: "path"
                currentIndex: {
                    for (let i = 0; i < root.bridge.folderRows.length; ++i)
                        if (root.bridge.folderRows[i].path === root.bridge.selectedFolder) return i;
                    return -1;
                }
                font: root.controlFont
                enabled: root.bridge.navigationEnabled
                onActivated: root.bridge.selectFolder(currentValue)
                Accessible.name: "Inventory folder"
            }
            ListView {
                id: files
                objectName: "sourceFiles"
                Layout.fillWidth: true
                Layout.preferredHeight: 80
                clip: true
                model: root.bridge.fileRows
                enabled: root.bridge.navigationEnabled
                boundsBehavior: Flickable.StopAtBounds
                Controls.ScrollBar.vertical: Controls.ScrollBar {}
                keyNavigationEnabled: false
                currentIndex: {
                    for (let i = 0; i < root.bridge.fileRows.length; ++i)
                        if (root.bridge.fileRows[i].path === root.bridge.selectedFile) return i;
                    return -1;
                }
                onCurrentIndexChanged: {
                    if (currentIndex >= 0)
                        positionViewAtIndex(currentIndex, ListView.Contain);
                }
                onContentHeightChanged: Qt.callLater(function() {
                    if (files.currentIndex >= 0)
                        files.positionViewAtIndex(files.currentIndex, ListView.Contain);
                })
                Keys.onDownPressed: root.bridge.moveFile(1)
                Keys.onUpPressed: root.bridge.moveFile(-1)
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    objectName: "sourceFile_" + index
                    width: files.width; height: root.datasetRowHeight
                    color: modelData.path === root.bridge.selectedFile ? colors.selected : colors.surface
                    Text {
                        anchors.fill: parent
                        anchors.leftMargin: 6
                        text: modelData.name
                        textFormat: Text.PlainText
                        elide: Text.ElideMiddle
                        color: colors.text
                        font: root.controlFont
                        verticalAlignment: Text.AlignVCenter
                    }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: { files.forceActiveFocus(); root.bridge.selectFile(modelData.path); }
                        onDoubleClicked: { root.bridge.selectFile(modelData.path); root.bridge.loadSelected(); }
                    }
                }
            }
            SurfaceButton {
                objectName: "sourceLoad"
                Layout.fillWidth: true
                text: "Load Selected File"
                font: root.controlFont
                enabled: root.bridge.loadEnabled
                onClicked: root.bridge.loadSelected()
            }
        }
    }
    Controls.Menu {
        id: datasetMenu
        objectName: "sourceDatasetMenu"
        property var target: ({})
        Controls.MenuItem { objectName: "sourceCopy"; text: "Copy Source Path"; enabled: root.bridge.navigationEnabled && root.bridge.datasetActionEnabled(datasetMenu.target, "copy"); onTriggered: root.bridge.invokeDatasetAction(datasetMenu.target, "copy") }
        Controls.MenuItem { objectName: "sourceOpenFolder"; text: "Open Source Folder"; enabled: root.bridge.navigationEnabled && root.bridge.datasetActionEnabled(datasetMenu.target, "source"); onTriggered: root.bridge.invokeDatasetAction(datasetMenu.target, "source") }
        Controls.MenuItem { objectName: "sourceOpenExport"; text: "Open Export Folder"; enabled: root.bridge.navigationEnabled && root.bridge.datasetActionEnabled(datasetMenu.target, "export"); onTriggered: root.bridge.invokeDatasetAction(datasetMenu.target, "export") }
        Controls.MenuSeparator {}
        Controls.MenuItem {
            objectName: "sourceRemove"
            text: "Remove from Project"
            enabled: root.bridge.navigationEnabled && root.bridge.datasetActionEnabled(datasetMenu.target, "remove")
            onTriggered: root.bridge.invokeDatasetAction(datasetMenu.target, "remove")
        }
    }
    Controls.Menu {
        id: graphMenu
        objectName: "sourceGraphMenu"
        property var target: ({})
        Controls.MenuItem { objectName: "sourceNewGraph"; text: "New Graph…"; enabled: root.bridge.graphsEnabled && root.bridge.graphActionEnabled(graphMenu.target, "new"); onTriggered: root.bridge.invokeGraphAction(graphMenu.target, "new") }
        Controls.MenuItem { objectName: "sourceRenameGraph"; text: "Rename Graph…"; enabled: root.bridge.graphsEnabled && root.bridge.graphActionEnabled(graphMenu.target, "rename"); onTriggered: root.bridge.invokeGraphAction(graphMenu.target, "rename") }
        Controls.MenuItem { objectName: "sourceRemoveGraph"; text: "Remove Graph…"; enabled: root.bridge.graphsEnabled && root.bridge.graphActionEnabled(graphMenu.target, "remove"); onTriggered: root.bridge.invokeGraphAction(graphMenu.target, "remove") }
    }
}

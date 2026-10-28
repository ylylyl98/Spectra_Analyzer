import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    color: "#ffffff"
    width: 660
    height: 600
    focus: true
    Keys.onReturnPressed: if (bridge.canAccept) dialogHost.accept()
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Controls.ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 16
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 10
                Controls.Label { text: "Choose graphs from imported DATs"; font.bold: true; color: "#263447" }
                Repeater {
                    model: bridge.targetGroups
                    ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 0
                        Controls.CheckBox {
                            objectName: "batchDataset_" + modelData.children[0].datasetId
                            Layout.fillWidth: true
                            text: modelData.label
                            tristate: true
                            checkState: modelData.checkState
                            nextCheckState: function() { return checkState === Qt.Checked ? Qt.Unchecked : Qt.Checked }
                            onClicked: modelData.setChecked(checkState === Qt.Checked)
                            font.bold: true
                            Accessible.name: modelData.label
                            Controls.ToolTip.visible: hovered
                            Controls.ToolTip.text: modelData.tooltip
                            contentItem: Text {
                                text: parent.text
                                font: parent.font
                                leftPadding: parent.indicator.width + parent.spacing
                                color: "#263447"
                                wrapMode: Text.Wrap
                                verticalAlignment: Text.AlignVCenter
                            }
                        }
                        Repeater {
                            model: modelData.children
                            Controls.CheckBox {
                                required property var modelData
                                objectName: "batchGraph_" + modelData.datasetId + "_" + modelData.graphId
                                Layout.fillWidth: true
                                Layout.leftMargin: 22
                                text: modelData.label
                                checked: modelData.checked
                                onToggled: modelData.setChecked(checked)
                                Accessible.name: modelData.label
                                contentItem: Text {
                                    text: parent.text
                                    font: parent.font
                                    leftPadding: parent.indicator.width + parent.spacing
                                    color: "#263447"
                                    wrapMode: Text.Wrap
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                    }
                }
                RowLayout {
                    SurfaceButton { font: Qt.application.font; objectName: "batchSelectAll"; text: "Select all"; onClicked: bridge.selectAll(true) }
                    SurfaceButton { font: Qt.application.font; objectName: "batchClear"; text: "Clear"; onClicked: bridge.selectAll(false) }
                    Item { Layout.fillWidth: true }
                }
                Controls.Label { text: "Output format"; font.bold: true; Layout.topMargin: 6; color: "#263447" }
                Controls.ComboBox {
                    objectName: "batchFormats"
                    Layout.fillWidth: true
                    model: ["SVG + PNG", "SVG", "PNG"]
                    currentIndex: bridge.formatIndex
                    onActivated: bridge.setFormatIndex(currentIndex)
                    Accessible.name: "Output format"
                }
                Controls.CheckBox {
                    objectName: "batchCommonFolder"
                    Layout.fillWidth: true
                    text: bridge.destinationLabel
                    checked: bridge.commonFolder
                    onToggled: bridge.setCommonFolder(checked)
                    contentItem: Text {
                        text: parent.text
                        font: parent.font
                        leftPadding: parent.indicator.width + parent.spacing
                        wrapMode: Text.Wrap
                        color: "#263447"
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Controls.TextField {
                        objectName: "batchFolder"
                        Layout.fillWidth: true
                        enabled: bridge.commonFolder
                        text: bridge.destinationFolder
                        onTextEdited: bridge.setDestinationFolder(text)
                        placeholderText: "Existing writable output folder"
                        Accessible.name: "Output folder"
                        selectByMouse: true
                    }
                    SurfaceButton { font: Qt.application.font;
                        objectName: "batchBrowse"
                        text: "Browse…"
                        enabled: bridge.commonFolder
                        onClicked: bridge.browse()
                    }
                }
                Controls.Label {
                    objectName: "batchFolderError"
                    Layout.fillWidth: true
                    text: bridge.folderError
                    visible: text.length > 0
                    color: "#af2f35"
                    wrapMode: Text.Wrap
                    Accessible.name: text
                }
            }
        }
        PlotDialogFooter {
            bridge: root.bridge
            dialogHost: root.dialogHost
            actionText: "Export selected"
            Layout.fillWidth: true
        }
    }
}


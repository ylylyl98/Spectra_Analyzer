import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    required property var dialogHost
    readonly property bool wide: width >= 680
    color: "#ffffff"
    width: 940
    height: 680
    focus: true
    Keys.onReturnPressed: if (bridge.canAccept) dialogHost.accept()
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        Controls.Label {
            Layout.fillWidth: true
            Layout.margins: 14
            text: "Source: " + bridge.sourceLabel
            textFormat: Text.PlainText
            maximumLineCount: 2
            elide: Text.ElideMiddle
            wrapMode: Text.Wrap
            color: "#263447"
        }
        Controls.TabBar {
            id: narrowTabs
            objectName: "formatNarrowTabs"
            visible: !root.wide
            Layout.fillWidth: true
            Controls.TabButton { text: "Targets & preview" }
            Controls.TabButton { text: "Format options" }
        }
        GridLayout {
            id: body
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: 14
            columnSpacing: 18
            columns: root.wide ? 2 : 1
            ColumnLayout {
                visible: root.wide || narrowTabs.currentIndex === 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 420
                Layout.minimumWidth: 0
                spacing: 8
                PlotTargetsList {
                    visible: !root.bridge.graphTargetMode
                    bridge: root.bridge
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: 180
                    Layout.minimumHeight: 206
                }
                Controls.ScrollView {
                    visible: bridge.graphTargetMode
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: 180
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        Repeater {
                            model: bridge.targetGroups
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Controls.CheckBox {
                                    objectName: "formatDataset_" + modelData.children[0].datasetId
                                    Layout.fillWidth: true
                                    text: modelData.label
                                    contentItem: Text {
                                        text: parent.text
                                        font: parent.font
                                        leftPadding: parent.indicator.width + parent.spacing
                                        color: "#263447"
                                        wrapMode: Text.Wrap
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                    tristate: true
                                    checkState: modelData.checkState
                                    enabled: !bridge.adjustment
                                    nextCheckState: function() { return checkState === Qt.Checked ? Qt.Unchecked : Qt.Checked }
                                    onClicked: modelData.setChecked(checkState === Qt.Checked)
                                }
                                Repeater {
                                    model: modelData.children
                                    Controls.CheckBox {
                                        required property var modelData
                                        objectName: "formatGraph_" + modelData.datasetId + "_" + modelData.graphId
                                        Layout.fillWidth: true
                                        Layout.leftMargin: 22
                                        text: modelData.label
                                        contentItem: Text {
                                            text: parent.text
                                            font: parent.font
                                            leftPadding: parent.indicator.width + parent.spacing
                                            color: "#263447"
                                            wrapMode: Text.Wrap
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        checked: modelData.checked
                                        enabled: !bridge.adjustment
                                        onToggled: modelData.setChecked(checked)
                                    }
                                }
                            }
                        }
                        Controls.Label { visible: bridge.adjustment; text: "Targets fixed for this paste. Deselect format groups to restore originals."; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
                RowLayout {
                    visible: root.bridge.graphTargetMode && !root.bridge.adjustment
                    SurfaceButton { text: "Select all"; onClicked: bridge.selectAll(true) }
                    SurfaceButton { text: "Clear"; onClicked: bridge.selectAll(false) }
                }
                Controls.TabBar {
                    id: previewTabs
                    objectName: "formatPreviewTabs"
                    Layout.fillWidth: true
                    Controls.TabButton { text: "Changes" }
                    Controls.TabButton { text: "Page preview" }
                }
                StackLayout {
                    currentIndex: previewTabs.currentIndex
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: 260
                    Layout.minimumHeight: 110
                    Controls.ScrollView {
                        clip: true
                        objectName: "formatChangesScroll"
                        contentWidth: availableWidth
                        Controls.TextArea {
                            objectName: "formatChanges"
                            text: bridge.previewText
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            font: Qt.application.font
                            color: "#263447"
                            Accessible.name: "Changes for each selected active graph"
                        }
                    }
                    ColumnLayout {
                        Controls.ComboBox {
                            objectName: "formatPreviewTarget"
                            Layout.fillWidth: true
                            model: bridge.previewLabels
                            currentIndex: bridge.previewIndex
                            onActivated: bridge.setPreviewIndex(currentIndex)
                            Accessible.name: "Page preview target"
                        }
                        Image {
                            objectName: "formatPagePreview"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            source: bridge.previewUrl
                            fillMode: Image.PreserveAspectFit
                            cache: false
                            asynchronous: false
                            Accessible.name: "Isolated page preview"
                            Accessible.role: Accessible.Graphic
                        }
                        Controls.Label {
                            visible: text.length > 0
                            text: bridge.previewError
                            wrapMode: Text.Wrap
                            color: "#af2f35"
                            Layout.fillWidth: true
                        }
                    }
                }
            }
            ApplyFormatOptions {
                bridge: root.bridge
                visible: root.wide || narrowTabs.currentIndex === 1
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 360
                Layout.minimumWidth: 0
            }
        }
        PlotDialogFooter {
            bridge: root.bridge
            dialogHost: root.dialogHost
            actionText: bridge.actionText
            Layout.fillWidth: true
        }
    }
}


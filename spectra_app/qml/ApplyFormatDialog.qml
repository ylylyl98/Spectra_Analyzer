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
                    bridge: root.bridge
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredHeight: 180
                    Layout.minimumHeight: 206
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
            actionText: "Apply format"
            Layout.fillWidth: true
        }
    }
}


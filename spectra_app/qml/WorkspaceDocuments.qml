import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "workspaceDocuments"
    required property QtObject documents
    color: colors.disabled
    AppColors { id: colors }
    Component.onCompleted: documents.attachRoot(root)
    function report() { documents.reportViewport(view.width, view.height, view.contentX, view.contentY) }
    Connections {
        target: root.documents
        function onStateChanged() { if (root.documents.mode === "tabbed") { view.contentX = 0; view.contentY = 0 } }
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            spacing: 4
            SurfaceButton { objectName: "documentsFree"; text: "Free"; checkable: true; checked: root.documents.mode === "free"; onClicked: root.documents.setMode("free") }
            SurfaceButton { objectName: "documentsTabbed"; text: "Tabs"; checkable: true; checked: root.documents.mode === "tabbed"; onClicked: root.documents.setMode("tabbed") }
            SurfaceButton { objectName: "documentsTile"; text: "Tile"; onClicked: root.documents.tile() }
            SurfaceButton { objectName: "documentsCascade"; text: "Cascade"; onClicked: root.documents.cascade() }
            Item { Layout.fillWidth: true }
        }
        Flickable {
            Layout.fillWidth: true
            Layout.preferredHeight: root.documents.mode === "tabbed" && root.documents.count > 0 ? 32 : 0
            visible: Layout.preferredHeight > 0
            contentWidth: tabs.width
            clip: true
            Row {
                id: tabs
                Repeater {
                    model: root.documents.model
                    SurfaceButton {
                        id: documentTab
                        required property string documentTooltip
                        required property var documentKey
                        required property string documentTitle
                        required property bool selected
                        property string inputToken: ""
                        objectName: "documentTab_" + documentKey.join("_")
                        text: documentTitle
                        checkable: true
                        checked: selected
                        onPressed: inputToken = root.documents.captureTabInput(documentKey, this)
                        onClicked: root.documents.activateTab(documentKey, this, inputToken)
                        onCanceled: inputToken = ""
                        Controls.ToolTip {
                            id: tabTooltip
                            objectName: "documentTabTooltip"
                            popupType: Controls.Popup.Window
                            visible: documentTab.hovered && documentTab.documentTooltip.length > 0
                            text: documentTab.documentTooltip
                            delay: 650; font: Qt.application.font
                            width: Math.min(640, implicitWidth)
                            contentItem: Text {
                                text: tabTooltip.text
                                textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere
                                font: Qt.application.font; color: colors.text
                            }
                            background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                        }
                    }
                }
            }
        }
        Flickable {
            id: view
            objectName: "workspaceDocumentViewport"
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: root.documents.mode === "tabbed" ? width : root.documents.contentWidth
            contentHeight: root.documents.mode === "tabbed" ? height : root.documents.contentHeight
            interactive: root.documents.mode === "free"
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            onWidthChanged: root.report()
            onHeightChanged: root.report()
            onContentXChanged: root.report()
            onContentYChanged: root.report()
            Controls.ScrollBar.horizontal: Controls.ScrollBar {}
            Controls.ScrollBar.vertical: Controls.ScrollBar {}
            Repeater {
                model: root.documents.model
                WorkspaceDocumentFrame { documents: root.documents }
            }
            Text {
                anchors.centerIn: parent
                visible: root.documents.count === 0
                text: "Open a Workbook or graph from Project, or start with data files."
                color: colors.secondary
                font: Qt.application.font
                width: Math.min(parent.width - 32, implicitWidth)
                wrapMode: Text.Wrap
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}

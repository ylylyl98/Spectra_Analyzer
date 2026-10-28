import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property QtObject bridge
    color: colors.surface
    implicitWidth: 900
    implicitHeight: 650
    AppColors { id: colors }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        Controls.ScrollView {
            id: provenanceScroll
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(provenance.implicitHeight, Math.max(80, root.height * 0.24))
            contentWidth: availableWidth
            clip: true
            Controls.TextArea {
                id: provenance
                objectName: "comparisonSource"
                width: provenanceScroll.availableWidth
                text: root.bridge.sourceText
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.PlainText
                color: colors.text
                font: root.bridge.applicationFont
                padding: 2
                background: Rectangle {
                    color: "transparent"
                    border.width: provenance.activeFocus ? 1 : 0
                    border.color: colors.accent
                    radius: 2
                }
                Accessible.name: "Captured source, title and version"
            }
        }

        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            spacing: 8

            Controls.Label {
                text: "Display:"
                height: zoomControl.height
                verticalAlignment: Text.AlignVCenter
                color: colors.text
                font: root.bridge.applicationFont
            }
            Controls.ComboBox {
                id: zoomControl
                objectName: "comparisonZoom"
                width: Math.max(112, implicitWidth)
                height: Math.max(32, implicitHeight)
                font: root.bridge.applicationFont
                model: root.bridge.zoomLevels
                currentIndex: root.bridge.zoomLevels.indexOf(root.bridge.zoom)
                onActivated: root.bridge.setZoom(currentText)
                focusPolicy: Qt.StrongFocus
                Accessible.name: "Comparison display zoom"
                background: Rectangle {
                    color: zoomControl.down ? colors.pressed : zoomControl.hovered ? colors.hover : colors.surface
                    radius: 4
                    border.width: zoomControl.visualFocus ? 2 : 1
                    border.color: zoomControl.visualFocus ? colors.accent : colors.border
                }
            }
            SurfaceButton {
                objectName: "comparisonExportSvg"
                text: "Export SVG"
                font: root.bridge.applicationFont
                implicitHeight: Math.max(32, contentItem.implicitHeight + topPadding + bottomPadding)
                Accessible.name: "Export captured SVG"
                onClicked: root.bridge.chooseExport("svg")
            }
            SurfaceButton {
                objectName: "comparisonExportPng"
                text: "Export PNG"
                font: root.bridge.applicationFont
                implicitHeight: Math.max(32, contentItem.implicitHeight + topPadding + bottomPadding)
                Accessible.name: "Export captured PNG"
                onClicked: root.bridge.chooseExport("png")
            }
        }

        Controls.Label {
            objectName: "comparisonDimensions"
            Layout.fillWidth: true
            text: root.bridge.dimensionsText
            color: colors.secondary
            wrapMode: Text.Wrap
            font: root.bridge.applicationFont
            Accessible.name: "Comparison export dimensions"
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: colors.disabled
            border.color: viewport.activeFocus ? colors.accent : colors.border
            border.width: viewport.activeFocus ? 2 : 1
            radius: 4

            Flickable {
                id: viewport
                objectName: "comparisonViewport"
                anchors.fill: parent
                anchors.margins: 1
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                contentWidth: Math.max(width, pagePreview.width + 24)
                contentHeight: Math.max(height, pagePreview.height + 24)
                activeFocusOnTab: true
                readonly property real displayScale: root.bridge.zoom === "Fit"
                    ? Math.min(Math.max(1, width - 24) / root.bridge.pageWidth,
                               Math.max(1, height - 24) / root.bridge.pageHeight)
                    : parseInt(root.bridge.zoom) / 100
                Controls.ScrollBar.horizontal: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
                Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
                Accessible.role: Accessible.Pane
                Accessible.name: "Read-only comparison snapshot; arrow keys scroll"
                Keys.onLeftPressed: contentX = Math.max(0, contentX - 40)
                Keys.onRightPressed: contentX = Math.min(Math.max(0, contentWidth - width), contentX + 40)
                Keys.onUpPressed: contentY = Math.max(0, contentY - 40)
                Keys.onDownPressed: contentY = Math.min(Math.max(0, contentHeight - height), contentY + 40)

                Image {
                    id: pagePreview
                    objectName: "comparisonPreview"
                    x: (viewport.contentWidth - width) / 2
                    y: (viewport.contentHeight - height) / 2
                    width: root.bridge.pageWidth * viewport.displayScale
                    height: root.bridge.pageHeight * viewport.displayScale
                    source: "image://comparison-snapshot/page"
                    // Qt applies the window DPR to the provider request.
                    sourceSize.width: Math.max(1, Math.ceil(width))
                    sourceSize.height: Math.max(1, Math.ceil(height))
                    cache: false
                    smooth: true
                    Accessible.role: Accessible.Graphic
                    Accessible.name: "Read-only captured formal page"
                }
            }
        }

        Controls.Label {
            objectName: "comparisonStatus"
            Layout.fillWidth: true
            text: root.bridge.statusText
            textFormat: Text.PlainText
            color: colors.secondary
            wrapMode: Text.Wrap
            font: root.bridge.applicationFont
            Accessible.name: "Comparison export status"
        }
    }
}

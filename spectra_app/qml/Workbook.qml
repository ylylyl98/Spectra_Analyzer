import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property QtObject bridge
    property font controlFont: Qt.application.font
    readonly property var sheet: root.bridge.currentSheet
    color: colors.surface
    focus: true
    AppColors { id: colors }
    Connections {
        target: root.bridge
        function onSheetAboutToChange() { cells.prepareSheetSwitch() }
    }
    Flickable {
        id: contentViewport
        objectName: "workbookContentViewport"
        anchors.fill: parent
        anchors.margins: 6
        contentWidth: width
        contentHeight: contentLayout.height
        interactive: contentHeight > height
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        Controls.ScrollBar.vertical: Controls.ScrollBar { objectName: "workbookContentScroll" }
        ColumnLayout {
            id: contentLayout
            width: contentViewport.width
            height: Math.max(contentViewport.height, implicitHeight)
            spacing: 4
            Text {
                visible: text.length > 0
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: root.sheet ? root.sheet.provenance : ""
                font: root.controlFont
                textFormat: Text.PlainText
                color: colors.secondary
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                SurfaceButton {
                    objectName: "workbookMappingToggle"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: "X/Y controls"
                    checkable: true
                    onClicked: mapping.visible = checked
                    font: root.controlFont
                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: text
                }
                Item { Layout.fillWidth: true }
                SurfaceButton {
                    objectName: "workbookSnapshot"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: "Save snapshot…"
                    font: root.controlFont
                    enabled: !!root.sheet && root.sheet.rowCount > 0
                    onClicked: if (root.sheet) root.sheet.snapshot()
                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: text
                    Accessible.name: "Save named Sheet snapshot"
                }
            }
            ColumnLayout {
                id: mapping
                visible: false
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: "X:"; font: root.controlFont; color: colors.text }
                    Controls.ComboBox {
                        id: xChoice
                        objectName: "workbookXColumn"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        model: root.sheet ? root.sheet.columnChoices : []
                        textRole: "label"
                        valueRole: "value"
                        currentIndex: root.sheet ? root.sheet.xColumn : -1
                        enabled: !!root.sheet && root.sheet.xEditable
                        font: root.controlFont
                        contentItem: Text {
                            text: xChoice.displayText
                            textFormat: Text.PlainText
                            font: root.controlFont
                            color: xChoice.enabled ? colors.text : colors.disabledText
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                        delegate: Controls.ItemDelegate {
                            required property var modelData
                            width: ListView.view.width
                            text: modelData.label
                            contentItem: Text { text: modelData.label; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; elide: Text.ElideRight }
                            enabled: modelData.numeric
                            font: root.controlFont
                        }
                        onActivated: root.sheet.setX(currentValue)
                        Accessible.name: "X column"
                    }
                }
                ListView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 65
                    clip: true
                    model: root.sheet ? root.sheet.columnChoices : []
                    Controls.ScrollBar.vertical: Controls.ScrollBar {}
                    delegate: Controls.CheckDelegate {
                        required property var modelData
                        width: ListView.view.width
                        height: 30
                        text: modelData.label
                        contentItem: Text { text: modelData.label; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; elide: Text.ElideRight }
                        font: root.controlFont
                        enabled: modelData.numeric && root.sheet.xColumn !== modelData.value
                        checked: modelData.checked
                        onClicked: root.sheet.setY(modelData.value, checked)
                    }
                }
            }
            SheetTable {
                id: cells
                objectName: "workbookSheetTable"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.fillHeight: true
                Layout.minimumHeight: implicitHeight
                sheet: root.sheet
                controlFont: root.controlFont
            }
            Text {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: "Headers choose Y · Ctrl/Shift adds columns · Right-click sets X · Double-click Long Name/Units to edit · F2/Shift+F2 · Ctrl+C copies"
                font: root.controlFont
                color: colors.secondary
                wrapMode: Text.Wrap
            }
            ListView {
                objectName: "workbookSheetNavigation"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.preferredHeight: 36
                orientation: ListView.Horizontal
                clip: true
                spacing: 3
                model: root.bridge.sheets
                Controls.ScrollBar.horizontal: Controls.ScrollBar {}
                delegate: SurfaceButton {
                    required property var modelData
                    objectName: "workbookSheet_" + modelData.value
                    text: modelData.label
                    contentItem: Text {
                        text: parent.text
                        textFormat: Text.PlainText
                        font: root.controlFont
                        color: colors.text
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                    }
                    font: root.controlFont
                    checked: root.bridge.currentSheetId === modelData.value
                    onClicked: root.bridge.selectSheet(modelData.value)
                    Controls.ToolTip.visible: hovered
                    Controls.ToolTip.text: modelData.tooltip
                    Accessible.name: "Sheet " + modelData.label
                }
            }
        }
    }
}

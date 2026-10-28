import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: editor
    required property var bridge
    readonly property real rowHeight: Math.max(26, metrics.height + 8)
    spacing: 6
    AppColors { id: colors }
    FontMetrics { id: metrics; font: bridge.applicationFont }
    Controls.Label {
        objectName: "sheetProvenance"
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        text: bridge.provenance
        font: bridge.applicationFont
        color: colors.text
        elide: Text.ElideMiddle
        textFormat: Text.PlainText
        Accessible.name: text
    }
    RowLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Controls.Label { text: "Values: " + bridge.valueLabel; font: bridge.applicationFont; color: colors.secondary }
        Item { Layout.fillWidth: true
            Layout.minimumWidth: 0 }
        Controls.Label { text: "X:"; font: bridge.applicationFont; color: colors.text }
        Controls.ComboBox {
            objectName: "sheetXColumn"
            Layout.preferredWidth: Math.min(230, editor.width * .5)
            Layout.minimumWidth: 80
            Layout.fillWidth: true
            font: bridge.applicationFont
            model: bridge.xOptions
            textRole: "label"
            valueRole: "column"
            currentIndex: bridge.xIndex
            enabled: bridge.xEditable
            onActivated: bridge.setX(currentValue)
            Accessible.name: "X column for selected Sheet"
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.fillHeight: true
        spacing: 8
        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.fillHeight: true
            Layout.preferredWidth: 145
            spacing: 6
            Controls.Label { text: "Y columns"; font: bridge.applicationFont; color: colors.secondary }
            Controls.ScrollView {
                objectName: "sheetYScroll"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ListView {
                    id: columns
                    objectName: "sheetYColumns"
                    model: bridge.columnModel
                    reuseItems: true
                    boundsBehavior: Flickable.StopAtBounds
                    delegate: Controls.CheckBox {
                        id: yCheck
                        required property int column
                        required property string label
                        required property bool included
                        required property bool available
                        objectName: "sheetY_" + column
                        width: ListView.view.width
                        height: Math.max(28, implicitHeight)
                        text: label
                        font: bridge.applicationFont
                        checked: included
                        enabled: available
                        onClicked: bridge.setY(column, checked)
                        Accessible.name: "Y column " + label
                        Controls.ToolTip {
                            visible: yCheck.hovered
                            text: yCheck.label
                            contentItem: Text {
                                text: yCheck.label
                                textFormat: Text.PlainText
                                font: bridge.applicationFont
                                color: colors.text
                            }
                        }
                        contentItem: Text {
                            text: parent.text
                            textFormat: Text.PlainText
                            font: parent.font
                            color: parent.enabled ? colors.text : colors.disabledText
                            leftPadding: parent.indicator.width + parent.spacing
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                    }
                }
            }
            Controls.Button {
                objectName: "sheetMatchGates"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: "Match selected\nGates"
                font: bridge.applicationFont
                enabled: bridge.canMatchGates
                onClicked: bridge.matchSelectedGates()
                Accessible.name: "Match selected Gates"
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: "Apply exact physical Gate values to checked DAT Original and Processed Sheets."
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.fillHeight: true
            Layout.preferredWidth: 300
            spacing: 4
            Controls.Label {
                objectName: "sheetPreviewInfo"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: bridge.previewInfo
                font: bridge.applicationFont
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
                color: colors.secondary
                textFormat: Text.PlainText
            }
            Controls.HorizontalHeaderView {
                id: headers
                objectName: "sheetPreviewHeaders"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.preferredHeight: editor.rowHeight
                syncView: table
                clip: true
                delegate: Rectangle {
                    required property var display
                    required property int column
                    implicitWidth: 112
                    implicitHeight: editor.rowHeight
                    color: colors.hover
                    border.color: colors.divider
                    HoverHandler { id: headerHover }
                    Controls.ToolTip {
                        visible: headerHover.hovered
                        text: display
                        contentItem: Text {
                            text: display
                            textFormat: Text.PlainText
                            font: bridge.applicationFont
                            color: colors.text
                        }
                    }
                    Text {
                        anchors.fill: parent
                        anchors.margins: 5
                        objectName: "sheetPreviewHeaderText_" + column
                        text: display
                        textFormat: Text.PlainText
                        font: bridge.applicationFont
                        color: colors.text
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
            TableView {
                id: table
                objectName: "sheetPreview"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.fillHeight: true
                clip: true
                model: bridge.previewModel
                reuseItems: true
                boundsBehavior: Flickable.StopAtBounds
                rowHeightProvider: function(row) { return editor.rowHeight }
                columnWidthProvider: function(column) { return 112 }
                Controls.ScrollBar.vertical: Controls.ScrollBar {}
                Controls.ScrollBar.horizontal: Controls.ScrollBar {}
                Accessible.name: "Selected Sheet numeric preview"
                delegate: Rectangle {
                    required property string display
                    required property int row
                    required property int column
                    implicitWidth: 112
                    implicitHeight: editor.rowHeight
                    color: row % 2 ? colors.hover : colors.surface
                    border.color: colors.divider
                    HoverHandler { id: cellHover }
                    Controls.ToolTip {
                        visible: cellHover.hovered
                        text: display
                        contentItem: Text {
                            text: display
                            textFormat: Text.PlainText
                            font: bridge.applicationFont
                            color: colors.text
                        }
                    }
                    Text {
                        anchors.fill: parent
                        anchors.margins: 5
                        objectName: "sheetPreviewCellText_" + row + "_" + column
                        text: display
                        textFormat: Text.PlainText
                        font: bridge.applicationFont
                        color: colors.text
                        horizontalAlignment: Text.AlignRight
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}

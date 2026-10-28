import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

ColumnLayout {
    id: panel
    required property var bridge
    spacing: 6
    AppColors { id: colors }
    Controls.Button {
        objectName: "sheetOriginals"
        text: "Select only Originals\n(all Workbooks)"
        font: bridge.applicationFont
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        onClicked: bridge.selectOriginals()
        Accessible.name: "Select only Original Sheets in all Workbooks"
    }
    Controls.Label { text: "Include Sheets"; color: colors.secondary; font: bridge.applicationFont }
    Rectangle {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.fillHeight: true
        color: colors.surface
        border.color: colors.border
        ListView {
            id: sources
            objectName: "sheetSources"
            anchors.fill: parent
            anchors.margins: 1
            clip: true
            model: bridge.sourceModel
            currentIndex: bridge.currentRow
            reuseItems: true
            activeFocusOnTab: true
            boundsBehavior: Flickable.StopAtBounds
            Controls.ScrollBar.vertical: Controls.ScrollBar {}
            Keys.onSpacePressed: bridge.toggleSource(bridge.sourceIdAt(currentIndex))
            Keys.onDownPressed: {
                bridge.selectSource(bridge.sourceIdAt(Math.min(count - 1, currentIndex + 1)))
            }
            Keys.onUpPressed: {
                bridge.selectSource(bridge.sourceIdAt(Math.max(0, currentIndex - 1)))
            }
            delegate: Rectangle {
                id: row
                required property int index
                required property string stableId
                required property string rowKind
                required property int checkState
                required property int indent
                required property string label
                required property string summary
                required property string tooltip
                width: ListView.view.width
                height: Math.max(38, details.implicitHeight + 10)
                color: bridge.currentId === stableId ? colors.selected : colors.surface
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 5 + row.indent * 12
                    spacing: 2
                    Controls.CheckBox {
                        id: sourceCheck
                        objectName: "sheetSourceCheck_" + row.index
                        Layout.preferredWidth: 28
                        tristate: row.rowKind === "workbook"
                        checkState: row.checkState
                        nextCheckState: function() { return row.checkState === Qt.Checked ? Qt.Unchecked : Qt.Checked }
                        onClicked: bridge.toggleSource(row.stableId)
                        Accessible.name: "Include " + row.label
                        Controls.ToolTip {
                            visible: sourceCheck.hovered
                            text: row.tooltip
                            contentItem: Text {
                                text: row.tooltip
                                textFormat: Text.PlainText
                                font: bridge.applicationFont
                                color: colors.text
                            }
                        }
                    }
                    Controls.ItemDelegate {
                        id: sourceLabel
                        objectName: "sheetSource_" + row.index
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        Layout.fillHeight: true
                        padding: 2
                        onClicked: { bridge.selectSource(row.stableId); sources.forceActiveFocus() }
                        Accessible.name: row.label + (row.summary.length ? "; " + row.summary : "")
                        contentItem: Column {
                            id: details
                            spacing: 2
                            Text {
                                width: parent.width
                                objectName: "sheetSourceLabel_" + row.index
                                text: row.label
                                textFormat: Text.PlainText
                                color: colors.text
                                font: bridge.applicationFont
                                elide: Text.ElideRight
                            }
                            Text {
                                visible: row.summary.length > 0
                                width: parent.width
                                objectName: "sheetSourceSummary_" + row.index
                                text: row.summary
                                textFormat: Text.PlainText
                                font: bridge.applicationFont
                                color: colors.secondary
                                elide: Text.ElideRight
                            }
                        }
                        Controls.ToolTip {
                            visible: sourceLabel.hovered
                            text: row.tooltip + (row.summary.length ? "\n" + row.summary : "")
                            contentItem: Text {
                                text: row.tooltip + (row.summary.length ? "\n" + row.summary : "")
                                textFormat: Text.PlainText
                                font: bridge.applicationFont
                                color: colors.text
                            }
                        }
                    }
                }
                Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    border.width: sources.activeFocus && sources.currentIndex === row.index ? 2 : 0
                    border.color: colors.accent
                }
            }
        }
    }
}

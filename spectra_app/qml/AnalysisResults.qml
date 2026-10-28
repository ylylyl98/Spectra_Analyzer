import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "analysisResults"
    required property var bridge
    required property var dialogHost
    property font controlFont: Qt.application.font
    readonly property var state: bridge.state
    readonly property int rowHeight: Math.max(32, metrics.height + 12)
    width: 1000; height: 640
    color: colors.surface
    focus: true
    Accessible.name: "Analysis Results"
    AppColors { id: colors }
    FontMetrics { id: metrics; font: root.controlFont }
    function locate(owner) { if (bridge.locate(owner)) dialogHost.reject() }
    component Note: Text {
        Layout.fillWidth: true; Layout.minimumWidth: 0
        font: root.controlFont; color: colors.secondary; textFormat: Text.PlainText; wrapMode: Text.Wrap
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12; spacing: 8
        Note { text: "All Gates · " + (root.state.rowCount || 0) + " rows. Select a cell to read its full value; double-click a row to locate its Gate." }
        ColumnLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true
            spacing: 0
            Controls.HorizontalHeaderView {
                id: header
                objectName: "resultsHeader"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                implicitHeight: root.rowHeight
                syncView: table
                clip: true
                delegate: Rectangle {
                    required property var display
                    implicitWidth: 130; implicitHeight: root.rowHeight
                    color: colors.hover; border.color: colors.border
                    Text { anchors.fill: parent; anchors.margins: 6; text: display; font: root.controlFont; color: colors.text; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText }
                    Accessible.role: Accessible.ColumnHeader
                    Accessible.name: String(display)
                }
            }
            TableView {
                id: table
                objectName: "resultsTable"
                property string keyboardOwner: ""
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true
                model: root.bridge.tableModel
                clip: true; reuseItems: true
                columnSpacing: 0; rowSpacing: 0
                columnWidthProvider: function(column) { return column === 2 ? 280 : column === 0 ? 100 : 140 }
                rowHeightProvider: function(row) { return root.rowHeight }
                onActiveFocusChanged: if (activeFocus) keyboardOwner = root.bridge.ownerToken
                Controls.ScrollBar.horizontal: Controls.ScrollBar { policy: Controls.ScrollBar.AlwaysOn }
                Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }
                delegate: Rectangle {
                    id: cell
                    required property int row
                    required property int column
                    required property var display
                    readonly property bool selected: root.state.selectedRow === row && root.state.selectedColumn === column
                    objectName: "resultCell_" + row + "_" + column
                    implicitWidth: 140; implicitHeight: root.rowHeight
                    color: selected ? colors.selected : root.state.selectedRow === row ? colors.hover : colors.surface
                    border.color: selected ? colors.accent : colors.divider
                    border.width: selected ? 2 : 1
                    Text { anchors.fill: parent; anchors.margins: 6; text: cell.display; color: colors.text; font: root.controlFont; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText }
                    Accessible.role: Accessible.Cell
                    Accessible.name: "Row " + (row + 1) + ", column " + (column + 1) + ": " + display
                    Accessible.selected: selected
                    MouseArea {
                        id: pointer
                        property string actionOwner: ""
                        anchors.fill: parent; hoverEnabled: true
                        enabled: root.bridge.enabled
                        onPressed: { actionOwner = root.bridge.ownerToken; table.forceActiveFocus(Qt.MouseFocusReason) }
                        onClicked: root.bridge.selectCell(cell.row, cell.column, actionOwner)
                        onDoubleClicked: if (root.bridge.selectCell(cell.row, cell.column, actionOwner)) root.locate(actionOwner)
                        Controls.ToolTip.visible: containsMouse && !pressed
                        Controls.ToolTip.text: String(cell.display)
                        Controls.ToolTip.delay: 650
                    }
                }
                Keys.onPressed: function(event) {
                    if (keyboardOwner !== root.bridge.ownerToken) return
                    let row = root.state.selectedRow, column = root.state.selectedColumn
                    if (event.key === Qt.Key_Left) --column
                    else if (event.key === Qt.Key_Right) ++column
                    else if (event.key === Qt.Key_Up) --row
                    else if (event.key === Qt.Key_Down) ++row
                    else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { root.locate(keyboardOwner); event.accepted = true; return }
                    else return
                    row = Math.max(0, Math.min(root.state.rowCount - 1, row))
                    column = Math.max(0, Math.min(root.state.columnCount - 1, column))
                    if (root.bridge.selectCell(row, column, keyboardOwner)) table.positionViewAtCell(Qt.point(column, row), TableView.Contain)
                    event.accepted = true
                }
            }
        }
        Note { id: selectedHeader; objectName: "resultsSelectedHeader"; text: root.state.selectedHeader || "Selected cell"; color: colors.text; Accessible.name: "Selected column: " + text }
        Controls.ScrollView {
            Layout.fillWidth: true; Layout.minimumWidth: 0
            Layout.preferredHeight: Math.min(120, root.height * 0.23)
            clip: true; contentWidth: availableWidth
            Controls.TextArea {
                objectName: "resultsSelectedText"
                text: root.state.selectedText || ""
                readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                font: root.controlFont; color: colors.text
                selectionColor: colors.accent; selectedTextColor: colors.surface
                textFormat: TextEdit.PlainText
                Accessible.name: "Full selected cell text"
                Accessible.description: text
                background: Rectangle { color: colors.surface; border.color: colors.border; radius: 3 }
            }
        }
        Note { visible: !!root.bridge.errorText; text: root.bridge.errorText; color: colors.error; Accessible.name: "Results error" }
        Flow {
            Layout.fillWidth: true; Layout.minimumWidth: 0
            Layout.preferredHeight: implicitHeight
            spacing: 6
            AnalysisAction { bridge: root.bridge; objectName: "resultsLocate"; text: "Locate"; font: root.controlFont; enabled: root.bridge.enabled && root.state.selectedRow >= 0; onTriggered: function(owner) { root.locate(owner) } }
            AnalysisAction { bridge: root.bridge; objectName: "resultsSave"; text: "Save workbook"; font: root.controlFont; enabled: root.bridge.enabled; onTriggered: function(owner) { if (bridge.saveWorkbook(owner)) root.dialogHost.reject() } }
            AnalysisAction { bridge: root.bridge; objectName: "resultsExport"; text: "Export CSV…"; font: root.controlFont; enabled: root.bridge.enabled; onTriggered: function(owner) { bridge.exportCsv(owner) } }
            AnalysisAction { bridge: root.bridge; objectName: "resultsClose"; text: "Close"; font: root.controlFont; onTriggered: root.dialogHost.reject() }
        }
    }
}

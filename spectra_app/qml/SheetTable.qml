import QtQuick
import QtQuick.Controls.Basic as Controls

FocusScope {
    id: root
    property var sheet
    property font controlFont: Qt.application.font
    readonly property int rowHeight: Math.max(26, metrics.height + 10)
    readonly property int headerHeight: rowHeight * 3
    implicitHeight: headerHeight + 2 * rowHeight + hbar.implicitHeight
    readonly property int cornerWidth: Math.max(86, metrics.advanceWidth + 16)
    property bool restoring: false
    AppColors { id: colors }
    TextMetrics { id: metrics; text: "Long Name"; font: root.controlFont }
    function prepareSheetSwitch() {
        if (sheet) sheet.rememberScroll(body.contentX, body.contentY)
        restoring = true
    }
    function revealColumn(column) {
        if (!sheet || column < 0) return
        let left = sheet.columnPosition(column)
        let right = left + sheet.columnWidth(column)
        if (left < body.contentX) body.contentX = left
        else if (right > body.contentX + body.width) body.contentX = right - body.width
    }
    function revealCell(row, column) {
        revealColumn(column)
        let top = row * root.rowHeight
        if (top < body.contentY) body.contentY = top
        else if (top + root.rowHeight > body.contentY + body.height)
            body.contentY = top + root.rowHeight - body.height
    }
    function restoreScroll() {
        restoring = true
        body.forceLayout()
        if (sheet) {
            body.contentX = Math.max(0, Math.min(sheet.savedScrollX, body.contentWidth - body.width))
            body.contentY = Math.max(0, Math.min(sheet.savedScrollY, body.contentHeight - body.height))
        }
        restoring = false
    }
    function syncEditor(activate) {
        if (root.sheet && root.sheet.editorColumn >= 0) {
            editor.text = root.sheet.editorText
            if (activate) {
                editor.forceActiveFocus()
                editor.selectAll()
            }
        } else if (activate) {
            body.forceActiveFocus()
        }
    }
    Component.onCompleted: syncEditor(false)
    onSheetChanged: Qt.callLater(function() { root.restoreScroll(); root.syncEditor(false) })
    Connections {
        target: root.sheet
        function onViewRestoreRequested() { root.restoring = true; Qt.callLater(root.restoreScroll) }
        function onWidthsChanged() { body.forceLayout(); headers.forceLayout() }
        function onRevealCellRequested(row, column) { root.revealCell(row, column) }
        function onRevealColumnRequested(column) { root.revealColumn(column) }
        function onEditorChanged() {
            root.syncEditor(true)
        }
    }
    Column {
        width: root.cornerWidth
        Repeater {
            model: ["All", "Long Name", "Units"]
            Rectangle {
                required property string modelData
                required property int index
                width: root.cornerWidth
                height: root.rowHeight
                color: colors.hover
                border.color: colors.border
                Text { anchors.centerIn: parent; text: modelData; font: root.controlFont; color: colors.text }
                MouseArea {
                    anchors.fill: parent
                    enabled: index === 0
                    onClicked: { if (root.sheet) root.sheet.selectAll(); body.forceActiveFocus() }
                }
                Accessible.role: index === 0 ? Accessible.Button : Accessible.StaticText
                Accessible.name: index === 0 ? "Select all Sheet cells" : modelData
                Accessible.onPressAction: if (index === 0 && root.sheet) root.sheet.selectAll()
            }
        }
    }
    TableView {
        id: headers
        objectName: "sheetHeaders"
        x: root.cornerWidth
        width: Math.max(0, root.width - x - vbar.width)
        height: root.headerHeight
        clip: true
        model: root.sheet ? root.sheet.headerModel : null
        contentX: body.contentX
        interactive: false
        rowSpacing: 0
        columnSpacing: 0
        reuseItems: true
        columnWidthProvider: function(column) {
            let revision = root.sheet ? root.sheet.widthRevision : 0
            return root.sheet ? root.sheet.columnWidth(column) : 100
        }
        rowHeightProvider: function(row) { return root.rowHeight }
        delegate: Rectangle {
            required property int row
            required property int column
            required property string label
            required property string tooltip
            required property bool gate
            implicitWidth: 100
            implicitHeight: root.rowHeight
            color: {
                let revision = root.sheet ? root.sheet.selectionRevision : 0
                return root.sheet && root.sheet.isSelected(0, column) ? colors.selected : colors.hover
            }
            border.color: colors.border
            Text {
                anchors.fill: parent
                anchors.margins: 6
                text: label
                textFormat: Text.PlainText
                font: { let f = Qt.font(root.controlFont); f.bold = gate || (root.sheet && root.sheet.yColumns.indexOf(column) >= 0); return f }
                color: colors.text
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            Accessible.role: Accessible.ColumnHeader
            Accessible.name: tooltip
        }
        MouseArea {
            id: headerInput
            parent: headers
            z: 2
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            hoverEnabled: true
            property int resizing: -1
            property real startX: 0
            property real startWidth: 0
            property int pressedRow: -1
            property int pressedColumn: -1
            property var gestureSheet: null
            function cell(mouse) { return headers.cellAtPosition(Qt.point(mouse.x + headers.contentX, mouse.y), true) }
            onPressed: function(mouse) {
                let p = cell(mouse)
                gestureSheet = root.sheet
                pressedRow = p.y
                pressedColumn = p.x
                if (!gestureSheet || p.x < 0) return
                let edge = gestureSheet.columnPosition(p.x) + gestureSheet.columnWidth(p.x) - headers.contentX
                if (mouse.button === Qt.LeftButton && edge - mouse.x <= 6) {
                    resizing = p.x; startX = mouse.x; startWidth = gestureSheet.columnWidth(p.x)
                } else if (mouse.button === Qt.LeftButton && p.y === 0) {
                    gestureSheet.beginHeader(p.x, mouse.modifiers)
                    body.forceActiveFocus()
                } else if (mouse.button === Qt.RightButton) {
                    menu.column = p.x
                    menu.ownerSheet = gestureSheet
                    if (gestureSheet.openColumnContext(p.x, p.y)) menu.popup(mouse.x + headers.x, mouse.y)
                }
            }
            onPositionChanged: function(mouse) {
                if (!pressed || !gestureSheet || gestureSheet !== root.sheet) return
                if (resizing >= 0) gestureSheet.resizeColumn(resizing, startWidth + mouse.x - startX)
                else if (pressedRow === 0) {
                    let p = cell(mouse)
                    if (p.x >= 0) gestureSheet.dragHeader(p.x)
                    if (mouse.x > width - 12) body.contentX = Math.min(body.contentWidth - body.width, body.contentX + 25)
                    if (mouse.x < 12) body.contentX = Math.max(0, body.contentX - 25)
                }
            }
            onReleased: {
                if (gestureSheet && resizing < 0 && pressedRow === 0) gestureSheet.endHeader()
                resizing = -1
            }
            onCanceled: { if (gestureSheet) gestureSheet.endHeader(); resizing = -1 }
            onDoubleClicked: function(mouse) {
                let p = cell(mouse)
                if (root.sheet && p.x >= 0 && p.y > 0 && resizing < 0)
                    root.sheet.editMetadata(p.x, p.y === 1 ? "long_name" : "units")
            }
            readonly property string fullTooltip: {
                let p = headers.cellAtPosition(Qt.point(mouseX + headers.contentX, mouseY), true)
                return root.sheet && p.x >= 0 ? root.sheet.headerTooltip(p.x, p.y) : ""
            }
            Controls.ToolTip.visible: containsMouse && !pressed && fullTooltip.length > 0
            Controls.ToolTip.text: fullTooltip
        }
    }
    Item {
        x: 0; y: root.headerHeight
        width: root.cornerWidth; height: body.height
        clip: true
        ListView {
            anchors.fill: parent
            model: root.sheet ? root.sheet.rowCount : 0
            contentY: body.contentY
            interactive: false
            delegate: Rectangle {
                required property int index
                width: root.cornerWidth; height: root.rowHeight
                color: colors.hover; border.color: colors.border
                Text { anchors.centerIn: parent; text: index + 1; font: root.controlFont; color: colors.secondary }
                Accessible.role: Accessible.RowHeader
                Accessible.name: "Row " + (index + 1)
            }
        }
        MouseArea {
            anchors.fill: parent
            property var gestureSheet: null
            function row(mouse) { return Math.max(0, Math.min(root.sheet.rowCount - 1, Math.floor((mouse.y + body.contentY) / root.rowHeight))) }
            onPressed: function(mouse) {
                gestureSheet = root.sheet
                if (gestureSheet) gestureSheet.beginRow(row(mouse), mouse.modifiers)
                body.forceActiveFocus()
            }
            onPositionChanged: function(mouse) {
                if (pressed && gestureSheet && gestureSheet === root.sheet) gestureSheet.dragRow(row(mouse))
            }
        }
    }
    TableView {
        id: body
        objectName: "sheetCells"
        x: root.cornerWidth
        y: root.headerHeight
        width: Math.max(0, root.width - x - vbar.width)
        height: Math.max(0, root.height - y - hbar.height)
        clip: true
        model: root.sheet ? root.sheet.tableModel : null
        focus: true
        reuseItems: true
        rowSpacing: 0
        columnSpacing: 0
        columnWidthProvider: headers.columnWidthProvider
        rowHeightProvider: function(row) { return root.rowHeight }
        onContentXChanged: if (!root.restoring && root.sheet) root.sheet.rememberScroll(contentX, contentY)
        onContentYChanged: if (!root.restoring && root.sheet) root.sheet.rememberScroll(contentX, contentY)
        delegate: Rectangle {
            required property int row
            required property int column
            required property string display
            implicitWidth: 100
            implicitHeight: root.rowHeight
            readonly property bool chosen: {
                let revision = root.sheet ? root.sheet.selectionRevision : 0
                return root.sheet && root.sheet.isSelected(row, column)
            }
            color: chosen ? colors.selected : row % 2 ? "#f8fafc" : colors.surface
            border.width: root.sheet && root.sheet.currentRow === row && root.sheet.currentColumn === column ? 2 : 1
            border.color: border.width === 2 ? colors.accent : colors.divider
            Text {
                anchors.fill: parent
                anchors.margins: 6
                text: display
                textFormat: Text.PlainText
                font: root.controlFont
                color: colors.text
                horizontalAlignment: Text.AlignRight
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            Accessible.role: Accessible.Cell
            Accessible.name: "Row " + (row + 1) + ", column " + (column + 1) + ": " + display
            Accessible.selected: chosen
        }
        MouseArea {
            parent: body
            z: 2
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            property var gestureSheet: null
            function cell(mouse) { return body.cellAtPosition(Qt.point(mouse.x + body.contentX, mouse.y + body.contentY), true) }
            onPressed: function(mouse) {
                let p = cell(mouse)
                gestureSheet = root.sheet
                if (gestureSheet && p.x >= 0 && p.y >= 0) {
                    if (mouse.button === Qt.RightButton) {
                        menu.column = p.x; menu.ownerSheet = gestureSheet
                        if (gestureSheet.openContext()) menu.popup(mouse.x + body.x, mouse.y + body.y)
                    } else gestureSheet.beginBody(p.y, p.x, mouse.modifiers)
                }
                body.forceActiveFocus()
            }
            onPositionChanged: function(mouse) {
                if (!pressed || !gestureSheet || gestureSheet !== root.sheet) return
                let p = cell(mouse)
                if (p.x >= 0 && p.y >= 0) gestureSheet.dragBody(p.y, p.x)
                if (mouse.y > height - 10) body.contentY = Math.min(body.contentHeight - body.height, body.contentY + root.rowHeight)
                if (mouse.y < 10) body.contentY = Math.max(0, body.contentY - root.rowHeight)
            }
            onWheel: function(wheel) {
                if (wheel.modifiers & Qt.ShiftModifier || Math.abs(wheel.angleDelta.x) > 0)
                    body.contentX = Math.max(0, Math.min(body.contentWidth - body.width, body.contentX - (wheel.angleDelta.x || wheel.angleDelta.y)))
                else body.contentY = Math.max(0, Math.min(body.contentHeight - body.height, body.contentY - wheel.angleDelta.y))
            }
        }
        Keys.onPressed: function(event) {
            if (!root.sheet) return
            if (event.matches(StandardKey.Copy)) root.sheet.copy_selection()
            else if (event.matches(StandardKey.SelectAll)) root.sheet.selectAll()
            else if (event.key === Qt.Key_Space && event.modifiers === Qt.ControlModifier) root.sheet.chooseCurrentColumn()
            else if (event.key === Qt.Key_F2) root.sheet.editMetadata(root.sheet.currentColumn, event.modifiers & Qt.ShiftModifier ? "units" : "long_name")
            else if (event.key === Qt.Key_Left) root.sheet.moveCurrent(0, -1, event.modifiers)
            else if (event.key === Qt.Key_Right) root.sheet.moveCurrent(0, 1, event.modifiers)
            else if (event.key === Qt.Key_Up) root.sheet.moveCurrent(-1, 0, event.modifiers)
            else if (event.key === Qt.Key_Down) root.sheet.moveCurrent(1, 0, event.modifiers)
            else { event.accepted = false; return }
            event.accepted = true
        }
    }
    Controls.ScrollBar {
        id: vbar
        objectName: "sheetVerticalScroll"
        x: root.width - width; y: body.y
        height: body.height
        orientation: Qt.Vertical
        active: true
        size: body.height / Math.max(body.height, body.contentHeight)
        position: body.contentY / Math.max(1, body.contentHeight)
        onPositionChanged: if (pressed) body.contentY = position * body.contentHeight
    }
    Controls.ScrollBar {
        id: hbar
        objectName: "sheetHorizontalScroll"
        x: body.x; y: root.height - height
        width: body.width
        orientation: Qt.Horizontal
        active: true
        size: body.width / Math.max(body.width, body.contentWidth)
        position: body.contentX / Math.max(1, body.contentWidth)
        onPositionChanged: if (pressed) body.contentX = position * body.contentWidth
    }
    Controls.TextField {
        id: editor
        objectName: "sheetMetadataEditor"
        z: 5
        visible: !!root.sheet && root.sheet.editorColumn >= 0
        x: root.cornerWidth + (root.sheet ? root.sheet.columnPosition(root.sheet.editorColumn) : 0) - body.contentX + 1
        y: root.sheet ? root.sheet.editorRow * root.rowHeight + 1 : 0
        width: root.sheet ? root.sheet.columnWidth(root.sheet.editorColumn) - 2 : 100
        height: root.rowHeight - 2
        font: root.controlFont
        selectByMouse: true
        onTextEdited: if (root.sheet) root.sheet.setEditorText(text)
        onAccepted: if (root.sheet && root.sheet.commitEditor()) body.forceActiveFocus()
        onActiveFocusChanged: if (!activeFocus && visible && root.sheet && !root.sheet.commitEditor()) Qt.callLater(forceActiveFocus)
        Keys.onEscapePressed: { root.sheet.cancelEditor(); body.forceActiveFocus() }
        background: Rectangle { color: colors.surface; border.color: root.sheet && root.sheet.editorError ? colors.error : colors.accent; border.width: 2 }
        Controls.ToolTip.visible: visible && !!root.sheet && root.sheet.editorError.length > 0
        Controls.ToolTip.text: root.sheet ? root.sheet.editorError : ""
        Accessible.name: root.sheet ? root.sheet.editorLabel : ""
        Accessible.description: root.sheet && root.sheet.editorError ? root.sheet.editorError : "Enter or leaving the field saves. Escape cancels."
    }
    Controls.Menu {
        id: menu
        objectName: "sheetContextMenu"
        property int column: -1
        property var ownerSheet: null
        Controls.MenuItem { objectName: "sheetContextCopy"; text: "Copy"; onTriggered: if (menu.ownerSheet) menu.ownerSheet.copy_selection() }
        Controls.MenuItem { objectName: "sheetContextSetX"; text: "Set as X"; enabled: !!menu.ownerSheet && menu.ownerSheet.xEditable && menu.ownerSheet.columnChoices[menu.column] && menu.ownerSheet.columnChoices[menu.column].numeric; onTriggered: menu.ownerSheet.setX(menu.column) }
        Controls.MenuItem { objectName: "sheetContextLongName"; text: "Edit Long Name…"; onTriggered: if (menu.ownerSheet) menu.ownerSheet.editMetadata(menu.column, "long_name") }
        Controls.MenuItem { objectName: "sheetContextUnits"; text: "Edit Units…"; onTriggered: if (menu.ownerSheet) menu.ownerSheet.editMetadata(menu.column, "units") }
        Controls.MenuSeparator {}
        Repeater {
            model: [{label: "Line", value: "line"}, {label: "Scatter", value: "scatter"}, {label: "Line+Symbol", value: "line_symbol"}, {label: "Heatmap", value: "heatmap"}]
            delegate: Controls.MenuItem {
                required property var modelData
                objectName: "sheetContextPlot_" + modelData.value
                text: modelData.label
                enabled: !!menu.ownerSheet && menu.ownerSheet.plotEnabled && (modelData.value !== "heatmap" || menu.ownerSheet.heatmapEnabled)
                onTriggered: menu.ownerSheet.plot(modelData.value)
            }
        }
        Controls.Menu {
            title: "Add to…"
            Repeater {
                model: menu.ownerSheet ? menu.ownerSheet.targets : []
                delegate: Controls.MenuItem {
                    required property var modelData
                    text: modelData.label
                    contentItem: Text { text: modelData.label; textFormat: Text.PlainText; font: root.controlFont; color: colors.text }
                    onTriggered: if (menu.ownerSheet) menu.ownerSheet.appendTo(modelData.value)
                }
            }
        }
    }
}

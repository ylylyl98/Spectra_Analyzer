import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    property font controlFont: Qt.application.font
    property var boundBridge: null
    property bool panelReady: false
    function bindEditorPanel() {
        if (boundBridge) boundBridge.detachPanel(0, root)
        boundBridge = bridge
        if (boundBridge) boundBridge.bindPanel(0, root)
    }
    Component.onCompleted: { panelReady = true; bindEditorPanel() }
    onBridgeChanged: if (panelReady) bindEditorPanel()
    Component.onDestruction: if (boundBridge) boundBridge.detachPanel(0, root)
    color: colors.surface
    enabled: bridge.enabled
    AppColors { id: colors }
    Connections {
        target: root.bridge
        function onChanged() { curves.currentIndex = root.bridge.currentRow }
        function onRevealCurveRequested(row) {
            curves.currentIndex = row
            curves.positionViewAtIndex(row, ListView.Contain)
            curvesScroll.contentItem.contentY = 0
        }
        function onFocusFieldRequested(field) {
            if (field.indexOf("marker_") === 0) symbols.checked = true
        }
    }
    ScrollView {
        id: curvesScroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: curvesScroll.availableWidth
            spacing: 10
            ColumnLayout {
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                Layout.leftMargin: 10
                Layout.rightMargin: 10
                spacing: 8
                Label { Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text: root.bridge.selectedCount + " selected · Current: " + root.bridge.currentLabel; font: root.controlFont; color: colors.secondary; wrapMode: Text.Wrap; Accessible.name: "Selected curve count and current curve" }
                RowLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                    SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Select all"; font:root.controlFont; onClicked:root.bridge.selectAll(true) }
                    SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Clear"; Accessible.name:"Clear curve selection"; font:root.controlFont; onClicked:root.bridge.selectAll(false) }
                }
                Label { text:"Check to show · Ctrl/Shift to select"; font:root.controlFont; color:colors.secondary; wrapMode:Text.Wrap; Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width }
                ListView {
                    id: curves
                    objectName: "xyCurveList"
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                    Layout.preferredWidth: 0
                    Layout.preferredHeight: Math.max(48, Math.min(252, root.bridge.curveCount * 42))
                    clip: true
                    model: root.bridge.curves
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: curves.contentHeight > curves.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                    Accessible.name: "Comparison curves"
                    keyNavigationEnabled: false
                    Keys.onDeletePressed: root.bridge.action("remove")
                    Keys.onEscapePressed: root.bridge.selectAll(false)
                    Keys.onUpPressed: (event) => { if (count) root.bridge.selectCurve(modelIndex(-1), event.modifiers) }
                    Keys.onDownPressed: (event) => { if (count) root.bridge.selectCurve(modelIndex(1), event.modifiers) }
                    function modelIndex(step) {
                        let i = currentIndex < 0 ? 0 : Math.max(0, Math.min(count - 1, currentIndex + step))
                        currentIndex = i
                        positionViewAtIndex(i, ListView.Contain)
                        return root.bridge.curveIdAt(i)
                    }
                    delegate: Rectangle {
                        id: curveRow
                        required property int index
                        required property string curveId
                        required property string caption
                        required property string curveColor
                        required property bool shown
                        required property bool selected
                        required property bool current
                        required property string sourceDescription
                        readonly property string rowId: curveId
                        width: curves.width
                        height: 42
                        color: selected ? colors.selected : mouse.containsMouse ? colors.hover : colors.surface
                        border.color: current ? colors.accent : colors.divider
                        radius: 4
                        Accessible.name: caption + (current ? ", current curve" : "")
                        Accessible.description: sourceDescription
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 5
                            spacing: 6
                            SurfaceCheckBox { checked:curveRow.shown; Accessible.name:"Show " + curveRow.caption; onClicked:root.bridge.setVisibility(curveRow.curveId, checked) }
                            Rectangle { width:12; height:12; color:curveRow.curveColor; border.color:colors.border }
                            Label { text:curveRow.caption; font:root.controlFont; color:colors.text; elide:Text.ElideRight; Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width }
                            Label { text:"⠿"; font:root.controlFont; color:colors.secondary; Accessible.name:"Drag to reorder" }
                        }
                        MouseArea {
                            id: mouse
                            anchors.fill:parent
                            anchors.leftMargin:38
                            hoverEnabled:true
                            preventStealing:true
                            property real startY:0
                            property bool retainedSelection:false
                            onPressed: (event) => {
                                startY = event.y
                                curves.currentIndex=curveRow.index
                                curves.forceActiveFocus()
                                retainedSelection = curveRow.selected && !(event.modifiers & (Qt.ControlModifier | Qt.ShiftModifier))
                                if (!retainedSelection) root.bridge.selectCurve(curveRow.curveId, event.modifiers)
                            }
                            onReleased: (event) => {
                                if (Math.abs(event.y-startY) > 18) {
                                    const p = mapToItem(curves, event.x, event.y)
                                    root.bridge.moveTo(Math.max(0, Math.min(curves.count, Math.floor((p.y+curves.contentY)/42) + (event.y > startY ? 1 : 0))))
                                } else if (retainedSelection) root.bridge.selectCurve(curveRow.curveId, event.modifiers)
                            }
                            ToolTip.visible: containsMouse
                            ToolTip.text:curveRow.sourceDescription
                            ToolTip.delay:800
                        }
                    }
                }
                SurfaceCheckBox { Layout.fillWidth:true; Layout.minimumWidth:0; Layout.maximumWidth:parent.width; text:"Highlight selection in preview"; font:root.controlFont; checked:root.bridge.highlight; onClicked:root.bridge.setHighlight(checked) }
                SurfaceButton { id: batch; objectName:"xyBatchDisclosure"; Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:(checked ? "▾ " : "▸ ") + "Selected curves · " + root.bridge.selectedCount; font:root.controlFont; checkable:true; onClicked:root.bridge.suspendBlur() }
                ColumnLayout {
                    visible:batch.checked
                    Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                    spacing:8
                    RowLayout {
                        Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                        SurfaceButton { text:"Color…"; font:root.controlFont; Layout.fillWidth:true; Layout.minimumWidth: 0; enabled:root.bridge.selectedCount > 0; onClicked:root.bridge.action("batchColor") }
                        SurfaceButton { text:"Width…"; font:root.controlFont; Layout.fillWidth:true; Layout.minimumWidth: 0; enabled:root.bridge.selectedCount > 0; onClicked:root.bridge.action("batchWidth") }
                    }
                    XYInspectorChoice { bridge: root.bridge; label:"Visibility"; field:"batchVisibility"; controlFont:root.controlFont; value:""; choices:[{key:"show",label:"Show selected"},{key:"hide",label:"Hide selected"},{key:"only",label:"Show only selected"},{key:"all",label:"Show all"}]; onChosen:(key)=>root.bridge.visibilityCommand(key) }
                    XYInspectorChoice { bridge: root.bridge; label:"Plot style · selected curves"; field:"batchStyle"; controlFont:root.controlFont; value:""; choices:[{key:"line",label:"Line"},{key:"scatter",label:"Scatter"},{key:"line_symbol",label:"Line + Symbol"}]; onChosen:(key)=>root.bridge.applyStyle(key) }
                    XYInspectorChoice { bridge: root.bridge; label:"Color list · selected curves"; field:"palette"; controlFont:root.controlFont; value:root.bridge.paletteKey; choices:root.bridge.palettes; onChosen:(key)=>root.bridge.setPalette(key) }
                    RowLayout {
                        Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                        SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Apply colors"; font:root.controlFont; enabled:root.bridge.selectedCount > 0; onClicked:root.bridge.action("palette") }
                        SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Import…"; font:root.controlFont; onClicked:root.bridge.action("importPalette") }
                    }
                    Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Apply assigns colors in graph order. Short lists repeat."; wrapMode:Text.Wrap; color:colors.secondary; font:root.controlFont }
                    RowLayout {
                        Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                        SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Move up"; font:root.controlFont; enabled:root.bridge.selectedCount > 0; onClicked:root.bridge.moveSelected(-1) }
                        SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Move down"; font:root.controlFont; enabled:root.bridge.selectedCount > 0; onClicked:root.bridge.moveSelected(1) }
                    }
                }
                Label { text:"Current curve"; font:Qt.font({family:root.controlFont.family, pointSize:root.controlFont.pointSize, bold:true}); color:colors.text }
                Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Text and numbers commit on Enter or when leaving a field. Esc reverts pending edits."; wrapMode:Text.Wrap; color:colors.secondary; font:root.controlFont }
                ColumnLayout {
                    Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                    enabled:root.bridge.hasCurrent
                    XYInspectorField { bridge: root.bridge; label:"Legend text"; field:"label"; controlFont:root.controlFont }
                    XYInspectorField { bridge: root.bridge; label:"Color · #RRGGBB"; field:"color"; controlFont:root.controlFont }
                    SurfaceButton { text:"Choose color…"; font:root.controlFont; onClicked:root.bridge.action("color") }
                    XYInspectorField { bridge: root.bridge; label:"Line width"; field:"width"; numeric:true; controlFont:root.controlFont }
                    XYInspectorField { bridge: root.bridge; label:"Extra Y offset"; field:"y_offset"; numeric:true; controlFont:root.controlFont }
                }
                SurfaceButton { id:symbols; objectName:"xySymbolsDisclosure"; Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:(checked ? "▾ " : "▸ ") + "Symbols"; font:root.controlFont; checkable:true; onClicked:root.bridge.suspendBlur() }
                ColumnLayout {
                    Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                    visible:symbols.checked
                    XYInspectorChoice { bridge: root.bridge; label:"Apply symbols to"; field:"symbolTarget"; value:root.bridge.symbolTarget; choices:[{key:"current",label:"Current curve"},{key:"selected",label:"Selected curves"}]; controlFont:root.controlFont; onChosen:(key)=>root.bridge.setSymbolTarget(key) }
                    XYInspectorChoice { bridge: root.bridge; label:"Shape"; field:"marker_shape"; choices:root.bridge.shapes; controlFont:root.controlFont; onChosen:(key)=>root.bridge.setDraft("marker_shape",key) }
                    XYInspectorChoice { bridge: root.bridge; label:"Fill"; field:"marker_fill"; choices:root.bridge.fills; controlFont:root.controlFont; onChosen:(key)=>root.bridge.setDraft("marker_fill",key) }
                    XYInspectorField { bridge: root.bridge; label:"Symbol size"; field:"marker_size"; numeric:true; controlFont:root.controlFont }
                    XYInspectorField { bridge: root.bridge; label:"Outline · auto or #RRGGBB"; field:"marker_edge_color"; controlFont:root.controlFont }
                    XYInspectorField { bridge: root.bridge; label:"Outline width"; field:"marker_edge_width"; numeric:true; controlFont:root.controlFont }
                    SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Apply symbols"; font:root.controlFont; enabled:root.bridge.symbolTarget === "current" ? root.bridge.hasCurrent : root.bridge.selectedCount > 0; onClicked:root.bridge.action("symbols") }
                    Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Use Apply symbols to commit these settings. Save, export and curve changes also finish pending settings. Symbols show with Scatter or Line + Symbol."; wrapMode:Text.Wrap; color:colors.secondary; font:root.controlFont }
                }
                SurfaceButton { id:source; Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:(checked ? "▾ " : "▸ ") + "Source details"; font:root.controlFont; checkable:true; onClicked:root.bridge.suspendBlur() }
                Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; visible:source.checked; text:root.bridge.source; wrapMode:Text.WrapAnywhere; color:colors.secondary; font:root.controlFont }
                SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Open source Sheet"; font:root.controlFont; enabled:root.bridge.hasCurrent; onClicked:root.bridge.action("source") }
                SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Remove current curve"; font:root.controlFont; enabled:root.bridge.hasCurrent; onClicked:root.bridge.action("remove") }
                XYInspectorFeedback { bridge: root.bridge; controlFont:root.controlFont }
                Item { height:6 }
            }
        }
    }
}

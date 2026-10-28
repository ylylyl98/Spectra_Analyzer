import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: root
    required property var bridge
    property font controlFont: Qt.application.font
    property bool showInterval: false
    property var boundBridge: null
    property bool panelReady: false
    function bindEditorPanel() {
        if (boundBridge) boundBridge.detachPanel(2, root)
        boundBridge = bridge
        if (boundBridge) boundBridge.bindPanel(2, root)
    }
    Component.onCompleted: { panelReady = true; bindEditorPanel() }
    onBridgeChanged: if (panelReady) bindEditorPanel()
    Component.onDestruction: if (boundBridge) boundBridge.detachPanel(2, root)
    color: colors.surface
    enabled: bridge.enabled
    AppColors { id: colors }
    Connections {
        target: root.bridge
        function onFocusFieldRequested(field) {
            if (["width_mm", "height_mm", "numeric_pt", "text_pt", "raster_dpi"].indexOf(field) >= 0) format.checked = true
        }
    }
    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: scroll.availableWidth
            spacing:10
            ColumnLayout {
                Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                Layout.margins:10
                spacing:8
                Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Graph settings · Enter or leave a field to commit. Section changes retain drafts."; wrapMode:Text.Wrap; font:root.controlFont; color:colors.secondary }
                XYInspectorField { bridge: root.bridge; label:"Graph name"; field:"name"; controlFont:root.controlFont }
                XYInspectorField { bridge: root.bridge; label:"Plot title"; field:"plot_title"; controlFont:root.controlFont }
                XYInspectorField { bridge: root.bridge; label:"X axis"; field:"x_label"; controlFont:root.controlFont }
                XYInspectorField { bridge: root.bridge; label:"Y axis"; field:"y_label"; controlFont:root.controlFont }
                SurfaceCheckBox { Layout.fillWidth:true; Layout.minimumWidth:0; Layout.maximumWidth:parent.width; text:"Show legend"; font:root.controlFont; checked:root.bridge.values.legend_visible; onClicked:{root.bridge.setDraft("legend_visible",checked);root.bridge.commitField("legend_visible")} }
                Label { text:"Comparison behavior"; color:colors.text; font:Qt.font({family:root.controlFont.family,pointSize:root.controlFont.pointSize,bold:true}) }
                XYInspectorChoice { bridge: root.bridge; label:"Normalization"; field:"normalization"; controlFont:root.controlFont; choices:[{key:"none",label:"None (original Y)"},{key:"maxabs",label:"Maximum absolute Y"},{key:"minmax",label:"Min–max (0–1)"}]; onChosen:(key)=>{root.bridge.setDraft("normalization",key);root.bridge.commitField("normalization")} }
                XYInspectorChoice { bridge: root.bridge; id:interval; label:"Data X interval · also used for analysis"; field:"x_range_mode"; controlFont:root.controlFont; choices:[{key:"all",label:"All X"},{key:"common",label:"Common overlap"},{key:"manual",label:"Manual interval"}]; onChosen:(key)=>root.bridge.setRangeMode(key) }
                Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Selects data for display, normalization and analysis. Toolbar zoom changes the view."; wrapMode:Text.Wrap; font:root.controlFont; color:colors.secondary }
                XYInspectorField { bridge: root.bridge; label:"X minimum"; field:"x_min"; numeric:true; visible:root.bridge.values.x_range_mode === "manual"; controlFont:root.controlFont }
                XYInspectorField { bridge: root.bridge; label:"X maximum"; field:"x_max"; numeric:true; visible:root.bridge.values.x_range_mode === "manual"; controlFont:root.controlFont }
                XYInspectorField { bridge: root.bridge; label:"Stack step · signed Y spacing"; field:"stack_step"; numeric:true; controlFont:root.controlFont }
                Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Stack order includes hidden curves. Individual offsets are in Curves."; wrapMode:Text.Wrap; font:root.controlFont; color:colors.secondary }
                SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:"Reset comparison display"; font:root.controlFont; onClicked:root.bridge.action("reset") }
                Label { Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:root.bridge.displayStatus; wrapMode:Text.Wrap; font:root.controlFont; color:colors.secondary; Accessible.name:"Comparison display status" }
                SurfaceButton { id:format; objectName:"xyFormatDisclosure"; Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; text:(checked ? "▾ " : "▸ ") + "Advanced figure and export format"; font:root.controlFont; checkable:true; onClicked:root.bridge.suspendBlur() }
                ColumnLayout {
                    Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                    visible:format.checked
                    SurfaceCheckBox { Layout.fillWidth:true; Layout.minimumWidth:0; Layout.maximumWidth:parent.width; text:"Use physical figure format"; font:root.controlFont; checked:root.bridge.values.physical; onClicked:{root.bridge.setDraft("physical",checked);root.bridge.commitField("physical")} }
                    ColumnLayout {
                        Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                        enabled:root.bridge.values.physical
                        XYInspectorField { bridge: root.bridge; label:"Width (mm)"; field:"width_mm"; numeric:true; controlFont:root.controlFont }
                        XYInspectorField { bridge: root.bridge; label:"Height (mm)"; field:"height_mm"; numeric:true; controlFont:root.controlFont }
                        XYInspectorField { bridge: root.bridge; label:"Tick and legend size (pt)"; field:"numeric_pt"; numeric:true; controlFont:root.controlFont }
                        XYInspectorField { bridge: root.bridge; label:"Axis and title size (pt)"; field:"text_pt"; numeric:true; controlFont:root.controlFont }
                        XYInspectorField { bridge: root.bridge; label:"PNG resolution (DPI)"; field:"raster_dpi"; numeric:true; controlFont:root.controlFont }
                    }
                }
                RowLayout {
                    Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
                    SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Export SVG…"; font:root.controlFont; onClicked:root.bridge.action("svg") }
                    SurfaceButton { Layout.fillWidth:true; Layout.minimumWidth: 0; text:"Export PNG…"; font:root.controlFont; onClicked:root.bridge.action("png") }
                }
                XYInspectorFeedback { bridge: root.bridge; controlFont:root.controlFont }
                Item { height:6 }
            }
        }
    }
    onShowIntervalChanged: if (showInterval) {
        Qt.callLater(function() {
            scroll.contentItem.contentY = Math.max(0, Math.min(interval.mapToItem(scroll.contentItem.contentItem, 0, 0).y-8, scroll.contentItem.contentHeight-scroll.height))
            interval.focusChoice()
        })
        showInterval = false
    }
}

import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "analysisStyle"
    required property var bridge
    required property var dialogHost
    property font controlFont: Qt.application.font
    readonly property var state: bridge.state
    property var editors: ({})
    width: 620; height: 670
    color: colors.surface
    focus: true
    Accessible.name: "Analysis Style"
    AppColors { id: colors }
    function title(layer) { return layer.replace("detected", "Detected").replace("fitted", "Fitted").replace("-", " ") }
    function draft(layer, field) { const styles = state.styles || ({}); return styles[layer] ? String(styles[layer][field]) : "" }
    component Note: Text { Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.Wrap; font: root.controlFont; color: colors.secondary; textFormat: Text.PlainText }
    component Field: ColumnLayout {
        id: field
        required property string layerId
        required property string fieldKey
        required property string label
        Layout.fillWidth: true; Layout.minimumWidth: 0
        spacing: 3
        Note { text: field.label }
        Controls.TextField {
            id: editor
            property string editOwner: ""
            objectName: "style_" + field.layerId + "_" + field.fieldKey
            Layout.fillWidth: true; Layout.minimumWidth: 0
            implicitHeight: Math.max(34, contentHeight + 12)
            font: root.controlFont; color: enabled ? colors.text : colors.disabledText
            selectByMouse: true; leftPadding: 8; rightPadding: 8
            selectionColor: colors.accent; selectedTextColor: colors.surface
            Accessible.name: root.title(field.layerId) + " " + field.label
            onActiveFocusChanged: if (activeFocus) editOwner = root.bridge.ownerToken
            onTextEdited: root.bridge.draftStyle(field.layerId, field.fieldKey, text, editOwner)
            Binding on text { value: root.draft(field.layerId, field.fieldKey) }
            background: Rectangle { radius: 4; color: editor.enabled ? colors.surface : colors.disabled; border.width: editor.activeFocus ? 2 : 1; border.color: editor.activeFocus ? colors.accent : colors.border }
            Component.onCompleted: root.editors[field.layerId + ":" + field.fieldKey] = editor
        }
    }
    Connections {
        target: root.bridge
        function onFocusRequested(layer, field) {
            pending.key = layer + ":" + field
            pending.restart()
        }
    }
    Timer {
        id: pending
        property string key: ""
        interval: 20
        onTriggered: {
            const editor = root.editors[key]
            if (!editor || !root.bridge.enabled) return
            editor.forceActiveFocus(Qt.OtherFocusReason)
            scroll.contentItem.contentY = Math.max(0, Math.min(scroll.contentItem.contentHeight - scroll.availableHeight,
                                                            editor.mapToItem(body, 0, 0).y - 35))
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 10
        Note { text: "Edit the four marker layers. Apply saves all layers together; Cancel discards these changes." }
        Controls.ScrollView {
            id: scroll
            objectName: "styleScroll"
            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                id: body
                width: scroll.availableWidth; spacing: 14
                enabled: root.bridge.enabled
                Repeater {
                    // The constant IDs keep each live editor intact while draft text changes.
                    model: root.bridge.layerIds
                    delegate: ColumnLayout {
                        id: layerRow
                        required property string modelData
                        Layout.fillWidth: true; Layout.minimumWidth: 0
                        spacing: 6
                        Note { text: root.title(layerRow.modelData); color: colors.text; font.bold: true }
                        GridLayout {
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            columns: 2; columnSpacing: 12; rowSpacing: 8
                            Field {
                                layerId: layerRow.modelData; fieldKey: "color"; label: "Color"
                                Layout.preferredWidth: Math.max(1, (scroll.availableWidth - 12) / 2)
                                Layout.maximumWidth: Layout.preferredWidth
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                Layout.preferredWidth: Math.max(1, (scroll.availableWidth - 12) / 2)
                                Layout.maximumWidth: Layout.preferredWidth
                                spacing: 3
                                Note { text: "Marker" }
                                AnalysisAction {
                                    id: markerChoice
                                    bridge: root.bridge
                                    objectName: "style_" + layerRow.modelData + "_marker"
                                    text: root.draft(layerRow.modelData, "marker") + " ▾"
                                    font: root.controlFont
                                    Accessible.name: root.title(layerRow.modelData) + " marker"
                                    Accessible.description: "Current marker: " + root.draft(layerRow.modelData, "marker")
                                    onTriggered: function(owner) { markerMenu.actionOwner = owner; markerMenu.open() }
                                    Component.onCompleted: root.editors[layerRow.modelData + ":marker"] = markerChoice
                                    Controls.Menu {
                                        id: markerMenu
                                        property string actionOwner: ""
                                        popupType: Controls.Popup.Window
                                        y: markerChoice.height + 3
                                        width: Math.max(160, markerChoice.width)
                                        font: root.controlFont
                                        background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                                        Repeater {
                                            model: [{label: "Circle · o", value: "o"}, {label: "Square · s", value: "s"}, {label: "Up · ^", value: "^"}, {label: "Down · v", value: "v"}, {label: "Diamond · D", value: "D"}, {label: "Plus · +", value: "+"}, {label: "Cross · x", value: "x"}, {label: "Star · *", value: "*"}, {label: "Point · .", value: "."}]
                                            delegate: Controls.MenuItem {
                                                required property var modelData
                                                text: modelData.label
                                                font: root.controlFont
                                                Accessible.name: text
                                                onTriggered: if (markerMenu.actionOwner === root.bridge.ownerToken)
                                                                 root.bridge.draftStyle(layerRow.modelData, "marker", modelData.value, markerMenu.actionOwner)
                                                contentItem: Text { text: parent.text; font: root.controlFont; color: colors.text; verticalAlignment: Text.AlignVCenter }
                                                background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
                                            }
                                        }
                                    }
                                    Connections {
                                        target: root.bridge
                                        function onStateChanged() { if (!root.bridge.enabled || markerMenu.actionOwner !== root.bridge.ownerToken) markerMenu.close() }
                                    }
                                }
                            }
                            Field {
                                layerId: layerRow.modelData; fieldKey: "size"; label: "Size (0.1–100)"
                                Layout.preferredWidth: Math.max(1, (scroll.availableWidth - 12) / 2)
                                Layout.maximumWidth: Layout.preferredWidth
                            }
                            SurfaceCheckBox {
                                property string actionOwner: ""
                                objectName: "style_" + layerRow.modelData + "_filled"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                text: "Filled"; font: root.controlFont
                                Accessible.name: root.title(layerRow.modelData) + " filled marker"
                                onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
                                onPressed: actionOwner = root.bridge.ownerToken
                                onClicked: root.bridge.draftStyle(layerRow.modelData, "filled", String(checked), actionOwner)
                                Binding on checked { value: root.draft(layerRow.modelData, "filled") === "true" }
                                Component.onCompleted: root.editors[layerRow.modelData + ":filled"] = this
                            }
                        }
                    }
                }
            }
        }
        Note { objectName: "styleError"; visible: !!root.bridge.errorText; text: root.bridge.errorText; color: colors.error; Accessible.name: "Style validation error" }
        RowLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 8
            AnalysisAction { bridge: root.bridge; objectName: "styleApply"; text: "Apply"; primary: true; font: root.controlFont; enabled: root.bridge.enabled; onTriggered: function(owner) { if (bridge.applyStyles(owner)) root.dialogHost.reject() } }
            AnalysisAction { bridge: root.bridge; objectName: "styleCancel"; text: "Cancel"; font: root.controlFont; onTriggered: root.dialogHost.reject() }
        }
    }
}

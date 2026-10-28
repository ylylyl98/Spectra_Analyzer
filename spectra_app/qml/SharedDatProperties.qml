import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "sharedDatProperties"
    property font controlFont: Qt.application.font
    required property var bridge
    readonly property var state: bridge.state
    property var editors: ({})
    property var disclosureHeaders: []
    property bool suppressFieldFinish: false
    property string projectedOwner: ""
    implicitWidth: 300
    implicitHeight: content.implicitHeight + 20
    color: colors.surface
    signal revealY(real y)
    Accessible.name: "DAT properties"
    AppColors { id: colors }
    Component.onCompleted: projectedOwner = bridge.ownerToken

    function value(field) { return state[field] === undefined ? "" : state[field] }
    function registerEditor(field, item) { editors[field] = item }
    function disclosureHasFocus() {
        for (let i = 0; i < disclosureHeaders.length; ++i)
            if (disclosureHeaders[i].activeFocus) return true
        return false
    }
    function expanded(section, fallback) {
        const saved = state.disclosures || ({})
        return saved[section] === undefined ? fallback : !!saved[section]
    }
    function revealField(section, field, takeFocus) {
        bridge.setDisclosure(section, true, bridge.ownerToken)
        if (field === "axis_override") bridge.setDisclosure("source_axis", true, bridge.ownerToken)
        if (["fix_annotations", "title_preset", "title_pattern", "title_angle_map", "title_preset_name", "insert_title_field"].indexOf(field) >= 0)
            bridge.setDisclosure("templates", true, bridge.ownerToken)
        pendingFocus.field = field
        pendingFocus.owner = bridge.ownerToken
        pendingFocus.takeFocus = takeFocus
        pendingFocus.restart()
    }
    Timer {
        id: pendingFocus
        property string field
        property string owner
        property bool takeFocus: true
        interval: 20
        onTriggered: {
            if (owner !== root.bridge.ownerToken) return
            const item = root.editors[field]
            if (!item || !item.visible) return
            if (takeFocus) {
                item.forceActiveFocus(Qt.OtherFocusReason)
                if (typeof item.selectAll === "function") item.selectAll()
            }
            root.revealY(item.mapToItem(root, 0, item.height / 2).y)
        }
    }
    Connections {
        target: root.bridge
        function onFocusRequested(section, field) { root.revealField(section, field, true) }
        function onRevealRequested(section, field) { root.revealField(section, field, false) }
        function onStateChanged() {
            if (root.projectedOwner !== root.bridge.ownerToken) {
                // Retire an old editor's focus before accepting input for the next graph.
                root.suppressFieldFinish = true
                for (const field in root.editors) {
                    const editor = root.editors[field]
                    if (editor.popup) editor.popup.close()
                }
                root.forceActiveFocus(Qt.OtherFocusReason)
                root.projectedOwner = root.bridge.ownerToken
                Qt.callLater(function() { root.suppressFieldFinish = false })
            }
        }
    }
    component Note: Text {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: colors.secondary
        font: root.controlFont
        lineHeight: 1.12
    }
    component Section: ColumnLayout {
        id: section
        property string sectionKey
        property string title
        property bool initiallyExpanded: true
        readonly property bool expanded: root.expanded(sectionKey, initiallyExpanded)
        property bool advanced: false
        objectName: "shared_" + sectionKey + "_section"
        default property alias contents: body.data
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 7
        Controls.Button {
            id: header
            property string actionOwner: ""
            objectName: "shared_" + section.sectionKey + "_toggle"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.max(34, contentItem.implicitHeight + 14)
            padding: 7
            font: root.controlFont
            hoverEnabled: true
            focusPolicy: Qt.TabFocus
            Accessible.name: section.title
            Accessible.description: section.expanded ? "Expanded. Press to collapse." : "Collapsed. Press to expand."
            Component.onCompleted: root.disclosureHeaders.push(header)
            onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
            onPressed: { actionOwner = root.bridge.ownerToken; root.suppressFieldFinish = true }
            onReleased: Qt.callLater(function() { root.suppressFieldFinish = false })
            onClicked: {
                root.suppressFieldFinish = true
                forceActiveFocus(Qt.MouseFocusReason)
                root.bridge.setDisclosure(section.sectionKey, !section.expanded, actionOwner)
                Qt.callLater(function() { root.suppressFieldFinish = false })
            }
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) {
                    root.bridge.setDisclosure(section.sectionKey, event.key === Qt.Key_Right, actionOwner)
                    event.accepted = true
                } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
                    if (!event.isAutoRepeat) header.clicked()
                    event.accepted = true
                }
            }
            Keys.onReleased: function(event) {
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space)
                    event.accepted = true
            }
            contentItem: RowLayout {
                Text { text: section.expanded ? "▾" : "▸"; font: root.controlFont; color: colors.secondary; textFormat: Text.PlainText }
                Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: section.title; font: root.controlFont; color: colors.text; textFormat: Text.PlainText; wrapMode: Text.Wrap }
            }
            background: Rectangle {
                radius: 4
                color: header.down ? colors.pressed : header.hovered ? colors.hover : section.advanced ? colors.surface : colors.disabled
                border.width: header.visualFocus ? 2 : 1
                border.color: header.visualFocus ? colors.accent : section.advanced ? colors.border : colors.divider
            }
        }
        ColumnLayout {
            id: body
            visible: section.expanded
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.leftMargin: 4
            Layout.rightMargin: 4
            spacing: 8
        }
    }
    component Toggle: SurfaceCheckBox {
        id: toggle
        property string field
        property string actionOwner: ""
        objectName: field
        Layout.minimumWidth: 0
        font: root.controlFont
        implicitHeight: Math.max(28, contentItem.implicitHeight + 6)
        onPressed: actionOwner = root.bridge.ownerToken
        onClicked: root.bridge.setFlag(field, checked, actionOwner)
        Binding on checked { value: !!root.value(toggle.field) }
        Component.onCompleted: root.registerEditor(field, toggle)
        Controls.ToolTip.visible: hovered && Accessible.description.length > 0
        Controls.ToolTip.text: Accessible.description
        Controls.ToolTip.delay: 650
    }
    component Field: ColumnLayout {
        id: fieldControl
        property string field
        property string label
        property string hint: ""
        property string placeholder: ""
        property string lockField: ""
        property string kind: "number"
        property bool editorEnabled: true
        readonly property bool invalid: root.bridge.errorField === field && !!root.bridge.errorText
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.preferredWidth: 1
        Layout.alignment: Qt.AlignTop
        spacing: 3
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Note { text: fieldControl.label; color: fieldControl.enabled ? colors.text : colors.disabledText }
            Toggle {
                visible: !!fieldControl.lockField
                field: fieldControl.lockField
                text: "Fix"
                Accessible.name: "Fix " + fieldControl.label
                Accessible.description: "Keep this endpoint when automatically scaling or opening another DAT."
            }
        }
        Controls.TextField {
            id: editor
            property string editOwner: ""
            objectName: fieldControl.field
            enabled: fieldControl.editorEnabled
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.max(32, contentHeight + topPadding + bottomPadding)
            font: root.controlFont
            leftPadding: 8; rightPadding: 8; topPadding: 6; bottomPadding: 6
            color: enabled ? colors.text : colors.disabledText
            selectionColor: colors.accent
            selectedTextColor: colors.surface
            selectByMouse: true
            hoverEnabled: true
            inputMethodHints: fieldControl.kind === "number" ? Qt.ImhFormattedNumbersOnly : Qt.ImhNone
            placeholderText: fieldControl.placeholder
            Accessible.name: fieldControl.label
            Accessible.description: fieldControl.hint
            onActiveFocusChanged: if (activeFocus) editOwner = root.bridge.ownerToken
            onTextEdited: {
                if (fieldControl.kind === "number") root.bridge.draftNumber(fieldControl.field, text, editOwner)
                else if (fieldControl.kind === "template") root.bridge.setTemplate(fieldControl.field, text, editOwner)
                else root.bridge.setLabel(fieldControl.field, text, editOwner)
            }
            function finish() {
                if (fieldControl.kind === "number") root.bridge.commitNumber(fieldControl.field, editOwner)
                else if (fieldControl.kind === "label") root.bridge.finishLabels(editOwner)
            }
            onAccepted: finish()
            onEditingFinished: {
                if (!root.suppressFieldFinish && !root.disclosureHasFocus()) finish()
            }
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                    finish()
                    event.accepted = true
                } else if (fieldControl.kind === "number" && (event.key === Qt.Key_Up || event.key === Qt.Key_Down)) {
                    root.bridge.stepNumber(fieldControl.field, event.key === Qt.Key_Up ? 1 : -1, editOwner)
                    event.accepted = true
                }
            }
            Binding on text { value: String(root.value(fieldControl.field)) }
            Component.onCompleted: root.registerEditor(fieldControl.field, editor)
            background: Rectangle {
                radius: 4
                color: !editor.enabled ? colors.disabled : fieldControl.invalid ? colors.errorSurface : colors.surface
                border.width: editor.activeFocus ? 2 : 1
                border.color: fieldControl.invalid ? colors.error : editor.activeFocus ? colors.accent : colors.border
            }
            Controls.ToolTip.visible: hovered && fieldControl.hint.length > 0
            Controls.ToolTip.text: fieldControl.hint
            Controls.ToolTip.delay: 650
        }
        Note { objectName: fieldControl.field + "_error"; visible: fieldControl.invalid; text: visible ? root.bridge.errorText : ""; color: colors.error; Accessible.name: fieldControl.label + " validation error" }
    }
    component Choice: ColumnLayout {
        id: choiceControl
        property string field
        property string label
        property var choices: []
        property string hint: ""
        property string selected: String(root.value(field))
        property string selectionOwner: ""
        signal chosen(string value, string owner)
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 3
        Note { text: choiceControl.label; color: choiceControl.enabled ? colors.text : colors.disabledText }
        Controls.ComboBox {
            id: choice
            objectName: choiceControl.field
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
            model: choiceControl.choices
            textRole: "label"
            valueRole: "value"
            font: root.controlFont
            leftPadding: 8; rightPadding: 26
            hoverEnabled: true
            Accessible.name: choiceControl.label
            Accessible.description: choiceControl.hint
            Controls.ToolTip.visible: hovered && choiceControl.hint.length > 0
            Controls.ToolTip.text: choiceControl.hint
            Controls.ToolTip.delay: 650
            onActiveFocusChanged: if (activeFocus) choiceControl.selectionOwner = root.bridge.ownerToken
            onPressedChanged: if (pressed) choiceControl.selectionOwner = root.bridge.ownerToken
            onActivated: if (choiceControl.selectionOwner === root.bridge.ownerToken) choiceControl.chosen(currentValue, choiceControl.selectionOwner)
            Connections {
                target: choice.popup
                function onAboutToShow() { choiceControl.selectionOwner = root.bridge.ownerToken }
            }
            Binding on currentIndex {
                value: {
                    for (let i = 0; i < choiceControl.choices.length; ++i)
                        if (String(choiceControl.choices[i].value) === choiceControl.selected) return i
                    return -1
                }
            }
            Component.onCompleted: {
                root.registerEditor(choiceControl.field, choice)
                popup.popupType = Controls.Popup.Window
                popup.y = Qt.binding(function() { return choice.height + 3 })
                popup.height = Qt.binding(function() {
                    return Math.min(popup.contentItem.implicitHeight, Math.max(100, choice.Screen.desktopAvailableHeight - popup.topMargin - popup.bottomMargin))
                })
            }
            contentItem: RowLayout {
                spacing: 8
                Image {
                    objectName: choiceControl.field + "_preview_selected"
                    source: choice.currentIndex >= 0 ? (choiceControl.choices[choice.currentIndex].preview || "") : ""
                    visible: source.toString().length > 0
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 14
                    fillMode: Image.Stretch
                }
                Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: choice.displayText; textFormat: Text.PlainText; font: root.controlFont; color: choice.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
            }
            indicator: Text { x: choice.width - width - 8; y: (choice.height - height) / 2; text: "⌄"; textFormat: Text.PlainText; font: root.controlFont; color: colors.secondary }
            background: Rectangle { radius: 4; color: !choice.enabled ? colors.disabled : choice.down ? colors.pressed : choice.hovered ? colors.hover : colors.surface; border.width: choice.visualFocus ? 2 : 1; border.color: choice.visualFocus ? colors.accent : colors.border }
            delegate: Controls.ItemDelegate {
                required property var modelData
                required property int index
                width: choice.width
                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                highlighted: choice.highlightedIndex === index
                font: root.controlFont
                contentItem: RowLayout {
                    spacing: 8
                    Image {
                        objectName: choiceControl.field + "_preview_" + index
                        source: modelData.preview || ""
                        visible: source.toString().length > 0
                        Layout.preferredWidth: 64
                        Layout.preferredHeight: 14
                        fillMode: Image.Stretch
                    }
                    Text { Layout.fillWidth: true; Layout.minimumWidth: 0; text: modelData.label; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap; verticalAlignment: Text.AlignVCenter }
                }
                background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
            }
        }
    }
    component Action: SurfaceButton {
        property string actionKey
        property string actionOwner: ""
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        objectName: "shared_" + actionKey
        font: root.controlFont
        implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
        onPressed: actionOwner = root.bridge.ownerToken
        onClicked: root.bridge.command(actionKey, actionOwner)
    }

    ColumnLayout {
        id: content
        x: 10; y: 10
        width: Math.max(1, root.width - 20)
        spacing: 10
        enabled: root.bridge.enabled
        Note { visible: !!root.bridge.errorText && !root.bridge.errorField; text: visible ? root.bridge.errorText : ""; color: colors.error }
        Section {
            sectionKey: "colors"
            title: "Color Scale"
            Choice { field: "colormap"; label: "Color map"; choices: root.state.colormaps || []; onChosen: function(value, owner) { root.bridge.setChoice(field, value, owner) } }
            Choice { field: "mapping"; label: "Mapping"; choices: root.state.mappings || []; onChosen: function(value, owner) { root.bridge.setChoice(field, value, owner) } }
            Field { field: "linthresh"; label: "Linear threshold"; visible: !!root.state.linthresh_enabled; hint: "SymLog transition from linear to logarithmic scaling. Press Enter to apply." }
            Note { visible: !!root.state.mapping_status; text: root.state.mapping_status || "" }
            Choice { field: "color_scale"; label: "Scale regions"; choices: root.state.color_scales || []; onChosen: function(value, owner) { root.bridge.setChoice(field, value, owner) } }
            Toggle { field: "fix_regions"; text: "Fix region count across DATs"; Accessible.description: "Carry the global, two-region or three-region choice to new files." }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8
                Field { field: "vmin"; label: "Minimum"; lockField: "vmin_lock"; editorEnabled: !root.state.regional; hint: "Enter a signal value and press Enter. Editing a color endpoint also fixes it." }
                Field { field: "vmax"; label: "Maximum"; lockField: "vmax_lock"; editorEnabled: !root.state.regional; hint: "Enter a signal value and press Enter. Editing a color endpoint also fixes it." }
            }
            Note { visible: !!root.state.regional; text: "Each region has its own scale. Minimum / maximum Fix applies to every bar." }
            ColumnLayout {
                visible: !!root.state.regional
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 8
                GridLayout {
                    Layout.fillWidth: true
                    columns: root.state.edge_count > 1 ? 2 : 1
                    columnSpacing: 8
                    Field { field: "edge0"; label: "Boundary 1 (" + (root.state.edge_unit || "source X") + ")"; visible: root.state.edge_count > 0; hint: "Snaps to an internal source-axis cell edge." }
                    Field { field: "edge1"; label: "Boundary 2 (" + (root.state.edge_unit || "source X") + ")"; visible: root.state.edge_count > 1; hint: "Snaps to an internal source-axis cell edge." }
                }
                Toggle { field: "fix_region_edges"; text: "Fix boundaries across DATs"; Accessible.description: "Reuse compatible boundaries independently of the region count." }
            }
            Action { actionKey: "auto_colors"; text: root.state.regional ? "Recalculate regional scales" : "Auto color scale" }
            Note { objectName: "shared_color_range_status"; visible: !!root.state.color_range_status; text: root.state.color_range_status || "" }
            Note { visible: !!root.state.color_clipping_status; text: root.state.color_clipping_status || "" }
        }
        Section {
            sectionKey: "ranges"
            title: "View Range"
            initiallyExpanded: false
            Note { text: "X · " + (root.state.x_label || "X axis"); color: colors.text }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8
                Field { field: "xmin"; label: "Minimum"; lockField: "xmin_lock"; hint: "X minimum in the axis units shown above. Press Enter to apply." }
                Field { field: "xmax"; label: "Maximum"; lockField: "xmax_lock"; hint: "X maximum in the axis units shown above. Press Enter to apply." }
            }
            Note { text: "Y · " + (root.state.y_label || "Y axis"); color: colors.text }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8
                Field { field: "ymin"; label: "Minimum"; lockField: "ymin_lock"; hint: "Y minimum in the axis units shown above. Press Enter to apply." }
                Field { field: "ymax"; label: "Maximum"; lockField: "ymax_lock"; hint: "Y maximum in the axis units shown above. Press Enter to apply." }
            }
            Note { text: "Fix keeps each endpoint during Auto view and across DATs." }
            RowLayout {
                Layout.fillWidth: true
                Action { actionKey: "apply_view"; text: "Apply view"; primary: true }
                Action { actionKey: "auto_view"; text: "Auto view" }
            }
            Section {
                sectionKey: "source_axis"
                title: "Source X-axis interpretation"
                advanced: true
                initiallyExpanded: false
                Choice { field: "axis_override"; label: "Source units"; choices: root.state.axis_overrides || []; enabled: !!root.state.source_axis_enabled; hint: root.state.source_axis_tooltip || ""; onChosen: function(value, owner) { root.bridge.setChoice(field, value, owner) } }
                Note { objectName: "shared_source_axis_hint"; visible: !root.state.source_axis_enabled; text: root.state.source_axis_tooltip || "" }
                Note { text: root.state.axis_status || "" }
            }
        }
        Section {
            sectionKey: "labels"
            title: "Labels"
            initiallyExpanded: false
            Field { field: "plot_title"; label: "Title"; kind: "label"; hint: "Literal text. An empty title is allowed." }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Field { field: "y_axis_name"; label: "Y-axis label"; kind: "label" }
                Field { field: "y_axis_unit"; label: "Unit (optional)"; kind: "label"; Layout.preferredWidth: 0.6 }
            }
            Choice {
                field: "signal_preset"
                label: "Colorbar label preset"
                selected: String(root.value("signal_title"))
                choices: root.state.signal_presets || []
                onChosen: function(value, owner) { root.bridge.setLabel("signal_title", value, owner); root.bridge.finishLabels(owner) }
            }
            Field { field: "signal_title"; label: "Colorbar label"; kind: "label"; hint: root.state.signal_title_tooltip || "" }
            Toggle { field: "signal_title_auto"; text: "Colorbar label from applied data"; Accessible.description: root.state.signal_title_tooltip || "" }
            Section {
                sectionKey: "templates"
                title: "Label templates"
                advanced: true
                initiallyExpanded: false
                Toggle { field: "fix_annotations"; text: "Fix labels across DATs"; Accessible.description: "Carry manual title and axis labels to new files. Active templates regenerate for every DAT." }
                Choice { field: "title_preset"; label: "Title preset"; choices: (root.state.title_presets || []).map(function(name) { return {label: name, value: name} }); onChosen: function(value, owner) { root.bridge.setChoice(field, value, owner) } }
                ColumnLayout {
                    visible: !!root.state.template_active
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    spacing: 8
                    Field { field: "title_pattern"; label: "Format"; kind: "template"; placeholder: "E={voltage} V {field} T {valley}" }
                    Choice {
                        field: "insert_title_field"
                        label: "Insert field"
                        selected: ""
                        choices: (root.state.title_fields || []).map(function(name) { return {label: "{" + name + "}", value: name} })
                        onChosen: function(value, owner) {
                            const editor = root.editors["title_pattern"]
                            const at = editor.cursorPosition
                            const text = String(root.value("title_pattern"))
                            root.bridge.setTemplate("title_pattern", text.slice(0, at) + "{" + value + "}" + text.slice(at), owner)
                            editor.forceActiveFocus(Qt.OtherFocusReason)
                            editor.cursorPosition = at + value.length + 2
                        }
                    }
                    Field { field: "title_angle_map"; label: "Angle map (optional)"; kind: "template"; placeholder: "85=K;175=Kp" }
                    Field { field: "title_preset_name"; label: "Save preset as"; kind: "template" }
                    RowLayout {
                        Layout.fillWidth: true
                        Action { actionKey: "template_save"; text: "Save preset" }
                        Action { actionKey: "template_apply"; text: "Apply template"; primary: true }
                    }
                }
                Note { objectName: "shared_template_status"; visible: !!root.state.title_template_status; text: root.state.title_template_status || "" }
            }
        }
    }
}



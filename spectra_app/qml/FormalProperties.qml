import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "formalProperties"
    property font controlFont: Qt.application.font
    required property QtObject bridge
    readonly property var state: bridge.state
    readonly property var fields: state.fields || ({})
    property var editors: ({})
    property var disclosureHeaders: []
    property bool suppressFieldFinish: false
    implicitWidth: 300
    implicitHeight: content.implicitHeight + 20
    color: colors.surface
    signal revealY(real y)

    AppColors { id: colors }

    function captureInput(control) {
        control.inputOwner = root.bridge.capturePropertiesInput(root)
    }
    function input(control, method, args) {
        const bridge = root.bridge
        const owner = control.inputOwner
        if (!bridge.propertiesInputCurrent(root, owner)) return
        bridge[method].apply(bridge, args.concat([root, owner]))
        // An accepted edit invalidates the old rendered presentation. Continue
        // editing on this focus owner, but never renew after a bridge rebind.
        if (root.bridge === bridge) control.inputOwner = bridge.capturePropertiesInput(root)
    }
    component InputButton: SurfaceButton {
        property string inputOwner: ""
        onPressed: root.captureInput(this)
    }
    function registerEditor(field, item) { editors[field] = item }
    function disclosureHasFocus() {
        for (let i = 0; i < disclosureHeaders.length; ++i)
            if (disclosureHeaders[i].activeFocus) return true
        return false
    }
    function fieldValue(field) { return fields[field] === undefined ? "" : fields[field] }
    function propertiesPlacement() {
        let owner = root
        while (owner.parent && owner.objectName !== "applicationProperties") owner = owner.parent
        return owner.parent || owner
    }
    function focusField(section, field) { revealField(section, field, true) }
    function revealField(section, field, takeFocus) {
        if (section === "page") pageSection.expanded = true
        else if (section === "frame") frameSection.expanded = true
        else if (section === "axes") axesSection.expanded = true
        else if (section === "colorbar") colorbarSection.expanded = true
        else if (section === "annotations") annotationsSection.expanded = true
        if (["heatmap_dpi", "include_peak_labels", "font_family"].indexOf(field) >= 0) fontSection.expanded = true
        if (["bar_width_in", "bar_height_in", "bar_gap_in", "bar_frame_gap_in", "bar_alignment", "bar_signal_position", "bar_show_extensions"].indexOf(field) >= 0) barLayoutSection.expanded = true
        if (["bar_id", "custom_ticks", "tick_values", "tick_labels"].indexOf(field) >= 0) customTicksSection.expanded = true
        const inputOwner = root.bridge.capturePropertiesInput(root)
        const editor = root.editors[field]
        // Validation may retire input while this exact editor keeps focus.
        if (takeFocus && inputOwner && editor && editor.activeFocus && typeof editor.inputOwner === "string")
            editor.inputOwner = inputOwner
        pendingFocus.field = field
        pendingFocus.takeFocus = takeFocus
        pendingFocus.requestBridge = root.bridge
        pendingFocus.inputOwner = inputOwner
        pendingFocus.requestPlacement = root.propertiesPlacement()
        pendingFocus.requestWindow = root.Window.window
        pendingFocus.restart()
    }
    Timer {
        id: pendingFocus
        property string field
        property bool takeFocus: true
        property var requestBridge: null
        property string inputOwner: ""
        property var requestPlacement: null
        property var requestWindow: null
        interval: 20
        onTriggered: {
            if (root.bridge !== requestBridge || !inputOwner || root.propertiesPlacement() !== requestPlacement
                    || root.Window.window !== requestWindow || !requestBridge.capturePropertiesInput(root)) return
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
        function onFocusRequested(section, field) { root.focusField(section, field) }
        function onRevealRequested(section, field) { root.revealField(section, field, false) }
    }

    component Note: Item {
        property alias text: noteText.text
        property alias color: noteText.color
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        implicitWidth: Math.max(1, root.width - 28)
        implicitHeight: noteText.implicitHeight
        Text {
            id: noteText
            width: parent.width
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: colors.secondary
            font: root.controlFont
            lineHeight: 1.15
        }
    }
    component Section: ColumnLayout {
        id: section
        property string title
        property bool expanded: false
        property bool advanced: false
        default property alias contents: body.data
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 7
        Controls.Button {
            id: sectionButton
            objectName: section.objectName + "_toggle"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.max(34, heading.implicitHeight + 14)
            padding: 7
            hoverEnabled: true
            focusPolicy: Qt.TabFocus
            font: root.controlFont
            Accessible.name: section.title
            Accessible.description: section.expanded ? "Expanded. Press to collapse." : "Collapsed. Press to expand."
            Component.onCompleted: root.disclosureHeaders.push(sectionButton)
            onClicked: {
                root.suppressFieldFinish = true
                forceActiveFocus(Qt.MouseFocusReason)
                section.expanded = !section.expanded
                Qt.callLater(function() { root.suppressFieldFinish = false })
            }
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) {
                    section.expanded = event.key === Qt.Key_Right
                    event.accepted = true
                } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
                    if (!event.isAutoRepeat) sectionButton.clicked()
                    event.accepted = true
                }
            }
            Keys.onReleased: function(event) {
                if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space)
                    event.accepted = true
            }
            contentItem: RowLayout {
                Text { text: section.expanded ? "▾" : "▸"; font: root.controlFont; color: colors.secondary; textFormat: Text.PlainText }
                Text {
                    id: heading
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: section.title
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: colors.text
                    font: root.controlFont
                }
            }
            background: Rectangle {
                radius: 4
                color: sectionButton.down ? colors.pressed : sectionButton.hovered ? colors.hover : section.advanced ? colors.surface : colors.disabled
                border.width: sectionButton.visualFocus ? 2 : 1
                border.color: sectionButton.visualFocus ? colors.accent : section.advanced ? colors.border : colors.divider
            }
        }
        ColumnLayout {
            id: body
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.leftMargin: 4
            Layout.rightMargin: 4
            visible: section.expanded
            spacing: 8
        }
    }
    component Field: ColumnLayout {
        id: fieldControl
        property string field
        property string label
        property string hint: ""
        property string placeholder: ""
        property bool numeric: true
        property bool annotation: false
        readonly property bool invalid: root.state.errorField === field && !!root.state.error
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.preferredWidth: 1
        Layout.alignment: Qt.AlignTop
        spacing: 3
        Note { text: fieldControl.label; color: fieldControl.enabled ? colors.text : colors.disabledText }
        Controls.TextField {
            id: editor
            property string inputOwner: ""
            onActiveFocusChanged: if (activeFocus) root.captureInput(editor)
            objectName: fieldControl.field
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            font: root.controlFont
            implicitHeight: Math.max(32, implicitBackgroundHeight, contentHeight + topPadding + bottomPadding)
            leftPadding: 8; rightPadding: 8; topPadding: 6; bottomPadding: 6
            color: enabled ? colors.text : colors.disabledText
            selectionColor: colors.accent
            selectedTextColor: colors.surface
            selectByMouse: true
            inputMethodHints: fieldControl.numeric ? Qt.ImhFormattedNumbersOnly : Qt.ImhNone
            placeholderText: fieldControl.placeholder
            Accessible.name: fieldControl.label
            Accessible.description: fieldControl.hint
            onTextEdited: root.input(editor, "setDraft", [fieldControl.field, text])
            onAccepted: if (!fieldControl.annotation) root.input(editor, "commitField", [fieldControl.field])
            onEditingFinished: if (!activeFocus && !fieldControl.annotation && !root.suppressFieldFinish && !root.disclosureHasFocus()) root.input(editor, "commitFieldOnBlur", [fieldControl.field])
            Keys.onPressed: function(event) {
                if (fieldControl.numeric && (event.key === Qt.Key_Up || event.key === Qt.Key_Down)) {
                    root.input(editor, "stepField", [fieldControl.field, event.key === Qt.Key_Up ? 1 : -1])
                    event.accepted = true
                }
            }
            Binding on text { value: String(root.fieldValue(fieldControl.field)) }
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
        Note { visible: fieldControl.invalid; text: visible ? root.state.error : ""; color: colors.error; Accessible.name: fieldControl.label + " validation error" }
    }
    component Choice: ColumnLayout {
        id: choiceControl
        property string field
        property string label
        property var choices: []
        property string value: String(root.fieldValue(field))
        property string inputOwner: ""
        signal chosen(string key)
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 3
        Note { text: choiceControl.label; color: choiceControl.enabled ? colors.text : colors.disabledText }
        Controls.ComboBox {
            id: choice
            onDownChanged: if (down) root.captureInput(choiceControl)
            onActiveFocusChanged: if (activeFocus) root.captureInput(choiceControl)
            objectName: choiceControl.field
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            font: root.controlFont
            implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
            model: choiceControl.choices
            textRole: "label"
            valueRole: "key"
            leftPadding: 8
            rightPadding: 26
            hoverEnabled: true
            Accessible.name: choiceControl.label
            onActivated: choiceControl.chosen(currentValue)
            Binding on currentIndex {
                value: {
                    for (let i = 0; i < choiceControl.choices.length; ++i)
                        if (String(choiceControl.choices[i].key) === choiceControl.value) return i
                    return -1
                }
            }
            Component.onCompleted: {
                root.registerEditor(choiceControl.field, choice)
                popup.popupType = Controls.Popup.Window
                popup.height = Qt.binding(function() {
                    return Math.min(popup.contentItem.implicitHeight, choice.Screen.desktopAvailableHeight - popup.topMargin - popup.bottomMargin)
                })
            }
            contentItem: Text { text: choice.displayText; textFormat: Text.PlainText; font: root.controlFont; color: choice.enabled ? colors.text : colors.disabledText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
            indicator: Text { x: choice.width - width - 8; y: (choice.height - height) / 2; text: "⌄"; textFormat: Text.PlainText; font: root.controlFont; color: colors.secondary }
            background: Rectangle { radius: 4; color: !choice.enabled ? colors.disabled : choice.down ? colors.pressed : choice.hovered ? colors.hover : colors.surface; border.width: choice.visualFocus ? 2 : 1; border.color: choice.visualFocus ? colors.accent : colors.border }
            delegate: Controls.ItemDelegate {
                required property var modelData
                required property int index
                width: choice.width
                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                highlighted: choice.highlightedIndex === index
                font: root.controlFont
                contentItem: Text { text: modelData.label; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap; verticalAlignment: Text.AlignVCenter }
                background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
            }
        }
    }
    component Toggle: SurfaceCheckBox {
        id: toggle
        property string inputOwner: ""
        onPressed: root.captureInput(toggle)
        property string field
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.preferredWidth: 1
        objectName: field
        font: root.controlFont
        implicitHeight: Math.max(30, contentItem.implicitHeight + 6)
        onClicked: root.input(toggle, "setValue", [field, checked])
        Binding on checked { value: !!root.fieldValue(toggle.field) }
        Component.onCompleted: root.registerEditor(field, toggle)
        contentItem: Text { text: toggle.text; textFormat: Text.PlainText; font: root.controlFont; color: toggle.enabled ? colors.text : colors.disabledText; leftPadding: toggle.indicator.width + toggle.spacing; verticalAlignment: Text.AlignVCenter; wrapMode: Text.Wrap }
    }

    ColumnLayout {
        id: content
        x: 10; y: 10
        width: Math.max(1, root.width - 20)
        spacing: 10
        Note { objectName: "formal_dimensions"; visible: !!root.state.dimensions; text: root.state.dimensions; color: colors.text }
        Note { objectName: "formal_status"; visible: !!root.state.status; text: root.state.status }
        Note { objectName: "formal_clipboard"; visible: !!root.state.clipboard; text: root.state.clipboard }
        Note { objectName: "formal_error"; visible: !!root.state.error && !root.state.errorField; text: visible ? root.state.error : ""; color: colors.error }
        Choice {
            field: "formal_format"
            label: "Saved format"
            value: root.state.selectedFormat || ""
            choices: (root.state.formats || []).map(function(name) { return {key: name, label: name} })
            enabled: root.bridge.enabled && choices.length > 0
            onChosen: function(key) { root.input(this, "selectFormat", [key]) }
        }
        Section {
            id: pageSection
            objectName: "formal_page"
            title: "Page and text size"
            Note { text: "Page size includes the data frame, labels and colorbars." }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8; rowSpacing: 8
                Field { field: "width_in"; label: "Width (in)"; enabled: root.bridge.enabled }
                Field { field: "height_in"; label: "Height (in)"; enabled: root.bridge.enabled }
                Field { field: "numeric_pt"; label: "Numeric (pt)"; enabled: root.bridge.enabled }
                Field { field: "text_pt"; label: "Text (pt)"; enabled: root.bridge.enabled }
            }
            Toggle { field: "aspect_locked"; text: "Lock aspect ratio"; enabled: root.bridge.enabled }
            Section {
                id: fontSection
                objectName: "formal_font_export"
                title: "Font and export"
                advanced: true
                Note { objectName: "font_family"; text: "Font: Arial"; Component.onCompleted: root.registerEditor("font_family", this) }
                Field { field: "heatmap_dpi"; label: "Heatmap resolution (DPI)"; enabled: root.bridge.enabled }
                Toggle { field: "include_peak_labels"; text: "Include selected peak labels"; enabled: root.bridge.enabled }
            }
        }
        Section {
            id: frameSection
            objectName: "formal_frame"
            title: "Frame and title layout"
            Note { text: "The frame is the data area inside the axes." }
            Toggle { field: "frame_auto"; text: "Fit frame to page"; enabled: root.bridge.enabled }
            GridLayout {
                Layout.fillWidth: true
                visible: !root.fields.frame_auto
                columns: 2; columnSpacing: 8
                Field { field: "frame_width_in"; label: "Frame width (in)"; enabled: root.bridge.enabled }
                Field { field: "frame_height_in"; label: "Frame height (in)"; enabled: root.bridge.enabled }
            }
            Note { text: "Title position uses frame fractions (0–1). You can also drag the title in the preview." }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8
                Field { field: "title_x"; label: "Title X (0–1)"; enabled: root.bridge.enabled }
                Field { field: "title_y"; label: "Title Y (0–1)"; enabled: root.bridge.enabled }
            }
        }
        Section {
            id: axesSection
            objectName: "formal_axes"
            title: "Axes and ticks"
            Toggle { field: "axis_exchange_xy"; text: "Exchange X and Y"; enabled: root.bridge.enabled }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8; rowSpacing: 2
                Toggle { field: "axis_flip_x"; text: "Flip horizontal"; enabled: root.bridge.enabled }
                Toggle { field: "axis_flip_y"; text: "Flip vertical"; enabled: root.bridge.enabled }
                Toggle { field: "axis_show_x"; text: "Horizontal axis"; enabled: root.bridge.enabled }
                Toggle { field: "axis_show_y"; text: "Vertical axis"; enabled: root.bridge.enabled }
                Toggle { field: "axis_show_x_label"; text: "Horizontal label"; enabled: root.bridge.enabled }
                Toggle { field: "axis_show_y_label"; text: "Vertical label"; enabled: root.bridge.enabled }
            }
            Toggle { field: "axis_show_tick_labels"; text: "Tick labels"; enabled: root.bridge.enabled }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8; rowSpacing: 2
                Toggle { field: "axis_major_ticks"; text: "Major ticks"; enabled: root.bridge.enabled }
                Toggle { field: "axis_minor_ticks"; text: "Minor ticks"; enabled: root.bridge.enabled }
                Toggle { field: "axis_ticks_inside"; text: "Ticks inside"; enabled: root.bridge.enabled }
                Toggle { field: "axis_border"; text: "Border"; enabled: root.bridge.enabled }
            }
            Field { field: "axis_border_width"; label: "Border width (pt)"; visible: !!root.fields.axis_border; enabled: root.bridge.enabled }
            Note { text: "Major ticks refer to the displayed horizontal and vertical axes. Manual increments and counts require linear coordinates." }
            Choice { field: "axis_x_mode"; label: "Horizontal major ticks"; enabled: root.bridge.enabled; choices: [{key: "Auto", label: "Auto"}, {key: "By increment", label: "By increment"}, {key: "By count", label: "By count"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
            Field { field: "axis_x_step"; label: "Horizontal increment (axis units)"; visible: root.fields.axis_x_mode === "By increment"; enabled: root.bridge.enabled }
            Field { field: "axis_x_count"; label: "Horizontal major count"; visible: root.fields.axis_x_mode === "By count"; enabled: root.bridge.enabled }
            Field { field: "axis_x_minor_count"; label: "Horizontal count between major ticks"; enabled: root.bridge.enabled; hint: "On nonlinear axes, 0 disables minor ticks and 1 keeps automatic scale defaults." }
            Choice { field: "axis_y_mode"; label: "Vertical major ticks"; enabled: root.bridge.enabled; choices: [{key: "Auto", label: "Auto"}, {key: "By increment", label: "By increment"}, {key: "By count", label: "By count"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
            Field { field: "axis_y_step"; label: "Vertical increment (axis units)"; visible: root.fields.axis_y_mode === "By increment"; enabled: root.bridge.enabled }
            Field { field: "axis_y_count"; label: "Vertical major count"; visible: root.fields.axis_y_mode === "By count"; enabled: root.bridge.enabled }
            Field { field: "axis_y_minor_count"; label: "Vertical count between major ticks"; enabled: root.bridge.enabled; hint: "On nonlinear axes, 0 disables minor ticks and 1 keeps automatic scale defaults." }
        }
        Section {
            id: colorbarSection
            objectName: "formal_colorbar"
            title: "Top colorbar"
            Toggle { field: "bar_visible"; text: "Visible"; enabled: root.bridge.enabled }
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8; rowSpacing: 2
                Toggle { field: "bar_show_label"; text: "Signal label"; enabled: root.bridge.enabled }
                Toggle { field: "bar_show_unit"; text: "Signal unit"; enabled: root.bridge.enabled }
            }
            Choice { field: "bar_label_format"; label: "Number format"; enabled: root.bridge.enabled; choices: [{key: "auto", label: "Auto"}, {key: "decimal", label: "Decimal"}, {key: "scientific", label: "Scientific"}, {key: "engineering", label: "Engineering"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
            Field {
                field: "bar_precision"
                label: root.fields.bar_label_format === "decimal" ? "Decimal places" : root.fields.bar_label_format === "scientific" ? "Mantissa decimal places" : "Significant digits"
                enabled: root.bridge.enabled
            }
            Note { text: root.fields.bar_label_format === "decimal" ? "Digits after the decimal point (0–12)." : root.fields.bar_label_format === "scientific" ? "Digits after the decimal point in the scientific mantissa (0–12)." : "Total significant digits in each label (1–12)." }
            Choice { field: "bar_scientific_style"; label: "Scientific style"; visible: root.fields.bar_label_format === "scientific"; enabled: root.bridge.enabled; choices: [{key: "e", label: "Compact e"}, {key: "times-ten", label: "×10 superscript"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
            Toggle { field: "bar_trim_zeros"; text: "Trim trailing zeros"; enabled: root.bridge.enabled }
            Note { objectName: "bar_sample"; text: "Sample labels: " + (root.state.sample || "Selected bar unavailable") }
            Note { objectName: "bar_warning"; visible: !!root.state.warning; text: root.state.warning; color: colors.error }
            Section {
                id: barLayoutSection
                objectName: "formal_bar_layout"
                title: "Colorbar layout"
                advanced: true
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2; columnSpacing: 8; rowSpacing: 8
                    Field { field: "bar_width_in"; label: "Width (in)"; enabled: root.bridge.enabled }
                    Field { field: "bar_height_in"; label: "Height (in)"; enabled: root.bridge.enabled }
                    Field { field: "bar_gap_in"; label: "Between bars (in)"; enabled: root.bridge.enabled }
                    Field { field: "bar_frame_gap_in"; label: "Above frame (in)"; enabled: root.bridge.enabled }
                }
                Choice { field: "bar_alignment"; label: "Alignment"; enabled: root.bridge.enabled; choices: [{key: "right", label: "Right"}, {key: "center", label: "Center"}, {key: "left", label: "Left"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
                Choice { field: "bar_signal_position"; label: "Signal position"; enabled: root.bridge.enabled; choices: [{key: "left", label: "Left"}, {key: "above", label: "Above"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
                Toggle { field: "bar_show_extensions"; text: "Under / over triangles"; enabled: root.bridge.enabled }
            }
            Section {
                id: customTicksSection
                objectName: "formal_custom_ticks"
                title: "Custom ticks"
                advanced: true
                Choice { field: "bar_id"; label: "Bar"; enabled: root.bridge.enabled; value: root.state.selectedBar; choices: (root.state.barIds || []).map(function(id) { return {key: id, label: id} }); onChosen: function(key) { root.input(this, "selectBar", [key]) } }
                Toggle { field: "custom_ticks"; text: "Custom three ticks"; enabled: root.bridge.enabled }
                Note { visible: !!root.fields.custom_ticks; text: "Enter exactly three increasing values. Optional labels use the same order, separated by |." }
                Field { field: "tick_values"; label: "Values (signal units)"; numeric: false; placeholder: "low, middle, high"; visible: !!root.fields.custom_ticks; enabled: root.bridge.enabled }
                Field { field: "tick_labels"; label: "Labels (optional)"; numeric: false; placeholder: "label | label | label"; visible: !!root.fields.custom_ticks; enabled: root.bridge.enabled }
            }
        }
        Section {
            id: annotationsSection
            objectName: "formal_annotations"
            title: "Text and arrows"
            GridLayout {
                Layout.fillWidth: true
                columns: 2; columnSpacing: 8
                InputButton { objectName: "add_text"; text: "Add text…"; font: root.controlFont; implicitHeight: Math.max(32, contentItem.implicitHeight + 12); Layout.fillWidth: true; enabled: root.bridge.enabled && root.state.ready; onClicked: root.input(this, "openPrompt", ["text"]) }
                InputButton { objectName: "add_arrow"; text: "Add arrow…"; font: root.controlFont; implicitHeight: Math.max(32, contentItem.implicitHeight + 12); Layout.fillWidth: true; enabled: root.bridge.enabled && root.state.ready; onClicked: root.input(this, "openPrompt", ["arrow"]) }
            }
            Note { visible: (root.state.annotations || []).length === 0; text: "No text or arrows on this page." }
            ColumnLayout {
                id: annotationItems
                objectName: "annotation_list"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                visible: (root.state.annotations || []).length > 0
                spacing: 3
                Repeater {
                    model: root.state.annotations || []
                    Controls.ItemDelegate {
                        id: annotationItem
                        property string inputOwner: ""
                        onPressed: root.captureInput(annotationItem)
                        required property var modelData
                        objectName: "annotation_" + modelData.id
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                        font: root.controlFont
                        enabled: root.bridge.enabled
                        highlighted: root.state.selectedAnnotation === modelData.id
                        Accessible.name: modelData.label
                        onClicked: root.input(annotationItem, "selectAnnotation", [modelData.id])
                        contentItem: Text { text: annotationItem.modelData.label; textFormat: Text.PlainText; font: root.controlFont; color: colors.text; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 3; color: annotationItem.highlighted ? colors.selected : annotationItem.hovered ? colors.hover : colors.surface; border.width: annotationItem.visualFocus ? 2 : 1; border.color: annotationItem.visualFocus ? colors.accent : colors.divider }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 8
                enabled: root.bridge.enabled && !!root.state.selectedAnnotation
                Field { field: "annotation_text"; label: "Text / number"; numeric: false; annotation: true }
                Choice { field: "annotation_coordinate_space"; label: "Text / tail coordinates"; choices: [{key: "axes-fraction", label: "Relative to plot frame (0–1)"}, {key: "data", label: "Data coordinates"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2; columnSpacing: 8
                    Field { field: "annotation_x"; label: root.fields.annotation_coordinate_space === "data" ? "Text / tail X (" + ((root.state.annotationUnits || {}).x || "X data units") + ")" : "Text / tail X (0–1)"; annotation: true }
                    Field { field: "annotation_y"; label: root.fields.annotation_coordinate_space === "data" ? "Text / tail Y (" + ((root.state.annotationUnits || {}).y || "Y data units") + ")" : "Text / tail Y (0–1)"; annotation: true }
                }
                Choice { field: "annotation_target_space"; label: "Arrow head coordinates"; visible: root.state.annotationKind === "arrow"; choices: [{key: "axes-fraction", label: "Relative to plot frame (0–1)"}, {key: "data", label: "Data coordinates"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2; columnSpacing: 8
                    visible: root.state.annotationKind === "arrow"
                    Field { field: "annotation_target_x"; label: root.fields.annotation_target_space === "data" ? "Arrow head X (" + ((root.state.annotationUnits || {}).x || "X data units") + ")" : "Arrow head X (0–1)"; annotation: true }
                    Field { field: "annotation_target_y"; label: root.fields.annotation_target_space === "data" ? "Arrow head Y (" + ((root.state.annotationUnits || {}).y || "Y data units") + ")" : "Arrow head Y (0–1)"; annotation: true }
                }
                Choice { field: "annotation_font_role"; label: "Use page font size"; choices: [{key: "text", label: "Text size"}, {key: "numeric", label: "Numeric size"}]; onChosen: function(key) { root.input(this, "setValue", [field, key]) } }
                Toggle { field: "annotation_visible"; text: "Visible" }
                Note { text: "Apply to keep the selected annotation edits." }
                InputButton { objectName: "annotation_apply"; text: "Apply annotation edit"; font: root.controlFont; implicitHeight: Math.max(32, contentItem.implicitHeight + 12); Layout.fillWidth: true; primary: true; onClicked: root.input(this, "applyAnnotation", []) }
                InputButton { objectName: "annotation_delete"; text: "Delete annotation"; font: root.controlFont; implicitHeight: Math.max(32, contentItem.implicitHeight + 12); Layout.fillWidth: true; onClicked: root.input(this, "deleteAnnotation", []) }
            }
        }
    }
}

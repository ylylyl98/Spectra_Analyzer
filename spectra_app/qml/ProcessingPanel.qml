import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    objectName: "processingPanel"
    required property var processing
    implicitWidth: 300
    implicitHeight: content.implicitHeight + 20
    color: colors.surface
    Accessible.name: "Data processing"

    AppColors { id: colors }

    ColumnLayout {
        id: content
        x: 10
        y: 8
        width: Math.max(1, root.width - 20)
        spacing: 7

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "Mode"
                color: colors.secondary
                font.family: Qt.application.font.family
                font.pixelSize: 12
                Layout.fillWidth: true
            }

            SurfaceCheckBox {
                id: fixedToggle
                objectName: "processingFixed"
                text: "Fix"
                enabled: root.processing.enabled && !root.processing.busy
                Accessible.name: "Fix processing settings"
                Accessible.description: "Carry mode and smoothing settings across files and restarts."
                onClicked: root.processing.setFixed(checked)
                Binding on checked { value: root.processing.fixed }
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: Accessible.description
                Controls.ToolTip.delay: 650
            }
        }

        Controls.ComboBox {
            id: modeCombo
            objectName: "processingMode"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: 32
            model: ["Original", "Energy 2nd derivative"]
            enabled: root.processing.enabled && !root.processing.busy
            hoverEnabled: true
            focusPolicy: Qt.StrongFocus
            leftPadding: 9
            rightPadding: 28
            font.family: Qt.application.font.family
            font.pixelSize: 12
            Accessible.name: "Processing mode"
            Accessible.description: "Choose the data represented by the heatmap and accompanying spectrum."
            onActivated: function(index) {
                root.processing.setMode(index === 1 ? "energy_second_derivative" : "original")
            }
            Binding on currentIndex {
                value: root.processing.mode === "energy_second_derivative" ? 1 : 0
            }

            contentItem: Text {
                text: modeCombo.displayText
                font: modeCombo.font
                color: modeCombo.enabled ? colors.text : colors.disabledText
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }

            indicator: Text {
                x: modeCombo.width - width - 10
                y: (modeCombo.height - height) / 2
                text: "\u2304"
                font.family: Qt.application.font.family
                font.pixelSize: 17
                color: modeCombo.enabled ? colors.secondary : colors.disabledText
            }

            background: Rectangle {
                radius: 4
                color: !modeCombo.enabled ? colors.disabled
                       : modeCombo.down ? colors.pressed
                       : modeCombo.hovered ? colors.hover : colors.surface
                border.width: modeCombo.visualFocus ? 2 : 1
                border.color: modeCombo.visualFocus ? colors.accent : colors.border
            }

            delegate: Controls.ItemDelegate {
                required property string modelData
                required property int index
                width: modeCombo.width
                implicitHeight: 34
                text: modelData
                highlighted: modeCombo.highlightedIndex === index
                font: modeCombo.font
                contentItem: Text {
                    text: parent.text
                    color: colors.text
                    font: modeCombo.font
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                background: Rectangle {
                    color: parent.highlighted ? colors.selected : colors.surface
                }
            }

            popup: Controls.Popup {
                y: modeCombo.height + 3
                width: modeCombo.width
                padding: 4
                implicitHeight: popupList.contentHeight + 8
                contentItem: ListView {
                    id: popupList
                    clip: true
                    implicitHeight: contentHeight
                    model: modeCombo.popup.visible ? modeCombo.delegateModel : null
                    currentIndex: modeCombo.highlightedIndex
                    boundsBehavior: Flickable.StopAtBounds
                }
                background: Rectangle {
                    radius: 4
                    color: colors.surface
                    border.color: colors.border
                }
            }

            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: Accessible.description
            Controls.ToolTip.delay: 650
        }

        SurfaceCheckBox {
            id: smoothingToggle
            objectName: "processingSmoothing"
            Layout.fillWidth: true
            text: "SG smoothing"
            enabled: root.processing.enabled && root.processing.smoothingEnabled && !root.processing.busy
            Accessible.name: "SG smoothing"
            Accessible.description: "Compute the second derivative directly from a local polynomial fit (SG). When disabled, use ordinary finite differences."
            onClicked: root.processing.setSmoothing(checked)
            Binding on checked { value: root.processing.smoothing }
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: Accessible.description
            Controls.ToolTip.delay: 650
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Text {
                text: "Window points"
                Layout.fillWidth: true
                color: windowEdit.enabled ? colors.secondary : colors.disabledText
                font.family: Qt.application.font.family
                font.pixelSize: 12
            }

            Controls.TextField {
                id: windowEdit
                objectName: "processingWindow"
                Layout.preferredWidth: 88
                Layout.minimumWidth: 60
                implicitHeight: 32
                enabled: root.processing.enabled && root.processing.windowEnabled && !root.processing.busy
                selectByMouse: true
                inputMethodHints: Qt.ImhDigitsOnly
                horizontalAlignment: TextInput.AlignRight
                verticalAlignment: TextInput.AlignVCenter
                leftPadding: 9
                rightPadding: 9
                topPadding: 5
                bottomPadding: 5
                color: enabled ? colors.text : colors.disabledText
                selectionColor: colors.accent
                selectedTextColor: colors.surface
                font.family: Qt.application.font.family
                font.pixelSize: 12
                Accessible.name: "Smoothing window points"
                Accessible.description: "Odd smoothing window length in energy points, from 5 to 1001. Press Enter to confirm."
                onTextEdited: root.processing.setWindowDraft(text)
                onAccepted: root.processing.commitWindow()

                // Keep backend restoration live after edits. Focus loss does not commit a draft.
                Binding on text { value: root.processing.windowDraft }

                background: Rectangle {
                    radius: 4
                    color: !windowEdit.enabled ? colors.disabled
                           : root.processing.error.length > 0 ? colors.errorSurface : colors.surface
                    border.width: windowEdit.activeFocus ? 2 : 1
                    border.color: root.processing.error.length > 0 ? colors.error
                                  : windowEdit.activeFocus ? colors.accent : colors.border
                }

                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: Accessible.description
                Controls.ToolTip.delay: 650
            }
        }

        Text {
            objectName: "processingError"
            Layout.fillWidth: true
            visible: root.processing.error.length > 0
            text: root.processing.error
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: colors.error
            font.family: Qt.application.font.family
            font.pixelSize: 11
            Accessible.name: "Processing validation error"
            Accessible.description: text
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: colors.divider
        }

        Text {
            objectName: "processingStatus"
            Layout.fillWidth: true
            text: root.processing.status
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: colors.secondary
            font.family: Qt.application.font.family
            font.pixelSize: 11
            lineHeight: 1.2
            Accessible.name: "Applied and pending data processing"
            Accessible.description: text
        }

        SurfaceButton {
            objectName: "processingApply"
            Layout.fillWidth: true
            primary: true
            text: root.processing.busy ? "Processing\u2026" : "Apply processing"
            enabled: root.processing.enabled && root.processing.applyEnabled && !root.processing.busy
            Accessible.name: "Apply processing"
            Accessible.description: "Create a new independent Processed Sheet and linked Heatmap from the original DAT using these settings. Earlier results remain available. Choose Original to return to source data."
            onClicked: root.processing.apply()
            Controls.ToolTip.visible: hovered
            Controls.ToolTip.text: Accessible.description
            Controls.ToolTip.delay: 650
        }
    }
}

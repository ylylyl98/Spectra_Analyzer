import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Controls.Pane {
    id: root
    objectName: "subplotEditor"
    required property var bridge
    required property var dialogHost
    property font controlFont: Qt.application.font
    font: controlFont
    implicitWidth: 520
    implicitHeight: 620
    padding: 12

    AppColors { id: colors }
    background: Rectangle { color: colors.surface }
    Keys.onEscapePressed: event => { dialogHost.reject(); event.accepted = true }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Controls.ScrollView {
            id: contentScroll
            objectName: "subplotContent"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 0
            Layout.minimumHeight: 0
            clip: true
            contentWidth: availableWidth
            contentHeight: form.implicitHeight
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

            ColumnLayout {
                id: form
                width: contentScroll.availableWidth
                spacing: 10

                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: "Borders and spacing"
                    font: root.controlFont
                    color: colors.text
                    wrapMode: Text.Wrap
                }

                Text {
                    objectName: "subplotNotice"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: root.bridge.notice
                    visible: text.length > 0
                    textFormat: Text.PlainText
                    font: root.controlFont
                    color: colors.text
                    wrapMode: Text.Wrap
                }

                Text {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    text: "Values range from 0 to 1. Press Enter or leave a field to apply; arrows change by 0.005."
                    textFormat: Text.PlainText
                    font: root.controlFont
                    color: colors.text
                    wrapMode: Text.Wrap
                }

                Repeater {
                    model: root.bridge.fields
                    delegate: ColumnLayout {
                        id: fieldRow
                        required property var modelData
                        readonly property string fieldKey: modelData.key
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        spacing: 4

                        Text {
                            Layout.fillWidth: true
                            text: fieldRow.modelData.label
                            font: root.controlFont
                            color: colors.text
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Controls.TextField {
                                id: editor
                                objectName: "subplotField_" + fieldRow.fieldKey
                                Layout.fillWidth: true
                                Layout.minimumWidth: 40
                                text: root.bridge.drafts[fieldRow.fieldKey]
                                font: root.controlFont
                                color: colors.text
                                selectByMouse: true
                                padding: 8
                                implicitHeight: Math.max(34, contentHeight + topPadding + bottomPadding)
                                Accessible.name: fieldRow.modelData.label
                                onTextEdited: root.bridge.setDraft(fieldRow.fieldKey, text)
                                onEditingFinished: root.bridge.commitDrafts()
                                Keys.onUpPressed: event => { root.bridge.step(fieldRow.fieldKey, 1); event.accepted = true }
                                Keys.onDownPressed: event => { root.bridge.step(fieldRow.fieldKey, -1); event.accepted = true }
                                background: Rectangle {
                                    radius: 4
                                    color: colors.surface
                                    border.width: editor.activeFocus ? 2 : 1
                                    border.color: root.bridge.errors[fieldRow.fieldKey] ? colors.error
                                                  : editor.activeFocus ? colors.accent : colors.border
                                }
                            }

                            SurfaceButton {
                                objectName: "subplotDecrement_" + fieldRow.fieldKey
                                text: "−"
                                font: root.controlFont
                                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                                Accessible.name: "Decrease " + fieldRow.modelData.label
                                onClicked: root.bridge.step(fieldRow.fieldKey, -1)
                            }
                            SurfaceButton {
                                objectName: "subplotIncrement_" + fieldRow.fieldKey
                                text: "+"
                                font: root.controlFont
                                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                                Accessible.name: "Increase " + fieldRow.modelData.label
                                onClicked: root.bridge.step(fieldRow.fieldKey, 1)
                            }
                        }
                    }
                }

                Text {
                    objectName: "subplotValidation"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: text.length > 0
                    text: root.bridge.validationError
                    textFormat: Text.PlainText
                    font: root.controlFont
                    color: colors.error
                    wrapMode: Text.Wrap
                }

                Controls.TextArea {
                    id: exportText
                    objectName: "subplotExportText"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: text.length > 0
                    text: root.bridge.exportText
                    textFormat: TextEdit.PlainText
                    font: root.controlFont
                    color: colors.text
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    Accessible.name: "Export values"
                    background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                }
            }
        }

        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            spacing: 8

            SurfaceButton {
                objectName: "subplotExport"
                text: "Export values"
                font: root.controlFont
                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                onClicked: {
                    if (root.bridge.exportValues()) Qt.callLater(function() {
                        if (!root || !root.Window.window || !root.Window.window.visible)
                            return
                        contentScroll.contentItem.contentY = Math.max(0, contentScroll.contentHeight - contentScroll.availableHeight)
                        exportText.forceActiveFocus(Qt.OtherFocusReason)
                        exportText.selectAll()
                    })
                }
            }
            SurfaceButton {
                objectName: "subplotTightLayout"
                text: "Tight layout"
                font: root.controlFont
                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                onClicked: root.bridge.tightLayout()
            }
            SurfaceButton {
                objectName: "subplotReset"
                text: "Reset"
                font: root.controlFont
                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                onClicked: root.bridge.reset()
            }
            SurfaceButton {
                objectName: "subplotClose"
                text: "Close"
                primary: true
                font: root.controlFont
                implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
                onClicked: root.dialogHost.reject()
            }
        }
    }
}

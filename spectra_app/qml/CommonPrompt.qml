import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Controls.Pane {
    id: root
    objectName: "commonPrompt"
    required property var bridge
    required property var dialogHost
    property font controlFont: Qt.application.font
    font: controlFont
    implicitWidth: 460
    implicitHeight: 250
    padding: 12

    AppColors { id: colors }
    background: Rectangle { color: colors.surface }

    // QuickDialog gives focus to the root after showing its window.
    onActiveFocusChanged: if (activeFocus) Qt.callLater(function() {
        if (!root || !root.bridge || !root.Window.window || !root.Window.window.visible)
            return
        if (root.bridge.mode === "text" || root.bridge.mode === "int" || root.bridge.mode === "float") {
            editor.forceActiveFocus(Qt.OtherFocusReason)
            editor.selectAll()
        } else if (root.bridge.mode === "item") {
            choice.forceActiveFocus(Qt.OtherFocusReason)
        } else {
            for (let i = 0; i < actions.count; ++i) {
                const button = actions.itemAt(i)
                if (button.actionKey === root.bridge.defaultAction) {
                    button.forceActiveFocus(Qt.TabFocusReason)
                    return
                }
            }
        }
    })

    Keys.onReturnPressed: event => { bridge.activateDefault(); event.accepted = true }
    Keys.onEnterPressed: event => { bridge.activateDefault(); event.accepted = true }
    Keys.onEscapePressed: event => { bridge.cancel(); event.accepted = true }

    Connections {
        target: root.bridge
        function onChanged() {
            if (root.bridge.validationError.length > 0) Qt.callLater(function() {
                if (root && root.bridge && root.Window.window && root.Window.window.visible)
                    contentScroll.contentItem.contentY = Math.max(0, contentScroll.contentHeight - contentScroll.availableHeight)
            })
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Controls.ScrollView {
            id: contentScroll
            objectName: "commonPromptContent"
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
                spacing: 8

                Controls.TextArea {
                    id: message
                    objectName: "commonPromptMessage"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: root.bridge.mode === "message"
                    text: root.bridge.message
                    textFormat: TextEdit.PlainText
                    font: root.controlFont
                    color: colors.text
                    wrapMode: TextEdit.Wrap
                    readOnly: true
                    selectByMouse: true
                    activeFocusOnTab: false
                    padding: 0
                    background: null
                }

                Text {
                    objectName: "commonPromptLabel"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: root.bridge.mode !== "message" && text.length > 0
                    text: root.bridge.label
                    textFormat: Text.PlainText
                    font: root.controlFont
                    color: colors.text
                    wrapMode: Text.Wrap
                }

                Controls.TextField {
                    id: editor
                    objectName: "commonPromptEditor"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: root.bridge.mode === "text" || root.bridge.mode === "int" || root.bridge.mode === "float"
                    text: root.bridge.draft
                    font: root.controlFont
                    color: colors.text
                    selectByMouse: true
                    padding: 8
                    implicitHeight: Math.max(34, contentHeight + topPadding + bottomPadding)
                    Accessible.name: root.bridge.label
                    onTextEdited: root.bridge.setDraft(text)
                    Keys.onReturnPressed: event => { root.bridge.activateDefault(); event.accepted = true }
                    Keys.onEnterPressed: event => { root.bridge.activateDefault(); event.accepted = true }
                    background: Rectangle {
                        radius: 4
                        color: colors.surface
                        border.width: editor.activeFocus ? 2 : 1
                        border.color: root.bridge.validationError.length > 0 ? colors.error
                                      : editor.activeFocus ? colors.accent : colors.border
                    }
                }

                Controls.ComboBox {
                    id: choice
                    objectName: "commonPromptChoice"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: root.bridge.mode === "item"
                    model: root.bridge.items
                    currentIndex: root.bridge.currentIndex
                    font: root.controlFont
                    editable: false
                    implicitHeight: Math.max(34, contentItem.implicitHeight + topPadding + bottomPadding)
                    Accessible.name: root.bridge.label
                    onActivated: index => root.bridge.setCurrentIndex(index)
                    Keys.onReturnPressed: event => {
                        if (popup.visible) event.accepted = false
                        else { root.bridge.activateDefault(); event.accepted = true }
                    }
                    Keys.onEnterPressed: event => {
                        if (popup.visible) event.accepted = false
                        else { root.bridge.activateDefault(); event.accepted = true }
                    }
                    contentItem: Text {
                        text: choice.displayText
                        textFormat: Text.PlainText
                        font: choice.font
                        color: colors.text
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        leftPadding: 8
                        rightPadding: choice.indicator.width + 8
                    }
                    background: Rectangle {
                        radius: 4
                        color: choice.down ? colors.pressed : colors.surface
                        border.width: choice.activeFocus ? 2 : 1
                        border.color: choice.activeFocus ? colors.accent : colors.border
                    }
                    delegate: Controls.ItemDelegate {
                        id: option
                        required property int index
                        required property var modelData
                        width: choice.width
                        highlighted: choice.highlightedIndex === index
                        font: root.controlFont
                        contentItem: Text {
                            text: String(option.modelData)
                            textFormat: Text.PlainText
                            font: option.font
                            color: colors.text
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }
                        background: Rectangle { color: option.highlighted ? colors.selected : colors.surface }
                    }
                }

                Text {
                    objectName: "commonPromptValidation"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    visible: text.length > 0
                    text: root.bridge.validationError
                    textFormat: Text.PlainText
                    font: root.controlFont
                    color: colors.error
                    wrapMode: Text.Wrap
                }
            }
        }

        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: implicitHeight
            spacing: 8
            Repeater {
                id: actions
                model: root.bridge.actions
                delegate: SurfaceButton {
                    required property var modelData
                    readonly property string actionKey: modelData.key
                    objectName: "commonPromptAction_" + actionKey
                    text: modelData.label
                    font: root.controlFont
                    implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
                    primary: actionKey === root.bridge.defaultAction
                    onClicked: root.bridge.chooseAction(actionKey)
                    Keys.onReturnPressed: event => { root.bridge.chooseAction(actionKey); event.accepted = true }
                    Keys.onEnterPressed: event => { root.bridge.chooseAction(actionKey); event.accepted = true }
                }
            }
        }
    }
}

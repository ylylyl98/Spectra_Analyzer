import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts
import QtQuick.Window

Rectangle {
    id: root
    objectName: "analysisHeader"
    required property var bridge
    property font controlFont: Qt.application.font
    readonly property var state: bridge.state
    readonly property string accessibleDetails: state.details || ""
    implicitWidth: 442
    implicitHeight: header.implicitHeight + 4
    color: colors.surface
    Accessible.name: "Analysis context"
    Accessible.description: accessibleDetails
    AppColors { id: colors }

    component Action: SurfaceButton {
        property string actionOwner: ""
        font: root.controlFont
        implicitHeight: Math.max(28, contentItem.implicitHeight + 8)
        leftPadding: 8; rightPadding: 8; topPadding: 4; bottomPadding: 4
        onActiveFocusChanged: if (activeFocus) actionOwner = root.bridge.ownerToken
        onPressed: actionOwner = root.bridge.ownerToken
    }
    ColumnLayout {
        id: header
        x: 2; y: 2; width: Math.max(1, root.width - 4)
        spacing: 2
        RowLayout {
            Layout.fillWidth: true; Layout.minimumWidth: 0
            spacing: 6
            Action {
                objectName: "analysisBack"
                text: "← Workspace"
                enabled: root.bridge.backEnabled
                Accessible.name: "Back to workspace"
                onClicked: root.bridge.back(actionOwner)
            }
            Text {
                id: title
                objectName: "contextTitle"
                Layout.fillWidth: true; Layout.minimumWidth: 0
                text: root.state.title || ""
                textFormat: Text.PlainText; elide: Text.ElideMiddle
                font: root.controlFont; color: colors.text
                verticalAlignment: Text.AlignVCenter
                Accessible.name: text
                Accessible.description: "Full source name. Open Details for source and analysis provenance."
                HoverHandler { id: sourceHover }
                Controls.ToolTip {
                    id: sourceTooltip
                    objectName: "contextSourceTooltip"
                    popupType: Controls.Popup.Window
                    visible: sourceHover.hovered && title.text.length > 0
                    text: title.text
                    delay: 650
                    font: root.controlFont
                    width: Math.min(640, implicitWidth)
                    contentItem: Text {
                        objectName: "contextSourceTooltipText"
                        text: sourceTooltip.text
                        textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere
                        font: root.controlFont; color: colors.text
                    }
                    background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
                }
            }
            Action {
                id: detailsButton
                objectName: "contextDetails"
                text: "Details…"
                enabled: root.bridge.enabled && !!root.state.details
                Accessible.name: "Analysis context details"
                onClicked: if (actionOwner === root.bridge.ownerToken) {
                    details.actionOwner = actionOwner
                    details.open()
                }
            }
        }
        Flow {
            id: contextLine
            Layout.fillWidth: true; Layout.minimumWidth: 0
            spacing: 8
            onWidthChanged: forceLayout()
            Text {
                objectName: "contextSummary"
                width: Math.min(implicitWidth, contextLine.width)
                text: root.state.summary || ""
                textFormat: Text.PlainText; wrapMode: Text.Wrap
                font: root.controlFont; color: colors.secondary
                Accessible.name: text
            }
            Text {
                objectName: "contextWarning"
                visible: !!root.state.warning
                width: Math.min(implicitWidth, contextLine.width)
                text: root.state.warning || ""
                textFormat: Text.PlainText; wrapMode: Text.Wrap
                font: root.controlFont; color: colors.error
                Accessible.name: text
            }
        }
    }
    Controls.Popup {
        id: details
        objectName: "analysisContextPopup"
        property string actionOwner: ""
        popupType: Controls.Popup.Window
        x: Math.round((root.width - width) / 2)
        y: detailsButton.height + 4
        width: Math.min(640, Math.max(400, Screen.width - 48))
        height: Math.min(460, Math.max(240, Screen.height - 80))
        padding: 12
        focus: true
        closePolicy: Controls.Popup.CloseOnEscape | Controls.Popup.CloseOnPressOutside
        background: Rectangle { color: colors.surface; border.color: colors.border; radius: 6 }
        onOpened: detailsText.forceActiveFocus(Qt.PopupFocusReason)
        contentItem: ColumnLayout {
            spacing: 10
            Text { text: "Analysis context"; textFormat: Text.PlainText; font: root.controlFont; color: colors.text }
            Controls.ScrollView {
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.minimumWidth: 0; Layout.minimumHeight: 0
                Layout.preferredHeight: 1
                clip: true
                Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                Controls.TextArea {
                    id: detailsText
                    objectName: "contextDetailsText"
                    text: root.state.details || ""
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.WrapAnywhere
                    readOnly: true; selectByMouse: true; selectByKeyboard: true
                    font: root.controlFont; color: colors.text
                    selectionColor: colors.accent; selectedTextColor: colors.surface
                    background: null
                    Accessible.name: "Full source and analysis provenance"
                }
            }
            Action {
                objectName: "contextDetailsClose"
                Layout.alignment: Qt.AlignRight
                text: "Close"
                onClicked: details.close()
            }
        }
    }
    Connections {
        target: root.bridge
        function onStateChanged() {
            if (!root.bridge.enabled || details.actionOwner !== root.bridge.ownerToken)
                details.close()
        }
    }
}

import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property QtObject bridge
    color: colors.surface
    focus: true

    AppColors { id: colors }

    component UpdateAction: SurfaceButton {
        id: action
        font: root.bridge.applicationFont
        width: Math.min(implicitWidth, parent.width)
        implicitHeight: Math.max(32, contentItem.implicitHeight + topPadding + bottomPadding)
        Accessible.name: text
        contentItem: Text {
            text: action.text
            font: action.font
            color: !action.enabled ? colors.disabledText
                   : action.primary ? colors.surface : colors.text
            wrapMode: Text.Wrap
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        Controls.ScrollView {
            id: bodyScroll
            objectName: "updateBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            contentWidth: availableWidth
            contentHeight: body.implicitHeight
            clip: true
            Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

            ColumnLayout {
                id: body
                width: bodyScroll.availableWidth
                spacing: 12

                Controls.Label {
                    id: version
                    objectName: "updateCurrentVersion"
                    Layout.fillWidth: true
                    text: "Current version: " + root.bridge.currentVersion
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: colors.text
                    font: root.bridge.applicationFont
                }
                Controls.Label {
                    id: status
                    objectName: "updateStatus"
                    Layout.fillWidth: true
                    text: root.bridge.statusText
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: colors.text
                    font: root.bridge.applicationFont
                    Accessible.role: Accessible.StaticText
                    Accessible.name: text
                }
                Controls.ProgressBar {
                    objectName: "updateProgress"
                    Layout.fillWidth: true
                    visible: root.bridge.busy
                    indeterminate: visible
                    Accessible.name: "Update operation in progress"
                }
                Controls.ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(96, bodyScroll.availableHeight
                        - version.implicitHeight - status.implicitHeight
                        - (sourceNotice.visible ? sourceNotice.implicitHeight + 12 : 0)
                        - (root.bridge.busy ? 18 : 0) - 24)
                    clip: true
                    Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                    Controls.TextArea {
                        objectName: "updateReleaseNotes"
                        text: root.bridge.releaseNotes
                        placeholderText: "Release notes appear here when an update is available."
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.Wrap
                        textFormat: TextEdit.PlainText
                        color: colors.text
                        placeholderTextColor: colors.secondary
                        selectionColor: colors.accent
                        selectedTextColor: colors.surface
                        font: root.bridge.applicationFont
                        Accessible.name: "Release notes"
                        background: Rectangle {
                            color: colors.surface
                            border.color: parent.activeFocus ? colors.accent : colors.border
                            border.width: parent.activeFocus ? 2 : 1
                            radius: 4
                        }
                    }
                }
                Controls.Label {
                    id: sourceNotice
                    objectName: "updateSourceNotice"
                    visible: root.bridge.sourceMode
                    Layout.fillWidth: true
                    text: "You are running from source. Install application updates from the installed Windows app."
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: colors.secondary
                    font: root.bridge.applicationFont
                }
            }
        }

        Flow {
            objectName: "updateActions"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.preferredWidth: 0
            Layout.preferredHeight: childrenRect.height
            spacing: 8

            UpdateAction {
                objectName: "updateCheck"
                text: "Check again"
                enabled: root.bridge.canCheck
                onClicked: root.bridge.checkUpdates()
            }
            UpdateAction {
                objectName: "updateDownload"
                text: "Download update"
                enabled: root.bridge.canDownload
                onClicked: root.bridge.downloadUpdate()
            }
            UpdateAction {
                objectName: "updateInstall"
                text: "Close app and install"
                enabled: root.bridge.canInstall
                primary: true
                onClicked: root.bridge.installUpdate()
            }
            UpdateAction {
                objectName: "updateClose"
                text: root.bridge.busy ? "Cancel" : "Close"
                onClicked: root.bridge.dismiss()
            }
        }
    }
}

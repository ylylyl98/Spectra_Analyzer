import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Rectangle {
    id: root
    required property QtObject documents
    required property var documentKey
    required property string documentTooltip
    required property string documentTitle
    required property real frameX
    required property real frameY
    required property real frameWidth
    required property real frameHeight
    required property bool selected
    required property bool frameMaximized
    required property int frameLayer
    objectName: "documentFrame_" + documentKey.join("_")
    x: frameX; y: frameY; width: frameWidth; height: frameHeight; z: frameLayer
    visible: documents.mode === "free" || selected
    color: colors.surface
    border.width: 1
    border.color: selected ? colors.accent : colors.border
    AppColors { id: colors }
    Component.onCompleted: documents.attachFrame(documentKey, root, content)
    Rectangle {
        id: title
        objectName: "documentTitleBar"
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
        height: 34
        color: selected ? colors.disabled : colors.surface
        HoverHandler { id: titleHover }
        Controls.ToolTip {
            id: titleTooltip
            objectName: "documentTitleTooltip"
            popupType: Controls.Popup.Window
            visible: titleHover.hovered && !maximizeButton.hovered && !closeButton.hovered && root.documentTooltip.length > 0
            text: root.documentTooltip
            delay: 650; font: Qt.application.font
            width: Math.min(640, implicitWidth)
            contentItem: Text {
                text: titleTooltip.text
                textFormat: Text.PlainText; wrapMode: Text.WrapAnywhere
                font: Qt.application.font; color: colors.text
            }
            background: Rectangle { color: colors.surface; border.color: colors.border; radius: 4 }
        }
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            property point start
            property point origin
            property string inputToken: ""
            onPressed: function(mouse) {
                inputToken = root.documents.captureFrameInput(root.documentKey, root)
                mouse.accepted = root.documents.activateFrame(root.documentKey, root, inputToken)
                start = mapToItem(root.parent, mouse.x, mouse.y)
                origin = Qt.point(root.frameX, root.frameY)
            }
            onPositionChanged: function(mouse) {
                if (pressed && !root.frameMaximized && root.documents.mode === "free") {
                    const p = mapToItem(root.parent, mouse.x, mouse.y)
                    root.documents.moveResize(root.documentKey, root, inputToken, origin.x + p.x - start.x,
                        origin.y + p.y - start.y, root.frameWidth, root.frameHeight)
                }
            }
            onDoubleClicked: root.documents.toggleMaximize(root.documentKey, root, inputToken)
            onCanceled: inputToken = ""
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 8
            Text { text: root.documentTitle; color: colors.text; font: Qt.application.font; elide: Text.ElideRight; Layout.fillWidth: true }
            SurfaceButton {
                id: maximizeButton
                objectName: "documentMaximize"
                property string inputToken: ""
                text: root.frameMaximized ? "Restore" : "Maximize"
                Accessible.name: text
                implicitWidth: 32
                leftPadding: 8; rightPadding: 8
                visible: root.documents.mode === "free"
                contentItem: Item {
                    implicitWidth: 16; implicitHeight: 16
                    Item {
                        id: maximizeIcon
                        anchors.centerIn: parent
                        width: 16; height: 16
                        readonly property color ink: maximizeButton.enabled ? colors.text : colors.disabledText
                        Item {
                            visible: root.frameMaximized
                            Rectangle { x: 5; y: 2; width: 9; height: 1; color: maximizeIcon.ink }
                            Rectangle { x: 13; y: 2; width: 1; height: 9; color: maximizeIcon.ink }
                            Rectangle { x: 11; y: 10; width: 3; height: 1; color: maximizeIcon.ink }
                        }
                        Rectangle {
                            x: 2; y: root.frameMaximized ? 5 : 2
                            width: root.frameMaximized ? 9 : 12
                            height: root.frameMaximized ? 9 : 12
                            color: "transparent"
                            border.width: 1; border.color: maximizeIcon.ink
                        }
                    }
                }
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: text
                Controls.ToolTip.delay: 650
                onPressed: inputToken = root.documents.captureFrameInput(root.documentKey, root)
                onClicked: root.documents.toggleMaximize(root.documentKey, root, inputToken)
                onCanceled: inputToken = ""
            }
            SurfaceButton {
                id: closeButton
                objectName: "documentClose"
                property string inputToken: ""
                text: "Close"
                Accessible.name: text
                implicitWidth: 32
                leftPadding: 8; rightPadding: 8
                contentItem: Item {
                    implicitWidth: 16; implicitHeight: 16
                    Rectangle {
                        anchors.centerIn: parent
                        width: 14; height: 1
                        rotation: 45; antialiasing: true
                        color: closeButton.enabled ? colors.text : colors.disabledText
                    }
                    Rectangle {
                        anchors.centerIn: parent
                        width: 14; height: 1
                        rotation: -45; antialiasing: true
                        color: closeButton.enabled ? colors.text : colors.disabledText
                    }
                }
                Controls.ToolTip.visible: hovered
                Controls.ToolTip.text: text
                Controls.ToolTip.delay: 650
                onPressed: inputToken = root.documents.captureFrameInput(root.documentKey, root)
                onClicked: root.documents.closeDocument(root.documentKey, root, inputToken)
                onCanceled: inputToken = ""
            }
        }
    }
    Item {
        id: content
        objectName: "documentContent"
        anchors { left: parent.left; right: parent.right; top: title.bottom; bottom: parent.bottom; margins: 1 }
        clip: true
    }
    Rectangle {
        objectName: "documentResizeHandle"
        anchors { right: parent.right; bottom: parent.bottom }
        width: 14; height: 14
        color: colors.border
        visible: root.documents.mode === "free" && !root.frameMaximized
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.SizeFDiagCursor
            property point start
            property size extent
            property string inputToken: ""
            onPressed: function(mouse) {
                inputToken = root.documents.captureFrameInput(root.documentKey, root)
                mouse.accepted = root.documents.activateFrame(root.documentKey, root, inputToken)
                start = mapToItem(root.parent, mouse.x, mouse.y)
                extent = Qt.size(root.frameWidth, root.frameHeight)
            }
            onPositionChanged: function(mouse) {
                if (pressed) {
                    const p = mapToItem(root.parent, mouse.x, mouse.y)
                    root.documents.moveResize(root.documentKey, root, inputToken, root.frameX, root.frameY,
                        extent.width + p.x - start.x, extent.height + p.y - start.y)
                }
            }
            onCanceled: inputToken = ""
        }
    }
}

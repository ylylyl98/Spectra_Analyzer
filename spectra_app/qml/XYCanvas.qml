import QtQuick

FocusScope {
    id: root
    objectName: "XYCanvasRoot"
    required property var bridge
    property int attachmentGeneration: -1
    property string pointerOwner: ""
    property string keyboardOwner: ""
    property bool gestureActive: false
    property int lastModifiers: Qt.NoModifier
    implicitWidth: 480
    implicitHeight: 320
    clip: true
    focus: true
    Accessible.name: "XY comparison plot"

    function cancelPointer() {
        if (!gestureActive) return
        gestureActive = false
        pointerOwner = ""
        bridge.cancelGesture()
    }
    function pressPointer(mouse, doubleClick) {
        lastModifiers = mouse.modifiers
        if (!gestureActive) {
            // A second held button cannot start a new owner after cancellation.
            if (mouse.buttons !== mouse.button) return
            pointerOwner = bridge.ownerToken
            gestureActive = true
        }
        forceActiveFocus(Qt.MouseFocusReason)
        keyboardOwner = pointerOwner
        if (!bridge.pointerEvent("press", mouse.x, mouse.y, mouse.button,
                                 mouse.buttons, mouse.modifiers, doubleClick, pointerOwner)) {
            gestureActive = false
            pointerOwner = ""
        }
    }
    onWidthChanged: bridge.setSceneViewport(width, height, attachmentGeneration)
    onHeightChanged: bridge.setSceneViewport(width, height, attachmentGeneration)
    Component.onCompleted: bridge.setSceneViewport(width, height, attachmentGeneration)
    onVisibleChanged: if (!visible) { cancelPointer(); keyboardOwner = "" }
    onActiveFocusChanged: if (activeFocus) keyboardOwner = bridge.ownerToken
    Keys.onPressed: function(event) {
        lastModifiers = event.modifiers
        event.accepted = bridge.keyEvent("press", event.key, event.text,
                                        event.modifiers, keyboardOwner)
    }
    Keys.onReleased: function(event) {
        lastModifiers = event.modifiers
        event.accepted = bridge.keyEvent("release", event.key, event.text,
                                        event.modifiers, keyboardOwner)
    }

    Rectangle { anchors.fill: parent; color: "#e8ecf1" }
    Image {
        objectName: "XYCanvasImage"
        x: root.bridge.contentX; y: root.bridge.contentY
        width: root.bridge.contentWidth; height: root.bridge.contentHeight
        source: root.bridge.imageUrl
        cache: false
        asynchronous: false
        smooth: false
    }
    MouseArea {
        id: input
        objectName: "XYCanvasPointer"
        anchors.fill: parent
        enabled: root.bridge.enabled
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        preventStealing: true
        cursorShape: root.bridge.cursorShape
        onPressed: function(mouse) { root.pressPointer(mouse, false) }
        onDoubleClicked: function(mouse) { root.pressPointer(mouse, true) }
        onPositionChanged: function(mouse) {
            root.lastModifiers = mouse.modifiers
            if (mouse.buttons && !root.gestureActive) return
            root.bridge.pointerEvent("move", mouse.x, mouse.y, mouse.button, mouse.buttons,
                                     mouse.modifiers, false,
                                     root.gestureActive ? root.pointerOwner : root.bridge.ownerToken)
        }
        onReleased: function(mouse) {
            root.lastModifiers = mouse.modifiers
            if (!root.gestureActive) return
            root.bridge.pointerEvent("release", mouse.x, mouse.y, mouse.button, mouse.buttons,
                                     mouse.modifiers, false, root.pointerOwner)
            if (!mouse.buttons) { root.gestureActive = false; root.pointerOwner = "" }
        }
        onCanceled: root.cancelPointer()
        onEntered: root.bridge.pointerEvent("enter", mouseX, mouseY, Qt.NoButton,
                                            pressedButtons, root.lastModifiers, false,
                                            root.gestureActive ? root.pointerOwner : root.bridge.ownerToken)
        onExited: root.bridge.pointerEvent("leave", mouseX, mouseY, Qt.NoButton,
                                           pressedButtons, root.lastModifiers, false,
                                           root.gestureActive ? root.pointerOwner : root.bridge.ownerToken)
        onWheel: function(wheel) { wheel.accepted = false }
    }
    Rectangle {
        objectName: "XYCanvasRubberband"
        visible: root.bridge.rubberband.visible === true
        x: root.bridge.contentX + (root.bridge.rubberband.x || 0)
        y: root.bridge.contentY + (root.bridge.rubberband.y || 0)
        width: root.bridge.rubberband.width || 0
        height: root.bridge.rubberband.height || 0
        color: "transparent"
        border.color: "#ffffff"; border.width: 1
        Rectangle {
            anchors.fill: parent; anchors.margins: 1
            color: "transparent"; border.color: "#202020"; border.width: 1
        }
    }
    Connections {
        target: root.bridge
        function onStateChanged() {
            if (!root.bridge.enabled || root.keyboardOwner !== root.bridge.ownerToken)
                root.keyboardOwner = ""
            if (!root.bridge.enabled || (root.gestureActive && root.pointerOwner !== root.bridge.ownerToken))
                root.cancelPointer()
        }
    }
}


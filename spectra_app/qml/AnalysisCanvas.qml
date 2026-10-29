import QtQuick
import QtQuick.Controls.Basic as Controls

FocusScope {
    id: root
    objectName: "AnalysisCanvas"
    required property QtObject bridge
    required property int attachmentGeneration
    readonly property bool attachmentCurrent: bridge !== null && attachmentGeneration === bridge.sceneGeneration
    enabled: attachmentCurrent
    property string pointerOwner: ""
    property string keyboardOwner: ""
    property bool gestureActive: false
    property int lastModifiers: Qt.NoModifier
    property var peakRequest: ({})
    property string peakChoice: ""
    implicitWidth: 480
    implicitHeight: 320
    Accessible.name: "Analysis plot"

    function cancelPointer() {
        if (!gestureActive) return
        gestureActive = false
        pointerOwner = ""
        if (attachmentCurrent) bridge.cancelGesture()
    }

    function reportViewport() {
        if (attachmentCurrent) bridge.reportViewport(attachmentGeneration, viewport.width, viewport.height)
    }

    function pressPointer(mouse, doubleClick) {
        if (!attachmentCurrent) return
        lastModifiers = mouse.modifiers
        // Additional held buttons stay with the original gesture. A canceled
        // grab cannot recapture a replacement owner.
        if (!gestureActive) {
            if (mouse.buttons !== mouse.button) return
            pointerOwner = bridge.ownerToken
            gestureActive = true
        }
        forceActiveFocus(Qt.MouseFocusReason)
        keyboardOwner = pointerOwner
        let anchor = input.mapToItem(root, mouse.x, mouse.y)
        if (!bridge.pointerEvent("press", mouse.x, mouse.y, mouse.button, mouse.buttons,
                                 mouse.modifiers, doubleClick, pointerOwner, anchor.x, anchor.y)) {
            gestureActive = false
            pointerOwner = ""
        }
    }

    onVisibleChanged: if (!visible) cancelPointer()
    onActiveFocusChanged: if (activeFocus && attachmentCurrent) keyboardOwner = bridge.ownerToken
    Keys.onPressed: function(event) {
        if (!attachmentCurrent) { event.accepted = false; return }
        if (peakMenu.visible) {
            event.accepted = event.key === Qt.Key_Escape
            if (event.accepted) peakMenu.close()
            return
        }
        lastModifiers = event.modifiers
        event.accepted = bridge.keyEvent("press", event.key, event.text,
                                        event.modifiers, keyboardOwner)
    }
    Keys.onReleased: function(event) {
        if (!attachmentCurrent) { event.accepted = false; return }
        if (peakMenu.visible) { event.accepted = false; return }
        lastModifiers = event.modifiers
        event.accepted = bridge.keyEvent("release", event.key, event.text,
                                        event.modifiers, keyboardOwner)
    }

    Flickable {
        id: viewport
        objectName: "analysisCanvasViewport"
        anchors.fill: parent
        clip: true
        contentWidth: (root.bridge ? root.bridge.contentWidth : 1)
        contentHeight: (root.bridge ? root.bridge.contentHeight : 1)
        boundsBehavior: Flickable.StopAtBounds
        onWidthChanged: root.reportViewport()
        onHeightChanged: root.reportViewport()
        Component.onCompleted: root.reportViewport()

        Image {
            id: frame
            objectName: "analysisCanvasImage"
            width: (root.bridge ? root.bridge.contentWidth : 1)
            height: (root.bridge ? root.bridge.contentHeight : 1)
            source: root.attachmentCurrent ? root.bridge.imageUrl : ""
            cache: false
            asynchronous: false
            smooth: false

            MouseArea {
                id: input
                objectName: "analysisCanvasInput"
                anchors.fill: parent
                enabled: root.attachmentCurrent && root.bridge.enabled
                acceptedButtons: Qt.AllButtons
                hoverEnabled: true
                preventStealing: true
                cursorShape: root.bridge ? root.bridge.cursorShape : Qt.ArrowCursor

                onPressed: function(mouse) { root.pressPointer(mouse, false) }
                // Accepting this signal suppresses MouseArea's ordinary press
                // for the second click, matching a single Matplotlib dblclick.
                onDoubleClicked: function(mouse) { root.pressPointer(mouse, true) }
                onPositionChanged: function(mouse) {
                    if (!root.attachmentCurrent) return
                    root.lastModifiers = mouse.modifiers
                    if (mouse.buttons && !root.gestureActive) return
                    let anchor = input.mapToItem(root, mouse.x, mouse.y)
                    root.bridge.pointerEvent("move", mouse.x, mouse.y,
                                             mouse.button, mouse.buttons,
                                             mouse.modifiers, false,
                                             root.gestureActive ? root.pointerOwner : root.bridge.ownerToken,
                                             anchor.x, anchor.y)
                }
                onReleased: function(mouse) {
                    if (!root.attachmentCurrent) { root.cancelPointer(); return }
                    root.lastModifiers = mouse.modifiers
                    if (!root.gestureActive) return
                    let anchor = input.mapToItem(root, mouse.x, mouse.y)
                    root.bridge.pointerEvent("release", mouse.x, mouse.y,
                                             mouse.button, mouse.buttons,
                                             mouse.modifiers, false, root.pointerOwner, anchor.x, anchor.y)
                    if (!mouse.buttons) {
                        root.gestureActive = false
                        root.pointerOwner = ""
                    }
                }
                onCanceled: root.cancelPointer()
                onEntered: if (root.attachmentCurrent) root.bridge.pointerEvent("enter", mouseX, mouseY, Qt.NoButton,
                                                    pressedButtons, root.lastModifiers, false,
                                                    root.gestureActive ? root.pointerOwner : root.bridge.ownerToken)
                onExited: if (root.attachmentCurrent) root.bridge.pointerEvent("leave", mouseX, mouseY, Qt.NoButton,
                                                   pressedButtons, root.lastModifiers, false,
                                                   root.gestureActive ? root.pointerOwner : root.bridge.ownerToken)
                onWheel: function(wheel) {
                    if (!root.attachmentCurrent) { wheel.accepted = false; return }
                    root.bridge.scrollEvent(wheel.x, wheel.y,
                                            (wheel.angleDelta.y || wheel.angleDelta.x) / 120,
                                            wheel.modifiers, root.bridge.ownerToken)
                    // The callback preserves Matplotlib semantics; Flickable
                    // handles the ordinary viewport scrolling independently.
                    wheel.accepted = false
                }
            }

            Rectangle {
                objectName: "analysisCanvasRubberband"
                visible: root.bridge !== null && root.bridge.rubberband.visible === true
                x: root.bridge ? (root.bridge.rubberband.x || 0) : 0
                y: root.bridge ? (root.bridge.rubberband.y || 0) : 0
                width: root.bridge ? (root.bridge.rubberband.width || 0) : 0
                height: root.bridge ? (root.bridge.rubberband.height || 0) : 0
                color: "transparent"
                border.color: "#ffffff"
                border.width: 1
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1
                    color: "transparent"
                    border.color: "#202020"
                    border.width: 1
                }
            }
        }

        Controls.ScrollBar.horizontal: Controls.ScrollBar {
            objectName: "analysisCanvasHorizontalScrollBar"
            policy: Controls.ScrollBar.AsNeeded
            interactive: true
            z: 10
        }
        Controls.ScrollBar.vertical: Controls.ScrollBar {
            objectName: "analysisCanvasVerticalScrollBar"
            policy: Controls.ScrollBar.AsNeeded
            interactive: true
            z: 10
        }
    }

    Controls.Menu {
        id: peakMenu
        objectName: "analysisPeakMenu"
        popupType: Controls.Popup.Item
        width: Math.min(280, Math.max(1, root.width - 12))
        margins: 6
        padding: 4
        onClosed: {
            let request = root.peakRequest
            let choice = root.peakChoice
            root.peakRequest = ({})
            root.peakChoice = ""
            if (!request.requestId || !root.attachmentCurrent) return
            if (choice) {
                Qt.callLater(function() {
                    if (root.attachmentCurrent) root.bridge.choosePeakMenu(request.requestId, choice, request.owner)
                })
            } else {
                root.bridge.dismissPeakMenu(request.requestId, request.owner)
                root.bridge.restorePeakMenuFocus(request.owner)
            }
        }
        Controls.MenuItem {
            objectName: "analysisPeakSetLabel"
            visible: root.peakRequest.hasPeak === true
            height: visible ? implicitHeight : 0
            text: root.peakRequest.labelText || "Set Label…"
            onTriggered: { root.peakChoice = "set_label"; peakMenu.close() }
        }
        Controls.MenuSeparator {
            objectName: "analysisPeakSeparator"
            visible: root.peakRequest.hasPeak === true
            height: visible ? implicitHeight : 0
        }
        Controls.MenuItem {
            objectName: "analysisPeakDelete"
            visible: root.peakRequest.hasPeak === true
            height: visible ? implicitHeight : 0
            text: "Delete Peak"
            onTriggered: { root.peakChoice = "delete"; peakMenu.close() }
        }
        Controls.MenuSeparator {
            visible: root.peakRequest.hasPeak === true
            height: visible ? implicitHeight : 0
        }
        Controls.MenuItem {
            objectName: "analysisCopySvg"
            text: "Copy SVG"
            enabled: root.bridge.formatCommands.copy_svg === true
            onTriggered: { root.peakChoice = "copy_svg"; peakMenu.close() }
        }
        Controls.MenuItem {
            objectName: "analysisCopyPng"
            text: "Copy PNG"
            enabled: root.bridge.formatCommands.copy_png === true
            onTriggered: { root.peakChoice = "copy_png"; peakMenu.close() }
        }
        Controls.MenuSeparator { }
        Controls.MenuItem {
            objectName: "analysisFormatCopy"
            text: "Copy Format"
            enabled: root.bridge.formatCommands.settings_copy === true
            onTriggered: { root.peakChoice = "settings_copy"; peakMenu.close() }
        }
        Controls.MenuItem {
            objectName: "analysisFormatPaste"
            text: "Paste Format"
            enabled: root.bridge.formatCommands.settings_paste === true
            onTriggered: { root.peakChoice = "settings_paste"; peakMenu.close() }
        }
        Controls.MenuItem {
            objectName: "analysisFormat_settings_paste_to"
            text: "Paste Format to Graphs…"
            enabled: root.bridge.formatCommands.settings_paste_to === true
            onTriggered: { root.peakChoice = "settings_paste_to"; peakMenu.close() }
        }
        Controls.MenuItem {
            objectName: "analysisFormat_settings_paste_options"
            text: "Paste Format Options…"
            enabled: root.bridge.formatCommands.settings_paste_options === true
            onTriggered: { root.peakChoice = "settings_paste_options"; peakMenu.close() }
        }
        Controls.MenuItem {
            objectName: "analysisFormat_settings_adjust"
            text: "Adjust Last Paste…"
            enabled: root.bridge.formatCommands.settings_adjust === true
            onTriggered: { root.peakChoice = "settings_adjust"; peakMenu.close() }
        }
        Controls.MenuItem {
            objectName: "analysisFormat_settings_undo_paste"
            text: "Undo Last Paste"
            enabled: root.bridge.formatCommands.settings_undo_paste === true
            onTriggered: { root.peakChoice = "settings_undo_paste"; peakMenu.close() }
        }
    }

    Connections {
        target: root.bridge
        function onPeakMenuChanged() {
            if (!root.attachmentCurrent) {
                root.peakRequest = ({})
                if (peakMenu.visible) peakMenu.close()
                return
            }
            let request = root.bridge.peakMenu
            if (!request.requestId) {
                if (peakMenu.visible) peakMenu.close()
                return
            }
            Qt.callLater(function() {
                if (!root.attachmentCurrent) return
                let live = root.bridge.peakMenu
                if (live.requestId !== request.requestId || root.gestureActive
                        || !root.bridge.enabled || live.owner !== root.bridge.ownerToken) return
                root.peakRequest = request
                root.peakChoice = ""
                // Popup.Item uses the window overlay; keep its outer bounds
                // within this canvas rather than the neighboring Properties.
                const margin = Math.max(0, peakMenu.margins)
                const height = Math.max(peakMenu.implicitHeight,
                    peakMenu.contentItem.implicitHeight + peakMenu.topPadding + peakMenu.bottomPadding)
                const x = Math.max(0, Math.min(request.anchorX, root.width - peakMenu.width - margin))
                const y = Math.max(0, Math.min(request.anchorY, root.height - height - margin))
                peakMenu.popup(root, x, y)
            })
        }
        function onPeakMenuFocusRequested(owner) {
            if (root.attachmentCurrent && root.bridge.enabled && owner === root.bridge.ownerToken) {
                root.keyboardOwner = owner
                root.forceActiveFocus(Qt.PopupFocusReason)
            }
        }
        function onStateChanged() {
            if (!root.attachmentCurrent || !root.bridge.enabled || root.keyboardOwner !== root.bridge.ownerToken) {
                root.keyboardOwner = ""
                root.focus = false
            }
            if (!root.attachmentCurrent || !root.bridge.enabled || (root.gestureActive && root.pointerOwner !== root.bridge.ownerToken))
                root.cancelPointer()
        }
    }
}

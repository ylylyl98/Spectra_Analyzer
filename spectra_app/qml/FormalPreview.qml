import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Window

Rectangle {
    id: root
    objectName: "formalPreview"
    required property QtObject bridge
    required property int attachmentGeneration
    color: bridge.readOnly ? "white" : colors.disabled
    implicitWidth: 640
    implicitHeight: 480
    property real fitScale: 1
    readonly property real displayScale: bridge.readOnly ? fitScale * bridge.relativeZoom
        : bridge.zoom === "Fit" ? fitScale : parseInt(bridge.zoom) / 100
    AppColors { id: colors }

    function refreshFit() {
        const margin = bridge.readOnly ? 0 : 12
        if (bridge.viewportVisible && visible && viewport.width > 24 && viewport.height > 24)
            fitScale = Math.min((viewport.width - margin) / bridge.pageWidth,
                               (viewport.height - margin) / bridge.pageHeight)
    }
    function publishGeometry() {
        bridge.sceneDisplayGeometry(attachmentGeneration, paper.width, paper.height, displayScale)
    }
    Component.onCompleted: {
        const scrollX = bridge.scrollX, scrollY = bridge.scrollY
        refreshFit(); publishGeometry()
        Qt.callLater(function() { viewport.contentX = scrollX; viewport.contentY = scrollY })
    }
    onWidthChanged: refreshFit()
    onHeightChanged: refreshFit()
    onVisibleChanged: refreshFit()
    Connections {
        target: root.bridge
        function onStateChanged() { root.refreshFit() }
    }

    Flickable {
        id: viewport
        objectName: "formalViewport"
        anchors.fill: parent
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: !root.bridge.readOnly
        contentWidth: root.bridge.readOnly ? width : Math.max(width, paper.width + 12)
        contentHeight: root.bridge.readOnly ? height : Math.max(height, paper.height + 12)
        activeFocusOnTab: true
        onContentXChanged: root.bridge.saveScroll(root.attachmentGeneration, contentX, contentY)
        onContentYChanged: root.bridge.saveScroll(root.attachmentGeneration, contentX, contentY)
        Accessible.role: Accessible.Pane
        Accessible.name: root.bridge.readOnly ? "Read-only exported page; wheel to zoom, drag to pan, double-click to fit"
            : "Formal page canvas; select labels or arrows to move them"
        Controls.ScrollBar.horizontal: Controls.ScrollBar { policy: root.bridge.readOnly ? Controls.ScrollBar.AlwaysOff : Controls.ScrollBar.AsNeeded }
        Controls.ScrollBar.vertical: Controls.ScrollBar { policy: root.bridge.readOnly ? Controls.ScrollBar.AlwaysOff : Controls.ScrollBar.AsNeeded }
        Keys.onPressed: function(event) {
            if (root.bridge.readOnly) return
            if (root.bridge.sceneKey(root.attachmentGeneration, event.key, event.modifiers)) {
                event.accepted = true
                return
            }
            if (event.key === Qt.Key_Left) contentX = Math.max(0, contentX - 40)
            else if (event.key === Qt.Key_Right) contentX = Math.min(Math.max(0, contentWidth - width), contentX + 40)
            else if (event.key === Qt.Key_Up) contentY = Math.max(0, contentY - 40)
            else if (event.key === Qt.Key_Down) contentY = Math.min(Math.max(0, contentHeight - height), contentY + 40)
            else return
            event.accepted = true
        }

        Image {
            id: paper
            objectName: "formalPaper"
            x: (viewport.contentWidth - width) / 2 + (root.bridge.readOnly ? root.bridge.panX : 0)
            y: (viewport.contentHeight - height) / 2 + (root.bridge.readOnly ? root.bridge.panY : 0)
            width: root.bridge.pageWidth * root.displayScale
            height: root.bridge.pageHeight * root.displayScale
            source: root.bridge.imageUrl
            // Qt multiplies image-provider requests by the window DPR itself.
            // Multiplying here as well rasterizes at DPR squared (Qt 6.11).
            // Read-only free zoom can reach 12x Fit. Bound that display raster
            // to 2048 physical pixels per edge; paper geometry stays exact.
            // Extreme zoom magnifies the capped raster rather than allocating
            // a full-page image far beyond the visible viewport.
            readonly property real rasterEdgeLimit: Math.max(1, Math.min(
                2 * Math.max(viewport.width, viewport.height), Math.floor(2048 / Screen.devicePixelRatio)))
            readonly property real rasterScale: root.bridge.readOnly
                ? Math.min(1, rasterEdgeLimit / Math.max(1, width, height)) : 1
            sourceSize.width: root.bridge.readOnly
                ? Math.min(Math.floor(rasterEdgeLimit), Math.max(1, Math.ceil(width * rasterScale)))
                : Math.max(1, Math.ceil(width))
            sourceSize.height: root.bridge.readOnly
                ? Math.min(Math.floor(rasterEdgeLimit), Math.max(1, Math.ceil(height * rasterScale)))
                : Math.max(1, Math.ceil(height))
            cache: false
            asynchronous: false
            smooth: true
            clip: true
            onWidthChanged: root.publishGeometry()
            onHeightChanged: root.publishGeometry()
            Accessible.role: Accessible.Graphic
            Accessible.name: "Exact formal export page"

            Canvas {
                id: selection
                objectName: "formalSelectionOverlay"
                anchors.fill: parent
                property var geometry: root.bridge.overlay
                onGeometryChanged: requestPaint()
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    const g = geometry
                    if (!g.visible) return
                    ctx.strokeStyle = "#1265d6"
                    ctx.lineWidth = 1
                    ctx.setLineDash([4, 3])
                    ctx.strokeRect(g.x, g.y, g.width, g.height)
                    ctx.setLineDash([])
                    if (g.anchor) {
                        ctx.beginPath(); ctx.arc(g.pointX, g.pointY, 5, 0, 2 * Math.PI); ctx.stroke()
                    }
                    if (g.arrow) {
                        ctx.beginPath(); ctx.moveTo(g.pointX, g.pointY); ctx.lineTo(g.targetX, g.targetY); ctx.stroke()
                        ctx.beginPath(); ctx.arc(g.targetX, g.targetY, 6, 0, 2 * Math.PI); ctx.stroke()
                    }
                }
            }

            MouseArea {
                id: pointer
                objectName: "formalPaperPointer"
                anchors.fill: parent
                enabled: !root.bridge.readOnly
                acceptedButtons: Qt.LeftButton
                property int generation: -1
                readonly property bool editingGesture: generation >= 0 && generation === root.bridge.gestureGeneration
                preventStealing: editingGesture
                onPressed: function(mouse) {
                    viewport.forceActiveFocus()
                    generation = root.bridge.scenePress(root.attachmentGeneration, mouse.x, mouse.y)
                    mouse.accepted = true
                }
                onPositionChanged: function(mouse) {
                    if (pressed) root.bridge.sceneMove(root.attachmentGeneration, generation, mouse.x, mouse.y)
                }
                onReleased: function(mouse) { root.bridge.sceneRelease(root.attachmentGeneration, generation, mouse.x, mouse.y); generation = -1 }
                onDoubleClicked: function(mouse) { mouse.accepted = root.bridge.sceneDoubleClick(root.attachmentGeneration, mouse.x, mouse.y) }
                onCanceled: { root.bridge.sceneCancel(root.attachmentGeneration); generation = -1 }
            }
        }
    }

    MouseArea {
        objectName: "formalReadonlyPointer"
        anchors.fill: parent
        enabled: root.bridge.readOnly
        acceptedButtons: Qt.LeftButton
        preventStealing: true
        property int generation: -1
        cursorShape: pressed && generation === root.bridge.gestureGeneration ? Qt.ClosedHandCursor : Qt.ArrowCursor
        onPressed: function(mouse) {
            viewport.forceActiveFocus()
            generation = root.bridge.sceneViewPress(root.attachmentGeneration, mouse.x, mouse.y)
            mouse.accepted = true
        }
        onPositionChanged: function(mouse) {
            if (pressed) root.bridge.sceneViewMove(root.attachmentGeneration, generation, mouse.x, mouse.y)
        }
        onReleased: function(mouse) { root.bridge.sceneViewRelease(root.attachmentGeneration, generation, mouse.x, mouse.y) }
        onDoubleClicked: function(mouse) { mouse.accepted = root.bridge.sceneViewFit(root.attachmentGeneration, generation) }
        onCanceled: { root.bridge.sceneCancel(root.attachmentGeneration); generation = -1 }
        onWheel: function(wheel) {
            root.bridge.sceneWheelZoom(root.attachmentGeneration, wheel.angleDelta.y)
            wheel.accepted = true
        }
    }
}

import QtQuick

Rectangle {
    id: root
    required property QtObject preview
    required property int attachmentGeneration
    readonly property bool attachmentCurrent: preview !== null && attachmentGeneration === preview.sceneGeneration
    enabled: attachmentCurrent
    function reportViewport() {
        if (attachmentCurrent) preview.reportViewport(attachmentGeneration, width, height)
    }
    onWidthChanged: reportViewport()
    onHeightChanged: reportViewport()
    Component.onCompleted: reportViewport()
    readonly property bool loadedReady: linecutImage.status === Image.Ready
    color: "white"
    implicitWidth: 700
    implicitHeight: 180

    Image {
        id: linecutImage
        objectName: "analysisLinecutImage"
        anchors.fill: parent
        source: root.attachmentCurrent ? root.preview.imageUrl : ""
        // Pixels already match viewport × actual DPR. Never ask the provider
        // for another resolution or stretch the scientific Figure's aspect.
        fillMode: Image.PreserveAspectFit
        cache: false
        asynchronous: false
        smooth: true
        Accessible.role: Accessible.Graphic
        Accessible.name: "Auxiliary Gate linecut — excluded from heatmap export"
    }
}

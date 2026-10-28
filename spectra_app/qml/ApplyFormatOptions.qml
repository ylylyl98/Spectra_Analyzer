import QtQuick
import QtQuick.Controls.Basic as Controls
import QtQuick.Layouts

Controls.ScrollView {
    id: scroll
    required property var bridge
    objectName: "formatOptionsScroll"
    clip: true
    contentWidth: availableWidth
    ColumnLayout {
        width: scroll.availableWidth
        spacing: 8
        Flow {
            Layout.fillWidth: true
            spacing: 6
            SurfaceButton { font: Qt.application.font; objectName: "onlyFormat"; text: "Only format"; onClicked: bridge.quickOptions(false) }
            SurfaceButton { font: Qt.application.font; objectName: "formatRanges"; text: "Format + ranges"; onClicked: bridge.quickOptions(true) }
        }
        Controls.Label { text: "Format groups"; font.bold: true; color: "#263447" }
        Repeater {
            model: bridge.basicOptions
            SurfaceCheckBox { font: Qt.application.font;
                required property var modelData
                objectName: "formatOption_" + modelData.key
                Layout.fillWidth: true
                text: modelData.label
                enabled: modelData.available
                checked: modelData.checked
                onToggled: modelData.setChecked(checked)
                Accessible.name: modelData.label
            }
        }
        Controls.Label { text: "Optional fields"; font.bold: true; color: "#263447"; Layout.topMargin: 4 }
        Repeater {
            model: bridge.optionalOptions
            SurfaceCheckBox { font: Qt.application.font;
                required property var modelData
                objectName: "formatOption_" + modelData.key
                Layout.fillWidth: true
                text: modelData.label
                enabled: modelData.available
                checked: modelData.checked
                onToggled: modelData.setChecked(checked)
                Accessible.name: modelData.label
            }
        }
        SurfaceCheckBox { font: Qt.application.font;
            objectName: "formatLockRanges"
            Layout.fillWidth: true
            text: "Lock copied range endpoints"
            checked: bridge.lockRanges
            onToggled: bridge.setLockRanges(checked)
        }
        Controls.Label {
            visible: bridge.annotationPositions
            Layout.fillWidth: true
            text: "Pair each source annotation explicitly. Skip keeps the target in place. Only its text / tail position transfers; target text, IDs and arrow heads stay intact."
            wrapMode: Text.Wrap
            color: "#5f6e80"
            font: Qt.application.font
        }
        Repeater {
            model: bridge.annotationPositions ? bridge.annotationPairs : []
            ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                Controls.Label { text: modelData.targetLabel; wrapMode: Text.Wrap; Layout.fillWidth: true; font.bold: true }
                Controls.Label { text: modelData.label + " →"; wrapMode: Text.Wrap; Layout.fillWidth: true; font: Qt.application.font }
                Controls.ComboBox {
                    objectName: "annotationPair_" + modelData.datasetId + "_" + modelData.sourceId
                    Layout.fillWidth: true
                    model: modelData.choices
                    currentIndex: modelData.currentIndex
                    onActivated: modelData.setCurrentIndex(currentIndex)
                    Accessible.name: modelData.label + " target annotation"
                }
            }
        }
    }
}


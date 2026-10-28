import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    property font controlFont: Qt.font({ family: "Segoe UI", pointSize: 10 })
    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
    spacing: 6
    Label {
        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
        visible: text.length > 0
        text: root.bridge.error || root.bridge.message
        color: root.bridge.error ? colors.error : colors.secondary
        font: root.controlFont
        wrapMode: Text.Wrap
        Accessible.name: "Comparison inspector feedback"
    }
    SurfaceButton {
        objectName: "xyRevertEdits"
        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
        text: "Revert pending edits"
        font: root.controlFont
        enabled: root.bridge.hasDraft
        Accessible.name: "Revert pending graph and curve edits"
        onClicked: root.bridge.revert()
    }
    AppColors { id: colors }
}

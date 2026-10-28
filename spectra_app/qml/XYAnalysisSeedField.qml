import QtQuick
import QtQuick.Controls.Basic as Controls

Controls.TextField {
    id: root
    required property var bridge
    required property string seedId
    required property string field
    property string value: ""
    property string label: field
    property string capturedSeed: ""
    readonly property string error: String(bridge.errors[seedId + "." + field] || "")
    objectName: "xy-seed-" + field
    width: 108; height: 36; font: Qt.application.font
    selectByMouse: true; inputMethodHints: Qt.ImhFormattedNumbersOnly
    placeholderText: field === "position" ? "Required" : field === "centerMin" || field === "centerMax" ? "Unbounded" : "Estimate"
    Accessible.name: label + ", seed " + seedId
    Accessible.description: error || placeholderText
    leftPadding: 7; rightPadding: 7
    AppColors { id: colors }
    function refresh() { if (!activeFocus) text = value }
    onActiveFocusChanged: { if (activeFocus) capturedSeed = seedId; else refresh() }
    onTextEdited: if (capturedSeed) bridge.edit(capturedSeed, field, text)
    onValueChanged: refresh()
    Component.onCompleted: refresh()
    background: Rectangle { radius: 4; color: root.error ? colors.errorSurface : colors.surface; border.width: root.activeFocus ? 2 : 1; border.color: root.error ? colors.error : root.activeFocus ? colors.accent : colors.border }
    Controls.ToolTip.visible: hovered && !!error
    Controls.ToolTip.text: error
}

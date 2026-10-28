import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var bridge
    property string label
    property string actionOwner: ""
    property string field
    property var choices: []
    property string value: String(bridge.values[field])
    property font controlFont: Qt.application.font
    readonly property var selectedChoice: box.currentIndex >= 0 ? root.choices[box.currentIndex] : ({})
    function focusChoice() { box.forceActiveFocus() }
    signal chosen(string key)
    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
    spacing: 4
    Label { text: root.label; font: root.controlFont; color: colors.text; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width }
    ComboBox {
        id: box
        objectName: "xyChoice_" + root.field
        Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
        implicitHeight: Math.max(32, contentItem.implicitHeight + 12)
        leftPadding: 8; rightPadding: 28; topPadding: 6; bottomPadding: 6
        font: root.controlFont
        hoverEnabled: true
        model: root.choices
        textRole: "label"
        valueRole: "key"
        Accessible.name: root.label
        currentIndex: {
            for (let i=0; i < root.choices.length; ++i) if (root.choices[i].key === root.value) return i
            return -1
        }
        onActiveFocusChanged: if (activeFocus) root.actionOwner = root.bridge.inputOwner
        PointHandler { onActiveChanged: if (active) root.actionOwner = root.bridge.inputOwner }
        onActivated: if (root.bridge.authorizeOwner(root.actionOwner)) root.chosen(currentValue)
        contentItem: RowLayout {
            spacing: 6
            Image {
                visible: !!root.selectedChoice.icon
                source: root.selectedChoice.icon || ""
                sourceSize.width: 24
                sourceSize.height: 24
                Layout.preferredWidth: visible ? 24 : 0
                Layout.preferredHeight: 24
                fillMode: Image.PreserveAspectFit
            }
            Row {
                visible: (root.selectedChoice.swatches || []).length > 0
                Repeater {
                    model: root.selectedChoice.swatches || []
                    Rectangle { required property string modelData; width:8; height:16; color:modelData }
                }
            }
            Text {
                text: box.displayText
                font: box.font
                color: box.enabled ? colors.text : colors.disabledText
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width
            }
        }
        indicator: Text { x: box.width - width - 8; y: (box.height - height) / 2; text: "⌄"; textFormat: Text.PlainText; font: root.controlFont; color: colors.secondary }
        background: Rectangle { radius: 4; color: !box.enabled ? colors.disabled : box.down ? colors.pressed : box.hovered ? colors.hover : colors.surface; border.width: box.visualFocus ? 2 : 1; border.color: box.visualFocus ? colors.accent : root.bridge.errorField === root.field && !!root.bridge.error ? colors.error : colors.border }
        delegate: ItemDelegate {
            required property var modelData
            required property int index
            width: box.width
            highlighted: box.highlightedIndex === index
            implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
            font: root.controlFont
            contentItem: RowLayout {
                spacing:6
                Image { visible:!!modelData.icon; source:modelData.icon || ""; sourceSize.width:24; sourceSize.height:24; Layout.preferredWidth:visible ? 24 : 0; Layout.preferredHeight:24; fillMode:Image.PreserveAspectFit }
                Row {
                    Repeater {
                        model: modelData.swatches || []
                        Rectangle { required property string modelData; width:8; height:16; color:modelData }
                    }
                }
                Label { text:modelData.label; font:root.controlFont; color:colors.text; Layout.fillWidth:true; Layout.minimumWidth: 0; Layout.maximumWidth: parent.width; elide:Text.ElideRight }
            }
            background: Rectangle { color: parent.highlighted ? colors.selected : colors.surface }
        }
    }
    AppColors { id: colors }
}
